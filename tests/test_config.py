# -*- coding: utf-8 -*-
"""配置文件：扁平 TOML 子集、安全键拒绝、命令行 > 配置 > 默认。"""

import io
import os
import stat
import tempfile
import unittest
from unittest import mock

import autopaste
from support import redirect_std, temp_home


def write_config(root, text, *, mode=0o600, name="config.toml"):
    d = os.path.join(root, "config", "autopaste")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)
    return path


def caps(**over):
    base = {"name": "x11", "can_focus_info": True,
            "can_paste_mode": False, "unicode": True,
            "needs_remap": True, "combos": True,
            "per_char_delay": True, "daemon": False,
            "window_control": False}
    base.update(over)
    return base


class ParseConfigTests(unittest.TestCase):
    def test_config_parses_flat_toml_subset(self):
        cfg = autopaste.parse_config_text('''
# 注释
delay = 0.02          # 行尾注释
sleep = 2.5
backend = "wtype"
auto_enter = false
paste_mode = true
''')
        self.assertEqual(cfg.delay, 0.02)
        self.assertEqual(cfg.sleep, 2.5)
        self.assertEqual(cfg.backend, "wtype")
        self.assertIs(cfg.auto_enter, False)
        self.assertIs(cfg.paste_mode, True)

    def test_config_allows_optional_autopaste_section(self):
        cfg = autopaste.parse_config_text('''
[autopaste]
delay = 0.01
''')
        self.assertEqual(cfg.delay, 0.01)

    def test_config_hash_inside_quotes_is_not_comment(self):
        cfg = autopaste.parse_config_text('backend = "auto" # ok')
        self.assertEqual(cfg.backend, "auto")

    def test_config_rejects_forbidden_security_keys(self):
        for key in ("allow_root", "allow_control", "yes",
                    "no_restore_clipboard"):
            with self.assertRaises(autopaste.ConfigError) as ctx:
                autopaste.parse_config_text(f"{key} = true\n")
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn(key, str(ctx.exception))

    def test_config_rejects_dashed_security_key_as_invalid_name(self):
        with self.assertRaises(autopaste.ConfigError) as ctx:
            autopaste.parse_config_text("allow-root = true\n")
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("非法键名", str(ctx.exception))

    def test_config_unknown_key_warns_but_ignored(self):
        log = autopaste.Log(err=io.StringIO())
        with mock.patch.object(autopaste, "LOG", log):
            cfg = autopaste.parse_config_text("future_knob = 1\n")
        self.assertIn("未知配置项 future_knob", log._err.getvalue())
        self.assertEqual(cfg.provided(), [])

    def test_config_invalid_value_exits_2_with_path_and_line(self):
        with self.assertRaises(autopaste.ConfigError) as ctx:
            autopaste.parse_config_text(
                "delay = 0.01\nbackend = \"nope\"\n", path="x.toml")
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("x.toml:2", str(ctx.exception))

    def test_config_rejects_nan_inf_and_arrays(self):
        for text in ("delay = nan\n", "delay = inf\n", "delay = [1, 2]\n",
                     "delay = 2020-01-01\n", "delay = true\n"):
            with self.assertRaises(autopaste.ConfigError):
                autopaste.parse_config_text(text)

    def test_config_rejects_duplicate_key_and_bad_section(self):
        with self.assertRaises(autopaste.ConfigError):
            autopaste.parse_config_text("delay = 1\ndelay = 2\n")
        with self.assertRaises(autopaste.ConfigError):
            autopaste.parse_config_text("[other]\ndelay = 1\n")

    def test_config_rejects_bad_escape(self):
        with self.assertRaises(autopaste.ConfigError):
            autopaste.parse_config_text('backend = "au\\xto"\n')

    def test_config_single_quoted_string_is_literal(self):
        cfg = autopaste.parse_config_text("backend = 'wtype'")
        self.assertEqual(cfg.backend, "wtype")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_config_missing_default_file_uses_defaults(self):
        with temp_home() as home:
            self.assertIsNone(autopaste.load_config())
            path = autopaste.default_config_path()
            self.assertTrue(path.startswith(home))
            self.assertFalse(os.path.exists(path))

    def test_config_explicit_missing_file_exits_2(self):
        with self.assertRaises(autopaste.ConfigError) as ctx:
            autopaste.load_config(os.path.join(self.tmp.name, "nope.toml"))
        self.assertEqual(ctx.exception.code, 2)

    def test_config_rejects_group_or_other_writable_file(self):
        path = write_config(self.tmp.name, "delay = 0.01\n", mode=0o666)
        with self.assertRaises(autopaste.ConfigError):
            autopaste.load_config(path)
        os.chmod(path, 0o642)
        with self.assertRaises(autopaste.ConfigError):
            autopaste.load_config(path)

    def test_config_rejects_symlinked_file(self):
        real = write_config(self.tmp.name, "delay = 0.01\n")
        link = os.path.join(self.tmp.name, "link.toml")
        os.symlink(real, link)
        with self.assertRaises(autopaste.ConfigError):
            autopaste.load_config(link)

    def test_config_rejects_wrong_owner(self):
        path = write_config(self.tmp.name, "delay = 0.01\n")
        with self.assertRaises(autopaste.ConfigError):
            autopaste.load_config(path, euid=os.geteuid() + 1)

    def test_config_relative_xdg_config_home_is_ignored(self):
        self.assertIsNone(autopaste.default_config_path(
            {"XDG_CONFIG_HOME": "rel"}))

    def test_config_default_path_follows_xdg(self):
        self.assertEqual(
            autopaste.default_config_path({"XDG_CONFIG_HOME": "/tmp/c"}),
            "/tmp/c/autopaste/config.toml")


