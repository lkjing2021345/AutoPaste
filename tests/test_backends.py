# -*- coding: utf-8 -*-
"""后端抽象：会话探测、后端选择、wtype 命令行与分片（全部打桩）。

这些用例不需要 DISPLAY、不需要安装 wtype/ydotool：外部命令统一经过
``autopaste._run``，测试里注入 FakeRunner。
"""

import os
import subprocess
import unittest
from unittest import mock

import autopaste
from support import KEYSYM_SHIFT, FakeX11, FakeXtst, redirect_std


def _which(names):
    """返回一个假 shutil.which：names 里的工具视为存在。"""
    def fake(name):
        return f"/usr/bin/{name}" if name in names else None
    return fake


class FakeRunner:
    def __init__(self, rc=0, stderr="", stdout=""):
        self.rc = rc
        self.stderr = stderr
        self.stdout = stdout
        self.calls = []
        self.kwargs = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        return subprocess.CompletedProcess(argv, self.rc, self.stdout,
                                           self.stderr)


# ---------------------------------------------------------------- 会话探测
class DetectSessionTests(unittest.TestCase):
    def test_xdg_session_type_x11(self):
        self.assertEqual(
            autopaste.detect_session({"XDG_SESSION_TYPE": "X11"}), "x11")

    def test_wayland_wins_over_display(self):
        self.assertEqual(
            autopaste.detect_session({"XDG_SESSION_TYPE": "wayland",
                                      "DISPLAY": ":0"}), "wayland")

    def test_falls_back_to_wayland_display(self):
        self.assertEqual(
            autopaste.detect_session({"WAYLAND_DISPLAY": "wayland-0"}),
            "wayland")

    def test_falls_back_to_display(self):
        self.assertEqual(autopaste.detect_session({"DISPLAY": ":0"}), "x11")

    def test_unknown_without_any_marker(self):
        self.assertEqual(autopaste.detect_session({}), "unknown")


# ---------------------------------------------------------------- 后端选择
class ChooseBackendTests(unittest.TestCase):
    def test_auto_on_x11_always_chooses_x11(self):
        env = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}
        # 即使 PATH 里只有 Wayland 工具，真 X11 会话也必须走 x11
        self.assertEqual(
            autopaste.choose_backend("auto", env=env,
                                     which=_which({"wtype", "ydotool"})),
            "x11")

    def test_auto_wayland_prefers_wtype_on_kde(self):
        env = {"XDG_SESSION_TYPE": "wayland",
               "XDG_CURRENT_DESKTOP": "KDE"}
        self.assertEqual(
            autopaste.choose_backend("auto", env=env,
                                     which=_which({"wtype", "ydotool"})),
            "wtype")

    def test_auto_without_tools_raises_install_hint(self):
        with self.assertRaises(autopaste.BackendUnavailable) as ctx:
            autopaste.choose_backend(
                "auto", env={"XDG_SESSION_TYPE": "wayland"},
                which=lambda name: None)
        msg = str(ctx.exception)
        self.assertIn("apt install wtype", msg)
        self.assertIn("pacman -S wtype", msg)
        self.assertIn("ydotoold", msg)

    def test_explicit_backend_is_returned_verbatim(self):
        self.assertEqual(
            autopaste.choose_backend("wtype", env={}, which=lambda n: None),
            "wtype")

    def test_unknown_session_without_tools_raises(self):
        with self.assertRaises(autopaste.BackendUnavailable):
            autopaste.choose_backend("auto", env={}, which=lambda n: None)


