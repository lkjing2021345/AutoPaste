# -*- coding: utf-8 -*-
"""CLI：参数默认值 + main() 的退出码（全部打桩，不连接 X11）。"""

import os
import unittest
from unittest import mock

import autopaste
from support import redirect_std, temp_home


class BuildParserTests(unittest.TestCase):
    def test_defaults(self):
        args = autopaste.build_parser().parse_args([])
        self.assertEqual(args.text, [])
        self.assertIsNone(args.delay)      # 默认值由 resolve_settings 填回
        self.assertIsNone(args.backend)
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
    def _main(self, argv, env, mock_typer=True):
        self.typer_cls = None
        with temp_home(), \
                mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                redirect_std() as (out, err):
            if mock_typer:
                with mock.patch.object(autopaste, "Typer") as typer_cls, \
                        mock.patch.object(autopaste.shutil, "which",
                                          return_value=None):
                    typer_cls.return_value.resolvable.return_value = []
                    typer_cls.return_value.capabilities.return_value = {
                        "name": "x11", "can_focus_info": True,
                        "can_paste_mode": False, "unicode": True,
                        "needs_remap": True, "combos": True,
                        "per_char_delay": True, "daemon": False}
                    typer_cls.return_value.focus_info.return_value = None
                    self.typer_cls = typer_cls
                    code = autopaste.main(argv)
            else:
                code = autopaste.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_delay_out_of_range(self):
        code, _, err = self._main(["--delay", "99", "hi"],
                                 {"DISPLAY": ":0"})
        self.assertEqual(code, 1)
        self.assertIn("字符间隔", err)

    def test_missing_backend_exits_2(self):
        # 无 X11、无 Wayland 工具 -> 环境类拒绝，退出码 2 且给出安装提示
        with mock.patch.dict(os.environ, clear=True), \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.object(autopaste.shutil, "which",
                                  return_value=None), \
                redirect_std() as (_, err):
            code = autopaste.main(["hi"])
        self.assertEqual(code, 2)
        self.assertIn("wtype", err.getvalue())

    def test_control_chars_rejected(self):
        code, _, err = self._main(["a\x01b"], {"DISPLAY": ":0"})
        self.assertEqual(code, 1)
        self.assertIn("控制字符", err)

    def test_dry_run_exits_zero(self):
        code, out, _ = self._main(["--dry-run", "hi"], {"DISPLAY": ":0"})
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)

    def test_dry_run_sends_no_injection(self):
        # 假绿防线：dry-run 必须在 return 之前一次都不调用注入方法
        code, out, _ = self._main(["--dry-run", "hi"], {"DISPLAY": ":0"})
        self.assertEqual(code, 0)
        typer = self.typer_cls.return_value
        typer.type_text.assert_not_called()
        typer.type_char.assert_not_called()
        typer.press.assert_not_called()
        # 可映射性检查仍然要跑（这是 dry-run 的价值所在）
        typer.resolvable.assert_called_once_with("hi")
        self.assertIn("未发送任何按键", out)

    def test_root_rejected(self):
        with temp_home(), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(os, "geteuid", return_value=0,
                                  create=True), \
                redirect_std() as (_, err):
            code = autopaste.main(["hi"])
        self.assertEqual(code, 2)
        self.assertIn("root", err.getvalue())


if __name__ == "__main__":
    unittest.main()
