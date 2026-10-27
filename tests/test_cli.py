# -*- coding: utf-8 -*-
"""CLI：参数默认值 + main() 的退出码（全部打桩，不连接 X11）。"""

import os
import unittest
from unittest import mock

import autopaste
from support import redirect_std


class BuildParserTests(unittest.TestCase):
    def test_defaults(self):
        args = autopaste.build_parser().parse_args([])
        self.assertEqual(args.text, [])
        self.assertAlmostEqual(args.delay, 0.008)
        self.assertIsNone(args.sleep)
        self.assertIsNone(args.file)
        self.assertEqual(args.key, [])
        self.assertFalse(args.no_newline)
        self.assertFalse(args.repl)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.yes)
        self.assertFalse(args.allow_control)
        self.assertFalse(args.allow_root)

    def test_version_uses_module_version(self):
        with redirect_std() as (out, _):
            with self.assertRaises(SystemExit) as ctx:
                autopaste.build_parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(autopaste.__version__, out.getvalue())


class MainExitCodeTests(unittest.TestCase):
    def _main(self, argv, env):
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                redirect_std() as (out, err):
            typer_cls.return_value.resolvable.return_value = []
            code = autopaste.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_delay_out_of_range(self):
        code, _, err = self._main(["--delay", "99", "hi"],
                                 {"DISPLAY": ":0"})
        self.assertEqual(code, 1)
        self.assertIn("字符间隔", err)

    def test_missing_display(self):
        code, _, err = self._main(["hi"], {"DISPLAY": ""})
        self.assertEqual(code, 2)
        self.assertIn("DISPLAY", err)

    def test_control_chars_rejected(self):
        code, _, err = self._main(["a\x01b"], {"DISPLAY": ":0"})
        self.assertEqual(code, 1)
        self.assertIn("控制字符", err)

    def test_dry_run_exits_zero(self):
        with mock.patch.object(autopaste, "focused_window", return_value=None):
            code, out, _ = self._main(["--dry-run", "hi"], {"DISPLAY": ":0"})
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)

    def test_root_rejected(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(os, "geteuid", return_value=0,
                                  create=True), \
                redirect_std() as (_, err):
            code = autopaste.main(["hi"])
        self.assertEqual(code, 2)
        self.assertIn("root", err.getvalue())


if __name__ == "__main__":
    unittest.main()
