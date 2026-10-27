# -*- coding: utf-8 -*-
"""03.2 --paste-mode：工具探测、备份/恢复、快捷键选择与降级（全部打桩）。"""

import os
import unittest
from unittest import mock

from support import redirect_std, temp_home

import autopaste


class FakeClipboard:
    name = "fake"

    def __init__(self, data=b"old", targets=None, *,
                 read_error=None, write_errors=None):
        self.data = data
        self._targets = ["UTF8_STRING"] if targets is None else targets
        self.read_error = read_error
        self.write_errors = dict(write_errors or {})
        self.read_calls = 0
        self.writes = []

    def targets(self):
        return self._targets

    def read(self):
        self.read_calls += 1
        if self.read_error:
            raise autopaste.ClipboardError(self.read_error)
        return self.data

    def write(self, data):
        if data in self.write_errors:
            raise autopaste.ClipboardError(self.write_errors[data])
        self.writes.append(data)


class FakeTyper:
    def __init__(self, *, press_error=None):
        self.pressed = []
        self.typed = []
        self.press_error = press_error

    def press(self, combo):
        if self.press_error:
            raise self.press_error
        self.pressed.append(combo)

    def type_text(self, text):
        self.typed.append(text)


class FakeRunner:
    def __init__(self, *, stdout=b"", rc=0, stderr=b""):
        self.stdout = stdout
        self.rc = rc
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, *, timeout, input_data=None):
        self.calls.append((list(argv), timeout, input_data))
        return mock.Mock(returncode=self.rc, stdout=self.stdout,
                         stderr=self.stderr)


class MakeClipboardTests(unittest.TestCase):
    def which(self, names):
        return lambda name: f"/usr/bin/{name}" if name in names else None

    def test_uses_xclip_when_available(self):
        runner = FakeRunner(stdout=b"hello")
        clip = autopaste.make_clipboard(
            "x11", which=self.which({"xclip"}), runner=runner)
        self.assertEqual(clip.name, "xclip")
        self.assertEqual(clip.read(), b"hello")
        argv, timeout, _ = runner.calls[0]
        self.assertEqual(argv, ["xclip", "-selection", "clipboard", "-o"])
        self.assertEqual(timeout, autopaste.CLIP_READ_TIMEOUT)

    def test_falls_back_to_xsel(self):
        clip = autopaste.make_clipboard(
            "x11", which=self.which({"xsel"}), runner=FakeRunner())
        self.assertEqual(clip.name, "xsel")
        self.assertIsNone(clip.targets())     # xsel 不支持列 targets

    def test_uses_wl_copy_on_wayland(self):
        clip = autopaste.make_clipboard(
            "wayland", which=self.which({"wl-copy", "wl-paste"}),
            runner=FakeRunner())
        self.assertEqual(clip.name, "wl-copy")

    def test_wayland_without_wl_uses_xclip(self):
        clip = autopaste.make_clipboard(
            "wayland", which=self.which({"xclip"}), runner=FakeRunner())
        self.assertEqual(clip.name, "xclip")

    def test_missing_tools_returns_none(self):
        self.assertIsNone(autopaste.make_clipboard(
            "x11", which=lambda name: None))

    def test_write_passes_argv_and_bytes(self):
        runner = FakeRunner()
        clip = autopaste.make_clipboard(
            "x11", which=self.which({"xclip"}), runner=runner)
        clip.write("中文".encode("utf-8"))
        argv, _, data = runner.calls[0]
        self.assertEqual(argv, ["xclip", "-selection", "clipboard", "-i"])
        self.assertEqual(data, "中文".encode("utf-8"))

    def test_read_failure_raises(self):
        clip = autopaste.make_clipboard(
            "x11", which=self.which({"xclip"}),
            runner=FakeRunner(rc=1, stderr=b"no selection"))
        with self.assertRaises(autopaste.ClipboardError) as ctx:
            clip.read()
        self.assertIn("no selection", str(ctx.exception))


class PasteKeyTests(unittest.TestCase):
    def test_terminal_uses_ctrl_shift_v(self):
        self.assertEqual(
            autopaste.paste_key_for({"class": "kitty", "name": "kitty"}),
            "ctrl+shift+v")

    def test_xterm_uses_shift_insert(self):
        self.assertEqual(
            autopaste.paste_key_for({"class": "XTerm", "name": "xterm"}),
            "shift+insert")

    def test_gui_uses_ctrl_v(self):
        self.assertEqual(
            autopaste.paste_key_for({"class": "firefox", "name": "Navigator"}),
            "ctrl+v")

    def test_unknown_target_uses_ctrl_v(self):
        self.assertEqual(autopaste.paste_key_for(None), "ctrl+v")

    def test_override_wins(self):
        self.assertEqual(
            autopaste.paste_key_for({"class": "kitty"}, "ctrl+alt+v"),
            "ctrl+alt+v")


