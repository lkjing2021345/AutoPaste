# -*- coding: utf-8 -*-
"""文档与代码的防漂移校验：参数表、能力矩阵、链接、退出码、版本。"""

import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import autopaste                      # noqa: E402
import gen_cli_docs                   # noqa: E402

#: 外部工具的 flag（出现在安装/故障排查示例里，不是本程序的参数）。
#: 来源：pip/pipx、systemctl、xclip/xsel、wl-clipboard、ydotool。
EXTERNAL_FLAGS = {
    "--no-deps", "--tags", "--upgrade",          # pip / pipx / git
    "--force", "--now", "--user",                # pipx / systemctl
    "--clipboard", "--selection", "--output", "--input",  # xclip / xsel
    "--list-types", "--no-newline",              # wl-paste
    "--key-delay", "--escape",                   # ydotool
    "--check",                                   # tools/gen_cli_docs.py
}

DOC_FILES = [ROOT / "README.md", ROOT / "README.en.md",
             ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
DOC_FILES += sorted((ROOT / "docs").glob("*.md"))


def _real_flags():
    parser = autopaste.build_parser()
    return {s for a in parser._actions for s in a.option_strings}


def _read(path):
    return path.read_text(encoding="utf-8")


def _auto_block(text, name):
    begin = gen_cli_docs.BEGIN.format(name)
    end = gen_cli_docs.END.format(name)
    start = text.index(begin) + len(begin)
    return text[start:text.index(end)].strip("\n")


class CliDocTests(unittest.TestCase):
    def test_cli_doc_in_sync(self):
        problems = gen_cli_docs.check()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_capability_matrix_matches_code(self):
        body = _auto_block(_read(ROOT / "docs" / "backends.md"), "matrix")
        self.assertEqual(body, "\n".join(gen_cli_docs.matrix_rows()))

    def test_documented_flags_exist(self):
        real = _real_flags() | EXTERNAL_FLAGS
        missing = []
        for path in DOC_FILES:
            for flag in re.findall(r"--[a-z][a-z0-9-]+", _read(path)):
                if flag not in real:
                    missing.append(f"{path.relative_to(ROOT)}: {flag}")
        self.assertEqual(missing, [], "文档提到了不存在的参数")

    def test_markdown_links_resolve(self):
        pattern = re.compile(r"\]\(([^)\s]+)\)")
        broken = []
        for path in DOC_FILES:
            for target in pattern.findall(_read(path)):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                rel = target.split("#", 1)[0]
                if not rel:
                    continue
                if not (path.parent / rel).exists():
                    broken.append(f"{path.relative_to(ROOT)} -> {target}")
        self.assertEqual(broken, [], "存在无法解析的相对链接")

    def test_all_docs_linked_from_index(self):
        readme = _read(ROOT / "README.md")
        index = _read(ROOT / "docs" / "README.md")
        for path in sorted((ROOT / "docs").glob("*.md")):
            self.assertIn(path.name, readme, f"{path.name} 未从 README 链接")
            self.assertIn(path.name, index, f"{path.name} 未从 docs/README 链接")

    def test_no_absolute_snapshot_links(self):
        for path in DOC_FILES:
            self.assertNotIn("raw.githubusercontent.com", _read(path),
                             f"{path.relative_to(ROOT)} 使用了绝对快照链接")


class ExitCodeDocTests(unittest.TestCase):
    def _documented_codes(self):
        text = _read(ROOT / "docs" / "cli.md")
        section = text.split("## 退出码", 1)[1].split("\n## ", 1)[0]
        return {int(n) for n in re.findall(r"^\| (\d+) \|", section, re.M)}

    def test_exit_codes_documented(self):
        codes = {int(n) for n in
                 re.findall(r"return (\d+)",
                            inspect.getsource(autopaste.main))}
        for cls in (autopaste.Abort, autopaste.BackendError,
                    autopaste.BackendUnavailable,
                    autopaste.WindowUnavailable,
                    autopaste.WindowCancelled,
                    autopaste.ClipboardUnsafe,
                    autopaste.ConfigError):
            code = getattr(cls, "code", None)
            if code is not None:
                codes.add(code)
        self.assertEqual(self._documented_codes(), codes)

    def test_argparse_and_interrupt_paths_documented(self):
        text = _read(ROOT / "docs" / "cli.md")
        section = text.split("## 退出码", 1)[1].split("\n## ", 1)[0]
        self.assertIn("argparse", section)
        self.assertIn("130", section)
        self.assertIn("REPL 内用 `--paste-mode`", section)


class VersionTests(unittest.TestCase):
    def test_version_single_source(self):
        changelog = _read(ROOT / "CHANGELOG.md")
        first = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
        self.assertIsNotNone(first, "CHANGELOG 缺少已发布版本标题")
        self.assertEqual(autopaste.__version__, first.group(1))

        source = _read(ROOT / "autopaste.py")
        self.assertEqual(len(re.findall(r"^__version__", source, re.M)), 1)

        pyproject = _read(ROOT / "pyproject.toml")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('attr = "autopaste.__version__"', pyproject)

    def test_docs_do_not_hardcode_another_version(self):
        # 只查“发布版本”会出现的文件；docs/backends.md 的 ydotool ≥1.0.4
        # 是上游工具版本，不是本程序的第二版本源。
        for path in (ROOT / "README.md", ROOT / "README.en.md",
                     ROOT / "docs" / "cli.md", ROOT / "pyproject.toml"):
            text = _read(path)
            for found in re.findall(r"\b\d+\.\d+\.\d+\b", text):
                self.assertEqual(found, autopaste.__version__,
                                 f"{path.relative_to(ROOT)} 写死了其他版本号")


class StructureTests(unittest.TestCase):
    def test_readme_length_budget(self):
        self.assertLessEqual(len(_read(ROOT / "README.md").splitlines()), 95)
        self.assertLessEqual(
            len(_read(ROOT / "README.en.md").splitlines()), 50)

    def test_no_stale_claims(self):
        stale = ["Typer._tap", "固定带 `-e 0`", "固定传 `-e 0`", "按上限截断读取"]
        for path in DOC_FILES:
            text = _read(path)
            for needle in stale:
                self.assertNotIn(needle, text,
                                 f"{path.relative_to(ROOT)} 仍在说 {needle}")
        for path in (ROOT / "README.md", ROOT / "README.en.md",
                     ROOT / "pyproject.toml"):
            self.assertNotIn("仅 X11", _read(path))
            self.assertNotRegex(_read(path), r"X11 窗口（XTEST 键盘注入")


if __name__ == "__main__":
    unittest.main()