class ResolveSettingsTests(unittest.TestCase):
    def args(self, argv):
        return autopaste.build_parser().parse_args(argv)

    def test_cli_delay_overrides_config(self):
        cfg = autopaste.Config()
        cfg.delay = 0.02
        R = autopaste.resolve_settings(self.args(["-d", "0.01", "hi"]), cfg)
        self.assertEqual(R["delay"], (0.01, "cli"))

    def test_config_delay_applied_when_cli_absent(self):
        cfg = autopaste.Config()
        cfg.delay = 0.02
        R = autopaste.resolve_settings(self.args(["hi"]), cfg)
        self.assertEqual(R["delay"], (0.02, "config"))

    def test_cli_sleep_overrides_config_and_repl_default_kept(self):
        cfg = autopaste.Config()
        cfg.sleep = 1.5
        R = autopaste.resolve_settings(self.args(["-s", "0.5", "hi"]), cfg)
        self.assertEqual(R["sleep"], (0.5, "cli"))
        R = autopaste.resolve_settings(self.args(["hi"]), cfg)
        self.assertEqual(R["sleep"], (1.5, "config"))
        R = autopaste.resolve_settings(self.args(["hi"]), None)
        self.assertEqual(R["sleep"], (None, "default"))

    def test_cli_backend_overrides_config(self):
        cfg = autopaste.Config()
        cfg.backend = "wtype"
        R = autopaste.resolve_settings(
            self.args(["--backend", "x11", "hi"]), cfg)
        self.assertEqual(R["backend"], ("x11", "cli"))

    def test_config_backend_applied_when_cli_absent(self):
        cfg = autopaste.Config()
        cfg.backend = "wtype"
        R = autopaste.resolve_settings(self.args(["hi"]), cfg)
        self.assertEqual(R["backend"], ("wtype", "config"))


