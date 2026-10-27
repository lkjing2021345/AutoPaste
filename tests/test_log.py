# -*- coding: utf-8 -*-
"""日志出口与 --quiet/--verbose 的安全契约。"""

import io
import os
import unittest
from unittest import mock

import autopaste
import test_repl
from support import redirect_std, temp_home


class QuietReplTests(unittest.TestCase):
    def test_log_quiet_suppresses_banner_and_bye_but_not_errors(self):
        log = autopaste.Log(autopaste.Log.QUIET, err=io.StringIO())
        with mock.patch.object(autopaste, "LOG", log):
            code, _, _ = test_repl.run_repl(
                b"hi\n:q\n", typer=test_repl.ReplTyper(),
                cfg=autopaste.Settings(sleep=0.0))
        err = log._err.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("autopaste REPL", err)
        self.assertNotIn("bye", err)

    def test_quiet_still_reports_blocked_commands(self):
        log = autopaste.Log(autopaste.Log.QUIET, err=io.StringIO())
        with mock.patch.object(autopaste, "LOG", log):
            test_repl.run_repl(b"x\n:w\n:q\n",
                               typer=test_repl.ReplTyper(),
                               cfg=autopaste.Settings(sleep=0.0))
        self.assertIn("已拦截", log._err.getvalue())
        self.assertIn("用法: :w", log._err.getvalue())


class QuietCountdownTests(unittest.TestCase):
    def test_quiet_countdown_prints_target_exactly_once(self):
        log = autopaste.Log(autopaste.Log.QUIET, err=io.StringIO())
        rfd, wfd = os.pipe()
        os.close(wfd)
        raw = os.fdopen(rfd, "r")
        self.addCleanup(raw.close)
        with mock.patch.object(autopaste, "LOG", log), \
                mock.patch.object(autopaste.sys, "stdin", raw), \
                mock.patch.object(autopaste, "target_label",
                                  return_value="[kitty] shell"):
            ok = autopaste.countdown(0.3, test_repl.ReplTyper())
        err = log._err.getvalue()
        self.assertTrue(ok)
        self.assertEqual(err.count("目标"), 1)
        self.assertIn("[kitty] shell", err)
        self.assertIn("可取消", err)
        self.assertNotIn("\r", err)

    def test_quiet_countdown_still_cancels_on_keypress(self):
        log = autopaste.Log(autopaste.Log.QUIET, err=io.StringIO())
        rfd, wfd = os.pipe()
        os.write(wfd, b"x")
        os.close(wfd)
        raw = os.fdopen(rfd, "r")
        self.addCleanup(raw.close)
        with mock.patch.object(autopaste, "LOG", log), \
                mock.patch.object(autopaste.sys, "stdin", raw):
            ok = autopaste.countdown(5.0, test_repl.ReplTyper())
        self.assertFalse(ok)
        self.assertIn("已取消", log._err.getvalue())

    def test_quiet_keeps_multiline_warning_and_confirm(self):
        typer = test_repl.ReplTyper(
            focus={"class": "Alacritty", "name": "alacritty",
                   "title": "shell"})
        log = autopaste.Log(autopaste.Log.QUIET, err=io.StringIO())
        with mock.patch.object(autopaste, "LOG", log):
            autopaste.warn_multiline_target(typer, "a\nb")
        self.assertIn("多行文本会被逐行执行", log._err.getvalue())

        caps = {"name": "x11", "can_focus_info": True,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": True, "combos": True,
                "per_char_delay": True, "daemon": False,
                "window_control": False}
        with temp_home(), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(autopaste, "_confirm",
                                  return_value=False), \
                mock.patch.object(autopaste, "LOG", log), \
                redirect_std():
            typer_cls.return_value.capabilities.return_value = caps
            typer_cls.return_value.focus_info.return_value = {
                "class": "Alacritty", "name": "alacritty", "title": "shell"}
            code = autopaste.main(["--quiet", "a\nb"])
        self.assertEqual(code, 3)
        self.assertIn("多行文本会被逐行执行", log._err.getvalue())
        self.assertIn("已取消", log._err.getvalue())

    def test_verbose_never_prints_payload_text(self):
        caps = {"name": "x11", "can_focus_info": True,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": True, "combos": True,
                "per_char_delay": True, "daemon": False,
                "window_control": False}
        log = autopaste.Log(autopaste.Log.VERBOSE, err=io.StringIO())
        with temp_home(), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(autopaste, "LOG", log), \
                redirect_std() as (out, _):
            typer_cls.return_value.capabilities.return_value = caps
            typer_cls.return_value.resolvable.return_value = []
            code = autopaste.main(["--verbose", "--dry-run",
                                   "secret-token-123"])
        self.assertEqual(code, 0)
        self.assertIn("secret-token-123", out.getvalue())
        self.assertNotIn("secret-token-123", log._err.getvalue())
        self.assertIn("后端:", log._err.getvalue())


if __name__ == "__main__":
    unittest.main()
