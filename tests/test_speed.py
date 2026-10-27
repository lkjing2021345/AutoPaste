# -*- coding: utf-8 -*-
"""03.3 批量提速：事件序列等价、flush 分块、服务端 delay 与回退路径。"""

import unittest
from unittest import mock

import autopaste
from support import EventLog, FakeX11, FakeXtst, redirect_std


class SpeedBase(unittest.TestCase):
    def setUp(self):
        self.log = EventLog()
        self.x11 = FakeX11(lo=8, hi=64, per=2, log=self.log)
        self.xtst = FakeXtst(log=self.log)
        for keysym, kc, high in (
                (ord("a"), 38, ord("A")),
                (ord("b"), 56, ord("B")),
                (ord("c"), 54, ord("C")),
                (autopaste.KEYSYM_SHIFT, 50, None),
        ):
            self.x11.add_key(keysym, kc, level0=keysym, level1=high)
        self.x11.add_key(ord("A"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(ord("B"), 56, level0=ord("b"), level1=ord("B"))
        self.x11.add_key(ord("C"), 54, level0=ord("c"), level1=ord("C"))
        self.x11.add_named("Shift_L", autopaste.KEYSYM_SHIFT)
        self.x11.add_key(0xFF0D, 36)
        self.x11.add_named("Return", 0xFF0D)
        self.x11.add_key(0xFF09, 23)
        self.x11.add_named("Tab", 0xFF09)
        self._patches = [
            mock.patch.object(autopaste, "x11", self.x11),
            mock.patch.object(autopaste, "xtst", self.xtst),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.backend = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
        self.addCleanup(self.backend.close)

    def reset_log(self):
        self.log.entries.clear()
        self.xtst.delays.clear()
        self.x11.flush_count = 0


class EquivalenceTests(SpeedBase):
    def test_event_sequence_identical_to_per_char(self):
        text = "aAbB\n\t"
        self.backend.type_text(text)
        batch = list(self.xtst.events)
        self.reset_log()
        self.backend.batch_enabled = False
        self.backend.type_text(text)
        per_char = list(self.xtst.events)
        self.assertEqual(batch, per_char)

    def test_batch_flushes_once_for_small_input(self):
        self.backend.type_text("abc")
        self.assertEqual(self.x11.flush_count, 1)

    def test_zero_delay_flushes_every_256_chars(self):
        self.backend.type_text("a" * 1000)
        # 256/512/768 各一次 + 收尾一次
        self.assertEqual(self.x11.flush_count, 4)

    def test_chunk_size_bounded_by_250ms_budget(self):
        self.backend.delay = 0.008
        self.assertEqual(autopaste.QUEUE_BUDGET_MS // 8, 31)
        self.backend.type_text("a" * 100)
        # 31/62/93 各一次 + 收尾一次
        self.assertEqual(self.x11.flush_count, 4)


class ServerDelayTests(SpeedBase):
    def test_delay_is_put_on_press_event(self):
        self.backend.delay = 0.01
        self.backend.type_text("a")
        self.assertEqual(self.xtst.delays, [10, 0])

    def test_shifted_char_keeps_order_and_delay(self):
        self.backend.delay = 0.01
        self.backend.type_text("A")
        self.assertEqual(self.xtst.events,
                         [(50, True), (38, True), (38, False), (50, False)])
        self.assertEqual(self.xtst.delays, [0, 10, 0, 0])

    def test_newline_and_tab_use_server_delay(self):
        self.backend.delay = 0.01
        self.backend.type_text("\n\t")
        self.assertEqual(self.xtst.events,
                         [(36, True), (36, False), (23, True), (23, False)])
        self.assertEqual(self.xtst.delays, [10, 0, 10, 0])

    def test_zero_delay_sends_no_delay(self):
        self.backend.type_text("ab")
        self.assertEqual(self.xtst.delays, [0, 0, 0, 0])

    def test_sub_millisecond_delay_has_1ms_floor(self):
        self.backend.delay = 0.0004
        self.backend.type_text("a")
        self.assertEqual(self.xtst.delays, [1, 0])

    def test_delay_zero_and_tiny_have_identical_event_sequence(self):
        self.backend.type_text("ab")
        zero = list(self.xtst.events)
        self.reset_log()
        self.backend.delay = 0.01
        self.backend.type_text("ab")
        self.assertEqual(zero, list(self.xtst.events))


class FallbackTests(SpeedBase):
    def test_unicode_uses_slow_path_and_restores_keymap(self):
        self.backend.type_text("a中")
        kinds = self.log.kinds()
        self.assertEqual(kinds,
                         ["tap", "tap", "remap", "tap", "tap", "restore"])
        self.assertFalse(self.backend._remapped)
        self.assertEqual(self.x11.mapping_history[-1],
                         (self.backend.spare_kc, [0] * self.x11.per))

    def test_unicode_flushes_pending_events_first(self):
        self.backend.type_text("abc中")
        # 'abc' 先排队、遇到 Unicode 前 flush，不能与 remap 交错
        idx_remap = self.log.kinds().index("remap")
        self.assertEqual(self.log.kinds().count("tap"), 6 + 2)
        self.assertGreater(idx_remap, 5)

    def test_flushes_on_exception(self):
        self.xtst.raise_on = {(38, True)}
        with self.assertRaises(RuntimeError):
            self.backend.type_text("ab")
        self.assertGreaterEqual(self.x11.flush_count, 1)

    def test_shift_released_when_batch_tap_raises(self):
        self.xtst.raise_on = {(38, True)}
        with self.assertRaises(RuntimeError):
            self.backend.type_text("A")
        self.assertEqual(self.xtst.events[-1], (50, False))

    def test_type_char_behavior_unchanged(self):
        self.backend.type_char("a")
        self.assertEqual(self.xtst.events, [(38, True), (38, False)])
        self.assertEqual(self.xtst.delays, [0, 0])
        self.assertEqual(self.x11.flush_count, 1)

    def test_use_xtest_delay_false_falls_back_to_per_char(self):
        with mock.patch.object(autopaste, "USE_XTEST_DELAY", False):
            self.backend.type_text("ab")
        self.assertEqual(self.xtst.events, [
            (38, True), (38, False), (56, True), (56, False)])
        self.assertEqual(self.x11.flush_count, 2)

    def test_safe_flag_disables_batch(self):
        with mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(autopaste, "countdown",
                                  return_value=True), \
                redirect_std():
            typer_cls.return_value.capabilities.return_value = {
                "name": "x11", "can_focus_info": True, "combos": True,
                "needs_remap": True, "window_control": True}
            typer_cls.return_value.resolvable.return_value = []
            autopaste.main(["--safe", "--dry-run", "hi"])
        self.assertFalse(typer_cls.return_value.backend.batch_enabled)


if __name__ == "__main__":
    unittest.main()
