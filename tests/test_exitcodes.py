# -*- coding: utf-8 -*-
"""退出码表（0/1/2/3/130）与两处未捕获异常收口。"""

import io
import os
import sys
import termios
import unittest
from unittest import mock

import autopaste
import test_repl
from support import redirect_std, temp_home


def caps(**over):
    base = {"name": "x11", "can_focus_info": True,
            "can_paste_mode": False, "unicode": True,
            "needs_remap": True, "combos": True,
            "per_char_delay": True, "daemon": False,
            "window_control": False}
    base.update(over)
    return base


def run_main(argv, *, euid=1000, confirm=False, countdown=True,
             focus=None, open_error=None):
    with temp_home(), \
            mock.patch.object(autopaste, "Typer") as typer_cls, \
            mock.patch.object(os, "geteuid", return_value=euid,
                              create=True), \
            mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
            mock.patch.object(autopaste, "_confirm", return_value=confirm), \
            mock.patch.object(autopaste, "countdown",
                              return_value=countdown), \
            redirect_std() as (out, err):
        typer_cls.return_value.capabilities.return_value = caps()
        typer_cls.return_value.resolvable.return_value = []
        typer_cls.return_value.focus_info.return_value = focus
        if open_error is not None:
            with mock.patch("builtins.open", side_effect=open_error):
                code = autopaste.main(argv)
        else:
            code = autopaste.main(argv)
    return code, out.getvalue(), err.getvalue(), typer_cls


class ExitCodeMatrixTests(unittest.TestCase):
    def test_exit_code_matrix(self):
        # 0：成功（dry-run 不注入）
        code, out, _, _ = run_main(["--dry-run", "hi"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)

        # 1：内容/数值校验失败
        code, _, err, _ = run_main(["--delay", "99", "hi"])
        self.assertEqual(code, 1)
        self.assertIn("字符间隔", err)

        # 2：安全/环境拒绝（root）
        code, _, err, typer_cls = run_main(["hi"], euid=0)
        self.assertEqual(code, 2)
        self.assertIn("root", err)
        typer_cls.assert_not_called()

        # 3：用户在安全确认处取消
        code, _, err, _ = run_main(
            ["a\nb"], confirm=False,
            focus={"class": "Alacritty", "name": "alacritty",
                   "title": "shell"})
        self.assertEqual(code, 3)
        self.assertIn("已取消", err)

        # 130：倒计时被取消
        code, _, _, _ = run_main(["-s", "1", "hi"], countdown=False)
        self.assertEqual(code, 130)

    def test_backend_error_codes_survive_inheritance(self):
        self.assertEqual(autopaste.Abort.code, 1)
        self.assertEqual(autopaste.BackendError.code, 1)
        self.assertEqual(autopaste.BackendUnavailable.code, 2)
        self.assertEqual(autopaste.ConfigError.code, 2)
        self.assertEqual(autopaste.WindowUnavailable.code, 2)
        self.assertEqual(autopaste.WindowCancelled.code, 3)
        # 环境错误不能因为继承自 BackendError 而退化成 1
        self.assertIsInstance(autopaste.BackendUnavailable(),
                              autopaste.BackendError)
        self.assertEqual(autopaste.BackendUnavailable().code, 2)

    def test_argparse_usage_error_exits_2(self):
        with redirect_std():
            with self.assertRaises(SystemExit) as ctx:
                autopaste.build_parser().parse_args(["--nosuch-option"])
            self.assertEqual(ctx.exception.code, 2)
            with self.assertRaises(SystemExit) as ctx:
                autopaste.build_parser().parse_args(["-q", "-v", "hi"])
            self.assertEqual(ctx.exception.code, 2)

    def test_help_and_version_still_exit_zero_after_log_refactor(self):
        with redirect_std():
            for argv in (["--help"], ["--version"]):
                with self.assertRaises(SystemExit) as ctx:
                    autopaste.build_parser().parse_args(argv)
                self.assertEqual(ctx.exception.code, 0)


class UncaughtExceptionFixesTests(unittest.TestCase):
    def test_repl_requires_tty_exits_2(self):
        with temp_home(), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(sys.stdin, "isatty", return_value=False), \
                redirect_std() as (_, err):
            code = autopaste.main(["--repl"])
        self.assertEqual(code, 2)
        self.assertIn("REPL 需要交互式终端", err.getvalue())
        typer_cls.assert_not_called()

    def test_repl_termios_error_exits_2(self):
        rfd, wfd = os.pipe()
        os.close(wfd)
        raw = os.fdopen(rfd, "r")
        self.addCleanup(raw.close)
        log = autopaste.Log(err=io.StringIO())
        with mock.patch.object(autopaste.sys, "stdin",
                               test_repl.FakeTTY(raw)), \
                mock.patch.object(autopaste.termios, "tcgetattr",
                                  side_effect=termios.error("boom")), \
                mock.patch.object(autopaste, "LOG", log), \
                redirect_std():
            code = autopaste.repl(test_repl.ReplTyper(),
                                  autopaste.Settings(sleep=0.0))
        self.assertEqual(code, 2)
        self.assertIn("termios", log._err.getvalue())

    def test_file_open_error_is_abort_not_traceback(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("hi")
            path = f.name
        self.addCleanup(os.unlink, path)
        code, _, err, typer_cls = run_main(
            ["-f", path], open_error=PermissionError("denied"))
        self.assertEqual(code, 1)
        self.assertIn("无法读取文件", err)
        typer_cls.assert_not_called()

    def test_broken_pipe_in_console_main_exits_zero_without_traceback(self):
        with mock.patch.object(autopaste.signal, "signal"), \
                mock.patch.object(autopaste, "main",
                                  side_effect=BrokenPipeError), \
                redirect_std() as (_, err):
            code = autopaste.console_main()
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
