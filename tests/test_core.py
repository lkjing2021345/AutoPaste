# -*- coding: utf-8 -*-
"""纯函数：payload 校验、文件校验、数字/预览/组合键判定。"""

import os
import tempfile
import unittest

import autopaste


class ValidatePayloadTests(unittest.TestCase):
    def test_rejects_control_chars(self):
        for bad in ("a\x01b", "x\x1b[31m", "a\x00", "\x7f"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(autopaste.Abort):
                    autopaste.validate_payload(bad)

    def test_allows_newline_and_tab(self):
        autopaste.validate_payload("a\nb\tc")   # 不抛异常

    def test_allow_control_bypasses(self):
        autopaste.validate_payload("a\x1bb", allow_control=True)

    def test_length_limit(self):
        autopaste.validate_payload("x" * autopaste.MAX_PAYLOAD)
        with self.assertRaises(autopaste.Abort):
            autopaste.validate_payload("x" * (autopaste.MAX_PAYLOAD + 1))

    def test_error_mentions_codepoint(self):
        with self.assertRaises(autopaste.Abort) as ctx:
            autopaste.validate_payload("\x1b")
        self.assertIn("U+001B", str(ctx.exception))


class CheckRegularFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_regular_file_ok(self):
        path = os.path.join(self.tmp.name, "note.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("hello")
        autopaste.check_regular_file(path)     # 不抛异常

    def test_directory_rejected(self):
        with self.assertRaises(autopaste.Abort):
            autopaste.check_regular_file(self.tmp.name)

    def test_fifo_rejected(self):
        path = os.path.join(self.tmp.name, "pipe")
        os.mkfifo(path)
        with self.assertRaises(autopaste.Abort):
            autopaste.check_regular_file(path)

    def test_missing_file_rejected(self):
        with self.assertRaises(autopaste.Abort):
            autopaste.check_regular_file(
                os.path.join(self.tmp.name, "no-such-file"))

    def test_oversized_file_rejected(self):
        path = os.path.join(self.tmp.name, "big.bin")
        with open(path, "wb") as f:
            f.truncate(autopaste.MAX_PAYLOAD + 1)
        with self.assertRaises(autopaste.Abort):
            autopaste.check_regular_file(path)


class NumTests(unittest.TestCase):
    def test_accepts_bounds(self):
        self.assertEqual(autopaste._num("0", 0.0, 5.0, "间隔"), 0.0)
        self.assertEqual(autopaste._num("5", 0.0, 5.0, "间隔"), 5.0)

    def test_rejects_out_of_range(self):
        for raw in ("-0.1", "5.1", "1e9"):
            with self.subTest(raw=raw):
                with self.assertRaises(autopaste.Abort):
                    autopaste._num(raw, 0.0, 5.0, "间隔")

    def test_rejects_non_numeric(self):
        with self.assertRaises(autopaste.Abort):
            autopaste._num("abc", 0.0, 5.0, "间隔")


class EscPreviewTests(unittest.TestCase):
    def test_escapes_special_chars(self):
        self.assertEqual(autopaste._esc_preview("a\nb\tc\x01\x7f"),
                         "a\\nb\\tc\\x01\\x7f")

    def test_keeps_unicode(self):
        self.assertEqual(autopaste._esc_preview("中文 😀"), "中文 😀")

    def test_truncates(self):
        out = autopaste._esc_preview("abcdef", limit=3)
        self.assertTrue(out.startswith("abc"))
        self.assertIn("6", out)


class IsComboTests(unittest.TestCase):
    def test_true_for_modifier_combo(self):
        for item in ("ctrl+alt+t", "ctrl+shift+v", "SUPER+space"):
            with self.subTest(item=item):
                self.assertTrue(autopaste.is_combo(item))

    def test_false_for_plain_text(self):
        for item in ("1+1", "hello", "ctrl", "a+b"):
            with self.subTest(item=item):
                self.assertFalse(autopaste.is_combo(item))


class CharKeysymTests(unittest.TestCase):
    def test_latin1_uses_codepoint(self):
        self.assertEqual(autopaste._char_keysym("a"), ord("a"))

    def test_unicode_uses_x11_keysym(self):
        self.assertEqual(autopaste._char_keysym("中"),
                         0x01000000 + ord("中"))


if __name__ == "__main__":
    unittest.main()