class PasteTextTests(unittest.TestCase):
    def test_backs_up_and_restores(self):
        clip = FakeClipboard(b"old")
        typer = FakeTyper()
        autopaste.paste_text(typer, "new", clip=clip, paste_key="ctrl+v",
                             sleep=lambda s: None)
        self.assertEqual(clip.writes, [b"new", b"old"])
        self.assertEqual(typer.pressed, ["ctrl+v"])

    def test_utf8_payload_written_as_bytes(self):
        clip = FakeClipboard(b"")
        autopaste.paste_text(FakeTyper(), "中文", clip=clip,
                             sleep=lambda s: None)
        self.assertEqual(clip.writes[0], "中文".encode("utf-8"))

    def test_refuses_image_only_clipboard(self):
        clip = FakeClipboard(targets=["image/png", "TARGETS"])
        with self.assertRaises(autopaste.ClipboardUnsafe) as ctx:
            autopaste.paste_text(FakeTyper(), "x", clip=clip)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(clip.writes, [])
        self.assertEqual(clip.read_calls, 0)

    def test_refuses_backup_over_4mib(self):
        clip = FakeClipboard(b"x" * (autopaste.MAX_CLIP_BACKUP + 1))
        with self.assertRaises(autopaste.ClipboardUnsafe):
            autopaste.paste_text(FakeTyper(), "x", clip=clip)
        self.assertEqual(clip.writes, [])

    def test_read_failure_is_fail_closed(self):
        clip = FakeClipboard(read_error="timeout")
        with self.assertRaises(autopaste.ClipboardUnsafe) as ctx:
            autopaste.paste_text(FakeTyper(), "x", clip=clip)
        self.assertIn("无法备份", str(ctx.exception))
        self.assertEqual(clip.writes, [])

    def test_no_backup_skips_read_and_restore(self):
        clip = FakeClipboard(b"old")
        autopaste.paste_text(FakeTyper(), "new", clip=clip, restore=False,
                             sleep=lambda s: None)
        self.assertEqual(clip.read_calls, 0)
        self.assertEqual(clip.writes, [b"new"])

    def test_restore_failure_warns_but_does_not_raise(self):
        clip = FakeClipboard(b"old",
                             write_errors={b"old": "selection lost"})
        typer = FakeTyper()
        with redirect_std() as (_, err):
            autopaste.paste_text(typer, "new", clip=clip,
                                 sleep=lambda s: None)
        self.assertIn("剪贴板恢复失败", err.getvalue())
        self.assertEqual(typer.pressed, ["ctrl+v"])

    def test_restore_runs_when_press_fails(self):
        clip = FakeClipboard(b"old")
        typer = FakeTyper(press_error=autopaste.BackendError("boom"))
        with self.assertRaises(autopaste.BackendError):
            autopaste.paste_text(typer, "new", clip=clip,
                                 sleep=lambda s: None)
        self.assertEqual(clip.writes, [b"new", b"old"])

    def test_waits_settle_then_configured_wait(self):
        seen = []
        autopaste.paste_text(FakeTyper(), "new", clip=FakeClipboard(),
                             wait=1.25, sleep=seen.append)
        self.assertEqual(seen, [autopaste.CLIP_SETTLE, 1.25])

    def test_default_wait_scales_with_size(self):
        self.assertEqual(autopaste.default_paste_wait(10), 0.5)
        self.assertGreater(autopaste.default_paste_wait(900_000), 0.5)
        self.assertLessEqual(autopaste.default_paste_wait(50 << 20), 3.0)