# ---------------------------------------------------------------- wtype
class WtypeArgvTests(unittest.TestCase):
    def make(self, delay=0.008, rc=0, stderr=""):
        runner = FakeRunner(rc=rc, stderr=stderr)
        backend = autopaste.WtypeBackend(
            delay=delay, which=_which({"wtype"}), runner=runner)
        return backend, runner

    def test_type_text_argv(self):
        backend, runner = self.make()
        backend.type_text("hello")
        self.assertEqual(runner.calls[0],
                         ["wtype", "-d", "8", "--", "hello"])

    def test_no_delay_flag_when_delay_zero(self):
        backend, runner = self.make(delay=0.0)
        backend.type_text("x")
        self.assertEqual(runner.calls[0], ["wtype", "--", "x"])

    def test_delay_converted_to_ms_and_clamped(self):
        backend, runner = self.make(delay=6.0)
        backend.type_text("x")
        self.assertEqual(runner.calls[0][:3], ["wtype", "-d", "5000"])

    def test_sub_minute_delay_never_becomes_zero(self):
        backend, runner = self.make(delay=0.0004)
        backend.type_text("x")
        self.assertEqual(runner.calls[0][:3], ["wtype", "-d", "1"])

    def test_text_starting_with_dash_uses_separator(self):
        backend, runner = self.make()
        backend.type_text("-rf")
        self.assertEqual(runner.calls[0][-2:], ["--", "-rf"])

    def test_combo_argv_ctrl_shift_v(self):
        backend, runner = self.make()
        backend.press("ctrl+shift+v")
        self.assertEqual(
            runner.calls[0],
            ["wtype", "-d", "8", "-M", "ctrl", "-M", "shift",
             "-k", "v", "-m", "shift", "-m", "ctrl"])

    def test_special_key_maps_to_keysym_name(self):
        backend, runner = self.make()
        backend.press("enter")
        self.assertEqual(runner.calls[0], ["wtype", "-d", "8", "-k", "Return"])

    def test_unknown_modifier_aborts(self):
        backend, _ = self.make()
        with self.assertRaises(autopaste.Abort):
            backend.press("hyper+t")

    def test_nonzero_exit_raises_backend_error_with_stderr(self):
        backend, _ = self.make(rc=1, stderr="failed to create virtual keyboard")
        with self.assertRaises(autopaste.BackendError) as ctx:
            backend.type_text("x")
        msg = str(ctx.exception)
        self.assertIn("1", msg)
        self.assertIn("virtual keyboard", msg)
        self.assertIn("ydotool", msg)

    def test_missing_binary_raises_unavailable(self):
        with self.assertRaises(autopaste.BackendUnavailable):
            autopaste.WtypeBackend(which=lambda name: None)

    def test_empty_text_sends_nothing(self):
        backend, runner = self.make()
        backend.type_text("")
        self.assertEqual(runner.calls, [])

    def test_run_seam_never_uses_shell(self):
        completed = subprocess.CompletedProcess(["wtype"], 0, "", "")
        with mock.patch.object(autopaste.subprocess, "run",
                               return_value=completed) as run:
            autopaste._run(["wtype", "--", "hi"])
        _, kwargs = run.call_args
        self.assertNotIn("shell", kwargs)
        self.assertIs(kwargs.get("check"), False)

    def test_chunks_long_text_below_arg_limit(self):
        backend, runner = self.make(delay=0.0)
        text = "a" * (autopaste.WtypeBackend.MAX_ARG * 2 + 7)
        backend.type_text(text)
        self.assertEqual(len(runner.calls), 3)
        sent = [call[-1] for call in runner.calls]
        self.assertEqual("".join(sent), text)
        for chunk in sent:
            self.assertLessEqual(len(chunk.encode("utf-8")),
                                 autopaste.WtypeBackend.MAX_ARG)

    def test_chunks_never_split_multibyte_chars(self):
        backend, runner = self.make(delay=0.0)
        # 让第一个分片刚好用完 32768 字节边界，下一字符是 3 字节中文
        text = "a" * 32767 + "中" * 100
        backend.type_text(text)
        sent = [call[-1] for call in runner.calls]
        self.assertEqual("".join(sent), text)
        self.assertEqual(sent[0], "a" * 32767)
        self.assertEqual(sent[1][0], "中")
        for chunk in sent:
            self.assertLessEqual(len(chunk.encode("utf-8")),
                                 autopaste.WtypeBackend.MAX_ARG)

    def test_capabilities(self):
        backend, _ = self.make()
        caps = backend.capabilities()
        self.assertEqual(caps["name"], "wtype")
        self.assertFalse(caps["can_focus_info"])
        self.assertTrue(caps["unicode"])
        self.assertFalse(caps["needs_remap"])


# ---------------------------------------------------------------- 外观层
class TyperFacadeTests(unittest.TestCase):
    def test_delay_setter_forwards_to_backend(self):
        typer = autopaste.Typer(backend="wtype", delay=0.01,
                                which=_which({"wtype"}),
                                runner=FakeRunner())
        self.addCleanup(typer.close)
        typer.delay = 0.25
        self.assertEqual(typer.delay, 0.25)
        self.assertEqual(typer.backend.delay, 0.25)

    def test_close_is_idempotent(self):
        typer = autopaste.Typer(backend="wtype", which=_which({"wtype"}),
                                runner=FakeRunner())
        typer.close()
        typer.close()
        self.assertTrue(typer._closed)

    def test_target_label_degrades_on_wayland(self):
        typer = autopaste.Typer(backend="wtype", which=_which({"wtype"}),
                                runner=FakeRunner())
        self.addCleanup(typer.close)
        label = autopaste.target_label(typer)
        self.assertIn("Wayland", label)
        self.assertIn("wtype", label)
        self.assertIn("看不到焦点窗口", label)

    def test_capabilities_table(self):
        expected = {
            "x11": (True, True, False),
            "wtype": (False, True, False),
        }
        fake_x11 = FakeX11()
        with mock.patch.object(autopaste, "x11", fake_x11), \
                mock.patch.object(autopaste, "xtst", FakeXtst()):
            x11_backend = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
            x11_caps = x11_backend.capabilities()
            x11_backend.close()
        wtype_backend = autopaste.WtypeBackend(
            which=_which({"wtype"}), runner=FakeRunner())
        for name, (focus, uni, daemon) in expected.items():
            caps = x11_caps if name == "x11" else wtype_backend.capabilities()
            self.assertEqual(caps["name"], name)
            self.assertEqual(caps["can_focus_info"], focus, name)
            self.assertEqual(caps["unicode"], uni, name)
            self.assertEqual(caps["daemon"], daemon, name)


