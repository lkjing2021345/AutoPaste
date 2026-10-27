# -*- coding: utf-8 -*-
"""REPL：焦点复核、倒计时 EOF 语义（无需 tty / X11）。"""

import io
import os
import unittest
from unittest import mock

import autopaste
from support import FakeX11, FakeXtst, redirect_std  # noqa: F401


class FakeTTY:
    """把 pipe 包成"看起来是终端"的 stdin：isatty 恒真。"""

    def __init__(self, raw):
        self._raw = raw

    def fileno(self):
        return self._raw.fileno()

    def isatty(self):
        return True

    def readline(self, *a):
        return self._raw.readline(*a)

    def read(self, *a):
        return self._raw.read(*a)


class ReplTyper:
    def __init__(self, *, focused=True):
        self.focused = focused
        self.typed = []
        self.pressed = []
        self.delay = 0.0

    def capabilities(self):
        return {"name": "x11", "can_focus_info": True,
                "can_paste_mode": True, "unicode": True,
                "needs_remap": True, "combos": True,
                "per_char_delay": True, "daemon": False,
                "window_control": True}

    def focus_info(self):
        return {"class": "kitty", "name": "kitty", "title": "shell"}

    def list_windows(self):
        return [autopaste.WindowInfo(0x200, cls="kitty", title="shell")]

    def focused_window_id(self):
        return 0x200

    def activate_window(self, info, **kwargs):
        return True

    def is_window_focused(self, xid):
        return self.focused

    def type_text(self, text):
        self.typed.append(text)

    def press(self, combo):
        self.pressed.append(combo)

    def close(self):
        pass


def run_repl(data: bytes, *, typer, cfg):
    """把 data 喂给 repl，返回退出码。"""
    rfd, wfd = os.pipe()
    try:
        os.write(wfd, data)
    finally:
        os.close(wfd)
    raw = os.fdopen(rfd, "r")
    try:
        stdin = FakeTTY(raw)
        with mock.patch.object(autopaste.sys, "stdin", stdin), \
                mock.patch.object(autopaste.termios, "tcgetattr",
                                  return_value=["old"]), \
                mock.patch.object(autopaste.termios, "tcsetattr"), \
                mock.patch.object(autopaste.tty, "setcbreak"), \
                mock.patch.object(autopaste, "countdown", return_value=True), \
                redirect_std() as (out, err):
            code = autopaste.repl(typer, cfg)
        return code, out.getvalue(), err.getvalue()
    finally:
        raw.close()


class ReplFocusTests(unittest.TestCase):
    def test_focus_lost_after_countdown_does_not_inject(self):
        typer = ReplTyper(focused=False)
        cfg = autopaste.Settings(sleep=0.0, window="kitty")
        code, _, err = run_repl(b"hi\n", typer=typer, cfg=cfg)
        self.assertEqual(code, 0)
        self.assertEqual(typer.typed, [])
        self.assertEqual(typer.pressed, [])
        self.assertIn("焦点已离开", err)

    def test_focus_kept_after_countdown_injects(self):
        typer = ReplTyper(focused=True)
        cfg = autopaste.Settings(sleep=0.0, window="kitty")
        code, _, _ = run_repl(b"hi\n", typer=typer, cfg=cfg)
        self.assertEqual(code, 0)
        self.assertEqual(typer.typed, ["hi"])

    def test_no_window_target_skips_focus_check(self):
        typer = ReplTyper(focused=False)
        cfg = autopaste.Settings(sleep=0.0)
        code, _, _ = run_repl(b"hi\n", typer=typer, cfg=cfg)
        self.assertEqual(code, 0)
        self.assertEqual(typer.typed, ["hi"])


if __name__ == "__main__":
    unittest.main()