class MainConfigTests(unittest.TestCase):
    def _run(self, argv, *, config=None, mode=0o600, symlink=False):
        with temp_home() as home:
            if config is not None:
                if symlink:
                    real = os.path.join(home, "real.toml")
                    with open(real, "w", encoding="utf-8") as f:
                        f.write(config)
                    d = os.path.join(home, "config", "autopaste")
                    os.makedirs(d, mode=0o700)
                    os.symlink(real, os.path.join(d, "config.toml"))
                else:
                    write_config(home, config, mode=mode)
            log = autopaste.Log(autopaste.Log.VERBOSE, err=io.StringIO())
            with mock.patch.object(autopaste, "Typer") as typer_cls, \
                    mock.patch.object(os, "geteuid", return_value=(os.geteuid() or 1),
                                      create=True), \
                    mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                    mock.patch.object(autopaste, "LOG", log), \
                    redirect_std() as (out, _):
                typer_cls.return_value.capabilities.return_value = caps()
                typer_cls.return_value.resolvable.return_value = []
                typer_cls.return_value.focus_info.return_value = None
                code = autopaste.main(argv)
            return code, out.getvalue(), log._err.getvalue(), typer_cls

    def test_main_applies_config_delay_and_verbose_source(self):
        code, _, err, typer_cls = self._run(
            ["--verbose", "--dry-run", "hi"],
            config='delay = 0.02\nbackend = "wtype"\n')
        self.assertEqual(code, 0)
        self.assertIn("delay=0.02（配置）", err)
        self.assertIn("backend=wtype（配置）", err)
        typer_cls.assert_called_with(backend="wtype", delay=0.02)

    def test_cli_overrides_config_in_main(self):
        code, _, err, _ = self._run(
            ["--verbose", "--dry-run", "-d", "0.008", "hi"],
            config="delay = 0.02\n")
        self.assertEqual(code, 0)
        self.assertIn("delay=0.008（命令行）", err)

    def test_bad_config_exits_2_before_touching_backend(self):
        code, _, err, typer_cls = self._run(
            ["--dry-run", "hi"], config="allow_root = true\n")
        self.assertEqual(code, 2)
        self.assertIn("allow_root", err)
        typer_cls.assert_not_called()

    def test_no_config_ignores_broken_file(self):
        code, _, _, _ = self._run(
            ["--no-config", "--dry-run", "hi"], config="this is not toml\n")
        self.assertEqual(code, 0)

    def test_explicit_missing_config_exits_2(self):
        code, _, err, _ = self._run(
            ["--config", "/nonexistent/autopaste.toml", "--dry-run", "hi"])
        self.assertEqual(code, 2)
        self.assertIn("配置文件不存在", err)

    def test_insane_config_permissions_exit_2(self):
        code, _, err, typer_cls = self._run(
            ["--dry-run", "hi"], config="delay = 0.01\n", mode=0o666)
        self.assertEqual(code, 2)
        self.assertIn("可写", err)
        typer_cls.assert_not_called()

    def test_repl_t_source_labels(self):
        import test_repl
        cfg = autopaste.Settings(sleep=1.5, delay=0.02,
                                 sleep_src="config", delay_src="cli")
        _, out, _ = test_repl.run_repl(b":t\n:q\n", typer=test_repl.ReplTyper(),
                                       cfg=cfg)
        self.assertIn("倒计时=1.5s（配置）", out)
        self.assertIn("字符间隔=0.02s（命令行）", out)

    def test_repl_uses_config_auto_enter_and_sleep(self):
        import test_repl
        rfd, wfd = os.pipe()
        os.write(wfd, b":t\n:q\n")
        os.close(wfd)
        raw = os.fdopen(rfd, "r")
        self.addCleanup(raw.close)
        with temp_home() as home:
            write_config(home, "auto_enter = true\nsleep = 1.5\n")
            typer_inst = test_repl.ReplTyper()
            with mock.patch.object(autopaste, "Typer",
                                   return_value=typer_inst), \
                    mock.patch.object(os, "geteuid", return_value=(os.geteuid() or 1),
                                      create=True), \
                    mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                    mock.patch.object(autopaste.sys, "stdin",
                                      test_repl.FakeTTY(raw)), \
                    mock.patch.object(autopaste.termios, "tcgetattr",
                                      return_value=["old"]), \
                    mock.patch.object(autopaste.termios, "tcsetattr"), \
                    mock.patch.object(autopaste.tty, "setcbreak"), \
                    redirect_std() as (out, _):
                code = autopaste.main(["--repl"])
        self.assertEqual(code, 0)
        self.assertIn("自动回车=开", out.getvalue())
        self.assertIn("倒计时=1.5s（配置）", out.getvalue())

    def test_config_symlink_rejected_by_main(self):
        code, _, err, _ = self._run(
            ["--dry-run", "hi"], config="delay = 0.01\n", symlink=True)
        self.assertEqual(code, 2)
        self.assertIn("无法读取配置文件", err)


if __name__ == "__main__":
    unittest.main()