# ---------------------------------------------------------------- CLI/诊断
class CliBackendTests(unittest.TestCase):
    def test_help_works_without_libx11(self):
        # 构造函数/帮助路径绝不能触发 dlopen
        with mock.patch.object(autopaste.ctypes, "CDLL",
                               side_effect=OSError("no libX11")):
            help_text = autopaste.build_parser().format_help()
        self.assertIn("--backend", help_text)
        self.assertIn("--list-backends", help_text)

    def test_list_backends_output(self):
        out = autopaste.format_backends(
            env={"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wl-0"},
            which=_which({"wtype"}),
            runner=FakeRunner(rc=1))
        self.assertIn("x11", out)
        self.assertIn("wtype", out)
        self.assertIn("当前会话: wayland", out)
        self.assertIn("auto 将选择: wtype", out)

    def test_invalid_backend_exits_2(self):
        with redirect_std():
            with self.assertRaises(SystemExit) as ctx:
                autopaste.build_parser().parse_args(
                    ["--backend", "nosuch", "hi"])
        self.assertEqual(ctx.exception.code, 2)

    def test_explicit_missing_backend_exits_2(self):
        with mock.patch.dict(os.environ, {
                "XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": "wl-0"}, clear=True), \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.object(autopaste.shutil, "which",
                                  return_value=None), \
                redirect_std() as (_, err):
            code = autopaste.main(["--backend", "wtype", "hi"])
        self.assertEqual(code, 2)
        self.assertIn("wtype", err.getvalue())

    def _mock_wayland_typer(self):
        return mock.patch.object(autopaste, "Typer")

    def test_wayland_multiline_requires_confirm(self):
        with self._mock_wayland_typer() as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.object(autopaste, "_confirm",
                                  return_value=False), \
                redirect_std() as (_, err):
            typer_cls.return_value.capabilities.return_value = {
                "name": "wtype", "can_focus_info": False,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": False}
            typer_cls.return_value.focus_info.return_value = None
            code = autopaste.main(["a\nb"])
        self.assertEqual(code, 3)
        self.assertIn("多行", err.getvalue())
        typer_cls.return_value.type_text.assert_not_called()

    def test_wayland_multiline_with_yes_proceeds(self):
        with self._mock_wayland_typer() as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                redirect_std():
            typer_cls.return_value.capabilities.return_value = {
                "name": "wtype", "can_focus_info": False,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": False}
            typer_cls.return_value.focus_info.return_value = None
            code = autopaste.main(["-y", "a\nb"])
        self.assertEqual(code, 0)
        typer_cls.return_value.type_text.assert_called_once_with("a\nb")


# ---------------------------------------------------------------- X11 回归
class X11RegressionTests(unittest.TestCase):
    """后端拆分后 X11 的按键序列必须与拆分前逐字节一致。"""

    def setUp(self):
        self.x11 = FakeX11(lo=8, hi=64, per=2)
        self.xtst = FakeXtst()
        self.x11.add_key(ord("a"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(ord("A"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(KEYSYM_SHIFT, 50)
        self.x11.add_named("Shift_L", KEYSYM_SHIFT)
        self.x11.add_key(0xFFE3, 37)
        self.x11.add_named("Control_L", 0xFFE3)
        self.x11.add_key(0xFF0D, 36)
        self.x11.add_named("Return", 0xFF0D)
        patches = [mock.patch.object(autopaste, "x11", self.x11),
                   mock.patch.object(autopaste, "xtst", self.xtst)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.backend = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
        self.addCleanup(self.backend.close)

    def test_tap_sequence_unchanged(self):
        self.backend.type_char("a")
        self.assertEqual(self.xtst.events, [(38, True), (38, False)])

    def test_shift_wrapped_for_uppercase(self):
        self.backend.type_char("A")
        self.assertEqual(self.xtst.events,
                         [(50, True), (38, True), (38, False), (50, False)])

    def test_newline_maps_to_return(self):
        self.backend.type_char("\n")
        self.assertEqual(self.xtst.events, [(36, True), (36, False)])

    def test_unicode_remaps_spare_keycode_then_restores(self):
        self.backend.type_char("中")
        remap = self.x11.mapping_history[0]
        restore = self.x11.mapping_history[-1]
        self.assertEqual(remap[0], self.backend.spare_kc)
        self.assertEqual(remap[1][0], 0x01000000 + ord("中"))
        self.assertEqual(restore, (self.backend.spare_kc, [0] * self.x11.per))
        self.assertFalse(self.backend._remapped)

    def test_focus_info_delegates_to_focused_window(self):
        target = {"title": "t", "name": "n", "class": "xterm"}
        with mock.patch.object(autopaste, "focused_window",
                               return_value=target) as spy:
            self.assertEqual(self.backend.focus_info(), target)
        spy.assert_called_once_with(self.backend.dpy)

    def test_close_is_idempotent(self):
        self.backend.close()
        self.backend.close()
        self.assertEqual(self.x11.close_count, 1)


if __name__ == "__main__":
    unittest.main()
