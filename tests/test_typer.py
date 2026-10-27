# -*- coding: utf-8 -*-
"""X11Backend：按键顺序、Shift 释放、Unicode 重映射还原（假 X11/XTest）。"""

import unittest
from unittest import mock

import autopaste
from support import KEYSYM_SHIFT, FakeX11, FakeXtst


class TyperTestBase(unittest.TestCase):
    def setUp(self):
        self.x11 = FakeX11(lo=8, hi=64, per=2)
        self.xtst = FakeXtst()
        # 常用键：'a' 'A' 't'，Shift_L，Control_L，Alt_L，Return，Tab
        self.x11.add_key(ord("a"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(ord("A"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(ord("t"), 28, level0=ord("t"))
        self.x11.add_key(KEYSYM_SHIFT, 50)
        self.x11.add_named("Shift_L", KEYSYM_SHIFT)
        self.x11.add_key(0xFFE3, 37)
        self.x11.add_named("Control_L", 0xFFE3)
        self.x11.add_key(0xFFE9, 64)
        self.x11.add_named("Alt_L", 0xFFE9)
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
        self.typer = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
        self.addCleanup(self.typer.close)


class TypeCharTests(TyperTestBase):
    def test_plain_char_taps_keycode(self):
        self.typer.type_char("a")
        self.assertEqual(self.xtst.events, [(38, True), (38, False)])

    def test_newline_presses_return(self):
        self.typer.type_char("\n")
        self.assertEqual(self.xtst.events, [(36, True), (36, False)])

    def test_tab_presses_tab(self):
        self.typer.type_char("\t")
        self.assertEqual(self.xtst.events, [(23, True), (23, False)])

    def test_carriage_return_is_ignored(self):
        self.typer.type_char("\r")
        self.assertEqual(self.xtst.events, [])

    def test_shifted_char_presses_shift(self):
        self.typer.type_char("A")
        self.assertEqual(self.xtst.events, [
            (50, True), (38, True), (38, False), (50, False)])

    def test_unicode_remap_and_restore(self):
        with mock.patch.object(self.typer, "_restore_keymap",
                               wraps=self.typer._restore_keymap) as spy:
            self.typer.type_char("中")
        # 输入时临时改写 spare keycode，结束后一定还原
        self.assertEqual(spy.call_count, 1)
        self.assertFalse(self.typer._remapped)
        remap, restore = self.x11.mapping_history[0], self.x11.mapping_history[-1]
        self.assertEqual(remap[0], self.typer.spare_kc)
        self.assertEqual(remap[1][0], 0x01000000 + ord("中"))
        self.assertEqual(restore[0], self.typer.spare_kc)
        self.assertEqual(restore[1], [0] * self.x11.per)
        self.assertEqual(self.xtst.events, [(self.typer.spare_kc, True),
                                            (self.typer.spare_kc, False)])

    def test_unicode_restores_even_when_tap_fails(self):
        self.xtst.raise_on = {(self.typer.spare_kc, True)}
        with self.assertRaises(RuntimeError):
            self.typer.type_char("中")
        self.assertFalse(self.typer._remapped)
        self.assertEqual(self.x11.mapping_history[-1],
                         (self.typer.spare_kc, [0] * self.x11.per))

    def test_unicode_aborts_without_spare_keycode(self):
        for kc in range(self.x11.lo, self.x11.hi + 1):
            self.x11.keycode_to_keysym[(kc, 0, 0)] = 0x100
        typer = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
        self.addCleanup(typer.close)
        self.assertIsNone(typer.spare_kc)
        with self.assertRaises(autopaste.Abort):
            typer.type_char("中")

    def test_shift_released_when_tap_raises(self):
        self.xtst.raise_on = {(38, True)}
        with self.assertRaises(RuntimeError):
            self.typer.type_char("A")
        self.assertEqual(self.xtst.events[-1], (50, False))

    def test_type_text_sequence(self):
        self.typer.type_text("at")
        self.assertEqual(self.xtst.events, [
            (38, True), (38, False), (28, True), (28, False)])


class PressTests(TyperTestBase):
    def test_combo_modifier_order(self):
        self.typer.press("ctrl+alt+t")
        self.assertEqual(self.xtst.events, [
            (37, True), (64, True),
            (28, True), (28, False),
            (64, False), (37, False),
        ])

    def test_unknown_key_aborts(self):
        with self.assertRaises(autopaste.Abort):
            self.typer.press("ctrl+nosuchkey")

    def test_unknown_modifier_aborts(self):
        with self.assertRaises(autopaste.Abort):
            self.typer.press("hyper+t")

    def test_modifiers_released_when_tap_raises(self):
        self.xtst.raise_on = {(28, True)}
        with self.assertRaises(RuntimeError):
            self.typer.press("ctrl+alt+t")
        self.assertEqual(self.xtst.events[-2:], [(64, False), (37, False)])


class LifecycleTests(TyperTestBase):
    def test_open_display_failure(self):
        self.x11.dpy = 0
        with self.assertRaises(autopaste.Abort):
            autopaste.X11Backend()

    def test_close_is_idempotent(self):
        self.typer.close()
        self.typer.close()
        self.assertEqual(self.x11.close_count, 1)

    def test_resolvable(self):
        self.assertEqual(self.typer.resolvable("at"), [])
        self.assertEqual(self.typer.resolvable("中"), [])

    def test_delay_attribute(self):
        self.assertEqual(self.typer.delay, 0.0)


if __name__ == "__main__":
    unittest.main()
