# -*- coding: utf-8 -*-
"""历史记录：JSONL 持久化、权限 fail-closed、行编辑器上下键/前缀/草稿。"""

import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import autopaste
from support import editor_input, redirect_std


def feed(data: bytes, history=None):
    fd = editor_input(data)
    try:
        editor = autopaste.LineEditor(fd, history=history)
        with redirect_std():
            return editor.read("> ")
    finally:
        os.close(fd)


class HistoryNavigationTests(unittest.TestCase):
    def test_history_add_and_up_down_recall(self):
        hist = ["one", "two", "three"]
        self.assertEqual(feed(b"\x1b[A\n", hist), ("text", "three"))
        self.assertEqual(feed(b"\x1b[A\x1b[A\n", hist), ("text", "two"))
        self.assertEqual(feed(b"\x1b[A\x1b[A\x1b[B\n", hist),
                         ("text", "three"))

    def test_history_draft_restored_when_leaving_newest(self):
        hist = ["foobar"]
        self.assertEqual(feed(b"foo\x1b[A\x1b[B\n", hist), ("text", "foo"))
        self.assertEqual(feed(b"\x1b[A\x1b[B\n", hist), ("text", ""))

    def test_history_up_at_oldest_does_not_wrap(self):
        self.assertEqual(feed(b"\x1b[A\x1b[A\x1b[A\n", ["one", "two"]),
                         ("text", "one"))

    def test_history_prefix_filter_only_matches_prefix(self):
        hist = [":s 1", "hello", ":s 2"]
        self.assertEqual(feed(b":s\x1b[A\n", hist), ("text", ":s 2"))
        self.assertEqual(feed(b":s\x1b[A\x1b[A\n", hist), ("text", ":s 1"))

    def test_history_empty_prefix_cycles_all_entries(self):
        self.assertEqual(feed(b"\x1b[A\x1b[A\x1b[A\n", ["one", "two"]),
                         ("text", "one"))

    def test_history_editing_recall_does_not_mutate_history(self):
        hist = ["abc"]
        self.assertEqual(feed(b"\x1b[AX\n", hist), ("text", "abcX"))
        self.assertEqual(hist, ["abc"])

    def test_ctrl_p_ctrl_n_alias(self):
        self.assertEqual(feed(b"\x10\n", ["old"]), ("text", "old"))
        self.assertEqual(feed(b"\x10\x0e\n", ["old"]), ("text", ""))

    def test_history_multiline_entry_recalled_as_collapsed_paste(self):
        fd = editor_input(b"\x1b[A\n")
        try:
            editor = autopaste.LineEditor(fd, history=["a\nb"])
            with redirect_std() as (out, _):
                kind, payload = editor.read("> ")
        finally:
            os.close(fd)
        self.assertEqual((kind, payload), ("text", "a\nb"))
        self.assertIn("历史", out.getvalue())
        self.assertIn("回车重发", out.getvalue())

    def test_history_typing_while_paste_active_is_ignored(self):
        self.assertEqual(feed(b"\x1b[200~pasted\x1b[201~X\n"), ("text", "pasted"))
        self.assertEqual(feed(b"\x1b[AX\n", ["a\nb"]), ("text", "a\nb"))

    def test_history_ctrl_u_clears_paste_before_buffer(self):
        self.assertEqual(feed(b"\x1b[200~pasted\x1b[201~\x15z\n"),
                         ("text", "z"))

    def test_history_ctrl_w_clears_paste(self):
        self.assertEqual(feed(b"\x1b[200~pasted\x1b[201~\x17q\n"),
                         ("text", "q"))


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.path = os.path.join(self.dir, "autopaste", "history")

    def test_history_persists_across_sessions_with_mode_0600(self):
        h = autopaste.History(self.path)
        h.add("first")
        h.add("second")
        self.assertIsNone(h.disabled_reason)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(
            os.stat(os.path.dirname(self.path)).st_mode) & 0o077, 0)
        again = autopaste.History(self.path)
        self.assertEqual(again.entries, ["first", "second"])
        self.assertEqual(again.skipped, 0)

    def test_history_skips_empty_and_adjacent_duplicates(self):
        h = autopaste.History(None)
        self.assertFalse(h.add(""))
        self.assertFalse(h.add("   "))
        self.assertTrue(h.add("x"))
        self.assertFalse(h.add("x"))
        self.assertTrue(h.add("y"))
        self.assertEqual(h.entries, ["x", "y"])

    def test_history_skips_entry_over_8kib_or_200_lines(self):
        h = autopaste.History(None)
        self.assertFalse(h.add("a" * (autopaste.HIST_MAX_ENTRY_BYTES + 1)))
        self.assertFalse(h.add("\n".join(["x"] * 201)))
        self.assertTrue(h.add("a" * autopaste.HIST_MAX_ENTRY_BYTES))
        self.assertTrue(h.add("\n".join(["x"] * 200)))
        self.assertEqual(len(h.entries), 2)

    def test_history_corrupt_lines_skipped_others_loaded(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        lines = [
            json.dumps({"v": 1, "t": "good"}),
            "not json",
            json.dumps({"v": 2, "t": "bad-version"}),
            json.dumps({"v": 1, "t": 5}),
            json.dumps(["not", "dict"]),
            json.dumps({"v": 1, "t": "x" * (autopaste.HIST_MAX_ENTRY_BYTES + 1)}),
            json.dumps({"v": 1, "t": "tail"}),
        ]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(self.path, 0o600)
        h = autopaste.History(self.path)
        self.assertEqual(h.entries, ["good", "tail"])
        self.assertEqual(h.skipped, 5)
        self.assertIsNone(h.disabled_reason)

    def test_history_compacts_when_file_exceeds_limit(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        blob = "z" * 3000
        with open(self.path, "w", encoding="utf-8") as f:
            for i in range(400):
                f.write(json.dumps({"v": 1, "t": f"{i}:{blob}"}) + "\n")
        os.chmod(self.path, 0o600)
        self.assertGreater(os.path.getsize(self.path),
                           autopaste.HIST_MAX_FILE_BYTES)
        h = autopaste.History(self.path)
        self.assertLessEqual(len(h.entries), autopaste.HIST_MAX_ENTRIES)
        self.assertEqual(h.entries[-1], "399:" + blob)
        self.assertLessEqual(os.path.getsize(self.path),
                             autopaste.HIST_MAX_FILE_BYTES)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_history_disabled_writes_nothing(self):
        h = autopaste.History(None)
        self.assertTrue(h.add("secret"))
        self.assertEqual(h.entries, ["secret"])
        self.assertIsNone(h.path)
        self.assertEqual(os.listdir(self.dir), [])

    def test_history_default_path_follows_xdg_state_home(self):
        path = autopaste.default_history_path({"XDG_STATE_HOME": "/tmp/xdg"})
        self.assertEqual(path, "/tmp/xdg/autopaste/history")
        path = autopaste.default_history_path({"HOME": "/home/u"})
        self.assertEqual(path, "/home/u/.local/state/autopaste/history")

    def test_history_rejects_relative_xdg_state_home(self):
        self.assertIsNone(autopaste.default_history_path(
            {"XDG_STATE_HOME": "rel"}))
        self.assertIsNone(autopaste.default_history_path({}))

    def test_history_chmods_loose_permissions_and_warns(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, "t": "x"}) + "\n")
        os.chmod(self.path, 0o644)
        os.chmod(os.path.dirname(self.path), 0o777)
        h = autopaste.History(self.path)
        self.assertIsNone(h.disabled_reason)
        self.assertEqual(h.entries, ["x"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(
            os.stat(os.path.dirname(self.path)).st_mode), 0o700)

    def test_history_disables_when_mode_cannot_be_fixed(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, "t": "x"}) + "\n")
        os.chmod(self.path, 0o600)
        with redirect_std():
            h = autopaste.History(self.path, euid=os.geteuid() + 1)
        self.assertIsNotNone(h.disabled_reason)
        self.assertEqual(h.entries, [])
        # 禁用后只写内存，绝不碰文件
        before = os.path.getsize(self.path)
        self.assertTrue(h.add("new"))
        self.assertEqual(os.path.getsize(self.path), before)

    def test_history_disables_on_symlinked_file(self):
        target = os.path.join(self.dir, "outside")
        with open(target, "w", encoding="utf-8") as f:
            f.write(json.dumps({"v": 1, "t": "leak"}) + "\n")
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        os.symlink(target, self.path)
        with redirect_std():
            h = autopaste.History(self.path)
        self.assertIsNotNone(h.disabled_reason)
        self.assertEqual(h.entries, [])

    def test_history_symlinked_directory_disabled(self):
        outside = os.path.join(self.dir, "outside_dir")
        os.makedirs(outside)
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        os.rmdir(os.path.dirname(self.path))
        os.symlink(outside, os.path.dirname(self.path))
        with redirect_std():
            h = autopaste.History(self.path)
        self.assertIsNotNone(h.disabled_reason)
        h.add("x")
        self.assertEqual(os.listdir(outside), [])

    def test_history_symlinked_ancestor_disabled(self):
        """$XDG_STATE_HOME 本身被换成符号链接也必须 fail-closed（R_04 M3）。"""
        outside = os.path.join(self.dir, "outside_dir")
        os.makedirs(outside)
        link = os.path.join(self.dir, "state")
        os.symlink(outside, link)
        path = os.path.join(link, "autopaste", "history")
        with redirect_std():
            h = autopaste.History(path)
        self.assertIsNotNone(h.disabled_reason)
        self.assertIn("历史目录", h.disabled_reason)
        self.assertTrue(h.add("x"))
        self.assertEqual(os.listdir(outside), [])

    def test_history_write_refuses_symlinked_ancestor(self):
        """目录在初始化后、首次写入前被换成符号链接，也不能写穿。"""
        h = autopaste.History(self.path)
        self.assertIsNone(h.disabled_reason)
        outside = os.path.join(self.dir, "outside_dir")
        os.makedirs(outside)
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        os.rmdir(os.path.dirname(self.path))
        os.symlink(outside, os.path.dirname(self.path))
        with redirect_std():
            self.assertTrue(h.add("x"))
        self.assertIsNotNone(h.disabled_reason)
        self.assertEqual(os.listdir(outside), [])


class ReplHistoryWiringTests(unittest.TestCase):
    def test_repl_records_submitted_text(self):
        import test_repl
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "state", "autopaste", "history")
        hist = autopaste.History(path)
        typer = test_repl.ReplTyper()
        cfg = autopaste.Settings(sleep=0.0)
        code, _, _ = test_repl.run_repl(b"hi\n:q\n", typer=typer, cfg=cfg,
                                       history=hist)
        self.assertEqual(code, 0)
        self.assertEqual(hist.entries, ["hi", ":q"])
        self.assertEqual(autopaste.History(path).entries, ["hi", ":q"])

    def test_repl_invalid_payload_not_recorded(self):
        import test_repl
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        hist = autopaste.History(os.path.join(tmp.name, "h", "history"))
        typer = test_repl.ReplTyper()
        cfg = autopaste.Settings(sleep=0.0)
        test_repl.run_repl(
            b"\x1b[200~a\x01b\x1b[201~\n:q\n", typer=typer, cfg=cfg,
            history=hist)
        self.assertEqual(hist.entries, [":q"])


if __name__ == "__main__":
    unittest.main()