class MainPasteTests(unittest.TestCase):
    def _run(self, argv, *, clip=None, clip_missing=False, caps=None,
             focus=None, error=None):
        caps = caps or {"name": "x11", "can_focus_info": True,
                        "can_paste_mode": False, "unicode": True,
                        "needs_remap": True, "combos": True,
                        "per_char_delay": True, "daemon": False,
                        "window_control": True}
        factory = mock.Mock(return_value=None if clip_missing else clip)
        with temp_home(), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(autopaste, "countdown", return_value=True), \
                mock.patch.object(autopaste, "make_clipboard", factory), \
                mock.patch.object(autopaste.time, "sleep", return_value=None), \
                mock.patch.object(autopaste, "paste_text",
                                  wraps=autopaste.paste_text) as paste_spy, \
                redirect_std() as (out, err):
            typer = typer_cls.return_value
            typer.capabilities.return_value = caps
            typer.focus_info.return_value = focus
            typer.resolvable.return_value = []
            if error:
                typer.type_text.side_effect = error
            code = autopaste.main(argv)
            return code, out.getvalue(), err.getvalue(), typer, factory, \
                paste_spy

    def test_paste_writes_clipboard_and_presses_ctrl_v(self):
        clip = FakeClipboard(b"old")
        code, _, _, typer, factory, _ = self._run(
            ["--paste-mode", "hi"], clip=clip)
        self.assertEqual(code, 0)
        factory.assert_called_once()
        self.assertEqual(clip.writes, [b"hi", b"old"])
        typer.press.assert_called_once_with("ctrl+v")
        typer.type_text.assert_not_called()

    def test_terminal_target_uses_ctrl_shift_v(self):
        clip = FakeClipboard(b"")
        code, _, _, typer, _, _ = self._run(
            ["--paste-mode", "hi"], clip=clip,
            focus={"class": "kitty", "name": "kitty"})
        self.assertEqual(code, 0)
        typer.press.assert_called_once_with("ctrl+shift+v")

    def test_paste_key_override(self):
        code, _, _, typer, _, _ = self._run(
            ["--paste-mode", "--paste-key", "ctrl+alt+v", "hi"],
            clip=FakeClipboard(b""))
        self.assertEqual(code, 0)
        typer.press.assert_called_once_with("ctrl+alt+v")

    def test_paste_key_without_paste_mode_rejected(self):
        code, _, err, typer, _, _ = self._run(
            ["--paste-key", "ctrl+v", "hi"])
        self.assertEqual(code, 1)
        self.assertIn("--paste-mode", err)
        typer.type_text.assert_not_called()

    def test_paste_mode_with_key_combo_rejected(self):
        code, _, err, typer, _, _ = self._run(
            ["--paste-mode", "-k", "enter", "hi"])
        self.assertEqual(code, 1)
        self.assertIn("不能与 -k", err)
        typer.press.assert_not_called()

    def test_missing_tools_falls_back_to_typing_with_hint(self):
        code, _, err, typer, _, _ = self._run(
            ["--paste-mode", "hi"], clip_missing=True)
        self.assertEqual(code, 0)
        self.assertIn("降级为逐字符", err)
        self.assertIn("xclip", err)
        typer.type_text.assert_called_once_with("hi")

    def test_backup_failure_exits_2_without_typing(self):
        clip = FakeClipboard(targets=["image/png"])
        code, _, err, typer, _, _ = self._run(
            ["--paste-mode", "hi"], clip=clip)
        self.assertEqual(code, 2)
        self.assertIn("--no-clipboard-backup", err)
        typer.type_text.assert_not_called()
        typer.press.assert_not_called()
        self.assertEqual(clip.writes, [])

    def test_no_backup_flag_warns_and_does_not_restore(self):
        clip = FakeClipboard(b"old")
        code, _, err, _, _, _ = self._run(
            ["--paste-mode", "--no-clipboard-backup", "hi"], clip=clip)
        self.assertEqual(code, 0)
        self.assertIn("不备份/不恢复", err)
        self.assertEqual(clip.writes, [b"hi"])

    def test_dry_run_never_touches_clipboard(self):
        code, out, _, typer, factory, paste_spy = self._run(
            ["--paste-mode", "--dry-run", "hi"])
        self.assertEqual(code, 0)
        factory.assert_not_called()
        paste_spy.assert_not_called()
        typer.press.assert_not_called()
        typer.type_text.assert_not_called()
        self.assertIn("粘贴模式", out)
        self.assertIn("dry-run 未执行", out)

    def test_clipboard_write_failure_falls_back_to_typing(self):
        clip = FakeClipboard(b"old", write_errors={b"hi": "xclip died"})
        code, _, err, typer, _, _ = self._run(
            ["--paste-mode", "hi"], clip=clip)
        self.assertEqual(code, 0)
        self.assertIn("降级为逐字符", err)
        typer.type_text.assert_called_once_with("hi")
        self.assertEqual(typer.press.call_count, 0)

    def test_paste_wait_out_of_range_exits_1(self):
        code, _, err, _, _, _ = self._run(
            ["--paste-mode", "--paste-wait", "99", "hi"])
        self.assertEqual(code, 1)
        self.assertIn("paste-wait", err)


if __name__ == "__main__":
    unittest.main()
