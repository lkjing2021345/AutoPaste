# -*- coding: utf-8 -*-
"""REPL :p（预览）/ :w（保存）/ :win（定向窗口）的安全契约。"""

import os
import stat
import tempfile
import unittest
from unittest import mock

import autopaste
import test_repl


def run(data: bytes, *, typer=None, cfg=None):
    typer = typer or test_repl.ReplTyper()
    cfg = cfg or autopaste.Settings(sleep=0.0)
    return test_repl.run_repl(data, typer=typer, cfg=cfg)


class PreviewTests(unittest.TestCase):
    def test_preview_uses_esc_preview_and_sends_nothing(self):
        typer = test_repl.ReplTyper()
        code, out, _ = run(b"\x1b[200~a\tb\x1b[201~\n:p\n:q\n",
                           typer=typer)
        self.assertEqual(code, 0)
        self.assertIn("预览", out)
        self.assertIn("a\\tb", out)
        self.assertIn("未发送", out)
        self.assertEqual(typer.typed, ["a\tb"])
        self.assertEqual(typer.pressed, [])

    def test_preview_without_last_reports_no_content(self):
        _, out, _ = run(b":p\n:q\n")
        self.assertIn("还没有可预览的内容", out)

    def test_preview_warns_on_terminal_multiline(self):
        typer = test_repl.ReplTyper(
            focus={"class": "Alacritty", "name": "alacritty",
                   "title": "shell"})
        _, out, _ = run(b"\x1b[200~a\nb\x1b[201~\n:p\n:q\n",
                        typer=typer)
        self.assertIn("多行文本会被逐行执行", out)
        self.assertEqual(typer.typed, ["a\nb"])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "out.txt")

    def test_save_writes_utf8_with_mode_0600(self):
        _, out, _ = run(f"中文\n:w {self.path}\n:q\n".encode())
        self.assertIn("已保存", out)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), "中文".encode())
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_save_refuses_symlink_target(self):
        target = os.path.join(self.tmp.name, "target.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("original")
        link = os.path.join(self.tmp.name, "link.txt")
        os.symlink(target, link)
        with mock.patch.object(test_repl.autopaste, "countdown",
                               return_value=True):
            _, out, _ = run(f"hacked\n:w {link}\n:q\n".encode())
        self.assertIn("拒绝写入符号链接", out)
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "original")

    def test_save_refuses_directory_and_fifo(self):
        d = os.path.join(self.tmp.name, "adir")
        os.makedirs(d)
        _, out, _ = run(f"x\n:w {d}\n:q\n".encode())
        self.assertIn("拒绝写入非普通文件", out)
        fifo = os.path.join(self.tmp.name, "fifo")
        os.mkfifo(fifo)
        _, out, _ = run(f"x\n:w {fifo}\n:q\n".encode())
        self.assertIn("拒绝写入非普通文件", out)

    def test_save_is_atomic_when_replace_fails(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("original")
        with mock.patch.object(autopaste.os, "replace",
                               side_effect=OSError("boom")):
            _, out, _ = run(f"new\n:w {self.path}\n:q\n".encode())
        self.assertIn("已拦截", out)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "original")
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_save_without_arg_prints_usage(self):
        _, out, _ = run(b"x\n:w\n:q\n")
        self.assertIn("用法: :w <文件>", out)

    def test_save_without_last_reports_no_content(self):
        _, out, _ = run(f":w {self.path}\n:q\n".encode())
        self.assertIn("还没有可保存的内容", out)
        self.assertFalse(os.path.exists(self.path))

    def test_save_overwrites_regular_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("old")
        os.chmod(self.path, 0o644)
        _, out, _ = run(f"new\n:w {self.path}\n:q\n".encode())
        self.assertIn("已保存", out)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "new")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_save_records_only_command_in_history(self):
        hist = autopaste.History(None)
        typer = test_repl.ReplTyper()
        test_repl.run_repl(f"body\n:w {self.path}\n:q\n".encode(),
                          typer=typer, cfg=autopaste.Settings(sleep=0.0),
                          history=hist)
        self.assertEqual(hist.entries, ["body", f":w {self.path}", ":q"])
        self.assertNotIn("body\n", "".join(hist.entries))


class CommandNamespaceTests(unittest.TestCase):
    def test_win_sets_window_and_w_saves(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "f.txt")
        cfg = autopaste.Settings(sleep=0.0)
        typer = test_repl.ReplTyper()
        run(f":win kitty\nhello\n:w {path}\n:q\n".encode(),
            typer=typer, cfg=cfg)
        self.assertEqual(cfg.window, "kitty")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello")

    def test_win_dash_clears_window(self):
        cfg = autopaste.Settings(sleep=0.0, window="kitty")
        run(b":win -\n:q\n", cfg=cfg)
        self.assertIsNone(cfg.window)


if __name__ == "__main__":
    unittest.main()
