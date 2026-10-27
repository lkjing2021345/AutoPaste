# -*- coding: utf-8 -*-
"""LineEditor：用 os.pipe() 伪造终端输入（不需要 tty）。"""

import os
import unittest
from unittest import mock

import autopaste
from support import editor_input, redirect_std


def feed(data: bytes):
    """把 data 喂给一个新的 LineEditor 并返回 (kind, payload)。"""
    fd = editor_input(data)
    try:
        editor = autopaste.LineEditor(fd)
        with redirect_std():
            return editor.read("> ")
    finally:
        os.close(fd)


class LineEditorTests(unittest.TestCase):
    def test_plain_text_submit(self):
        self.assertEqual(feed(b"hello\n"), ("text", "hello"))

    def test_carriage_return_submits(self):
        self.assertEqual(feed(b"hi\r"), ("text", "hi"))

    def test_backspace(self):
        self.assertEqual(feed(b"abc\x7f\n"), ("text", "ab"))
        self.assertEqual(feed(b"abc\x08\n"), ("text", "ab"))

    def test_arrow_left_right_and_insert(self):
        # abc -> 左 左 -> 在 'b' 前插 X
        self.assertEqual(feed(b"abc\x1b[D\x1b[DX\n"), ("text", "aXbc"))
        self.assertEqual(feed(b"ac\x1b[Db\n"), ("text", "abc"))
        self.assertEqual(feed(b"ac\x1b[Db\x1b[C\n"), ("text", "abc"))

    def test_home_end(self):
        self.assertEqual(feed(b"abc\x1b[HX\x1b[FY\n"), ("text", "XabcY"))
        self.assertEqual(feed(b"abc\x01X\x05Y\n"), ("text", "XabcY"))

    def test_delete_key(self):
        # 光标移到行首后按 Delete 删掉 'a'
        self.assertEqual(feed(b"abc\x1b[D\x1b[D\x1b[D\x1b[3~\n"),
                         ("text", "bc"))

    def test_ctrl_u_clears_line(self):
        self.assertEqual(feed(b"abcd\x15zz\n"), ("text", "zz"))

    def test_ctrl_w_deletes_word(self):
        self.assertEqual(feed(b"foo bar\x17\n"), ("text", "foo "))

    def test_ctrl_c_interrupts(self):
        self.assertEqual(feed(b"abc\x03"), ("interrupt", None))

    def test_ctrl_d_eof_on_empty(self):
        self.assertEqual(feed(b"\x04"), ("eof", None))

    def test_ctrl_d_ignored_when_not_empty(self):
        self.assertEqual(feed(b"ab\x04cd\n"), ("text", "abcd"))

    def test_eof_returns_eof(self):
        self.assertEqual(feed(b""), ("eof", None))

    def test_bracketed_paste(self):
        data = b"\x1b[200~line1\r\nline2\rline3\x1b[201~\n"
        self.assertEqual(feed(data), ("text", "line1\nline2\nline3"))

    def test_paste_preview_not_in_payload(self):
        fd = editor_input(b"\x1b[200~pasted\x1b[201~\n")
        try:
            editor = autopaste.LineEditor(fd)
            with redirect_std() as (out, _):
                kind, payload = editor.read("> ")
        finally:
            os.close(fd)
        self.assertEqual((kind, payload), ("text", "pasted"))
        self.assertIn("已粘贴", out.getvalue())

    def test_oversized_paste_truncated(self):
        limit = 16
        payload = b"0123456789abcdefghij"
        data = b"\x1b[200~" + payload + b"\x1b[201~\n"
        with mock.patch.object(autopaste, "MAX_PAYLOAD", limit):
            kind, text = feed(data)
        self.assertEqual(kind, "text")
        # _read_paste 在超过上限后停止读取，最多保留 limit+1 个字节
        self.assertEqual(text, payload[:limit + 1].decode())

    def test_utf8_input(self):
        self.assertEqual(feed("中文\n".encode("utf-8")), ("text", "中文"))

    def test_backspace_after_paste_clears_paste(self):
        data = b"\x1b[200~pasted\x1b[201~\x7f\n"
        self.assertEqual(feed(data), ("text", ""))


if __name__ == "__main__":
    unittest.main()
