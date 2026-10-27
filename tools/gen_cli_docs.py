#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 ``build_parser()`` / ``Backend.capabilities()`` 生成文档的 AUTO 区块。

用法::

    python3 tools/gen_cli_docs.py            # 写回 docs/cli.md / docs/backends.md
    python3 tools/gen_cli_docs.py --check    # 只校验，有差异退出 1

AUTO 区块由成对的 HTML 注释标记界定，标记之间的内容禁止手改：

    <!-- BEGIN AUTO:flags -->
    <!-- END AUTO:flags -->
    <!-- BEGIN AUTO:matrix -->
    <!-- END AUTO:matrix -->

只依赖标准库；导入 autopaste 不会触碰 libX11（惰性 dlopen）。
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autopaste  # noqa: E402  （必须在 sys.path 调整之后）

CLI_DOC = ROOT / "docs" / "cli.md"
BACKENDS_DOC = ROOT / "docs" / "backends.md"

BEGIN = "<!-- BEGIN AUTO:{} -->"
END = "<!-- END AUTO:{} -->"

#: 用户能力列（can_paste_mode 是全 False 的内部占位，不展示）
MATRIX_COLUMNS = [
    ("can_focus_info", "看焦点"),
    ("unicode", "Unicode"),
    ("needs_remap", "重映射"),
    ("combos", "组合键"),
    ("per_char_delay", "每字符延时"),
    ("daemon", "守护进程"),
    ("window_control", "窗口定向"),
    ("escapes_supported", "转义支持"),
]

MATRIX_BACKENDS = [
    ("x11", autopaste.X11Backend),
    ("wtype", autopaste.WtypeBackend),
    ("ydotool", autopaste.YdotoolBackend),
]


def _action_value(action: argparse.Action) -> str | None:
    if isinstance(action, argparse._HelpAction):
        return None
    if isinstance(action, (argparse._StoreTrueAction,
                           argparse._StoreFalseAction,
                           argparse._StoreConstAction,
                           argparse._VersionAction)):
        return "—"
    if not action.option_strings:                 # positional
        if action.nargs in ("*", "+"):
            return f"{action.metavar or action.dest} …"
        return action.metavar or action.dest
    if action.choices:
        base = " / ".join(str(c) for c in action.choices)
    elif action.metavar:
        base = str(action.metavar)
    elif action.type is float:
        base = "浮点"
    elif action.type is int:
        base = "整数"
    else:
        base = action.dest
    if isinstance(action, argparse._AppendAction):
        base += "（可重复）"
    return base


def _help_text(action: argparse.Action) -> str:
    if isinstance(action, argparse._VersionAction):
        return "打印版本号后退出"
    text = (action.help or "").strip() or "—"
    return " ".join(text.split()).replace("|", "\\|")


def flag_rows() -> list[str]:
    parser = autopaste.build_parser()
    rows = ["| 参数 | 取值 | 说明 |", "|---|---|---|"]
    for action in (parser._get_positional_actions()
                   + parser._get_optional_actions()):
        names = ", ".join(
            f"`{s}`" for s in (action.option_strings or [action.dest]))
        value = _action_value(action)
        if value is None:                          # -h/--help
            continue
        rows.append(f"| {names} | {value} | {_help_text(action)} |")
    return rows


def matrix_rows() -> list[str]:
    header = ["| 后端 | " + " | ".join(label for _, label in MATRIX_COLUMNS) + " |"]
    header.append("|---" * (len(MATRIX_COLUMNS) + 1) + "|")
    rows = list(header)
    for name, cls in MATRIX_BACKENDS:
        obj = types.SimpleNamespace(name=name, escapes_supported=None)
        caps = cls.capabilities(obj)
        cells = []
        for key, _ in MATRIX_COLUMNS:
            value = caps.get(key)
            if value is True:
                cells.append("✔")
            elif value is False:
                cells.append("✘")
            elif key == "escapes_supported":
                cells.append("按版本探测（≥1.0.4）")
            else:
                cells.append("—")
        rows.append(f"| `{name}` | " + " | ".join(cells) + " |")
    return rows


def _replace_block(text: str, name: str, body: list[str]) -> str:
    begin, end = BEGIN.format(name), END.format(name)
    if begin not in text or end not in text:
        raise SystemExit(f"缺少 AUTO:{name} 标记（需要 {begin} … {end}）")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    return text[:start] + "\n" + "\n".join(body) + "\n" + text[stop:]


def render(path: Path, name: str, rows: list[str]) -> str:
    if not path.exists():
        raise SystemExit(f"文档不存在: {path}")
    return _replace_block(path.read_text(encoding="utf-8"), name, rows)


def expected() -> dict[Path, str]:
    return {
        CLI_DOC: render(CLI_DOC, "flags", flag_rows()),
        BACKENDS_DOC: render(BACKENDS_DOC, "matrix", matrix_rows()),
    }


def check() -> list[str]:
    """返回差异列表；空列表表示文档与代码一致。"""
    problems = []
    for path, want in expected().items():
        have = path.read_text(encoding="utf-8")
        if have != want:
            diff = "".join(difflib.unified_diff(
                have.splitlines(True), want.splitlines(True),
                fromfile=str(path), tofile=f"{path}（生成结果）"))
            problems.append(diff)
    return problems


def _self_check() -> None:
    """argparse 私有 API 变更时给出可读错误，而不是静默生成空表。"""
    parser = autopaste.build_parser()
    helptext = parser.format_help()
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if not action.option_strings:              # positional: dest 就是 metavar
            continue
        if not any(s in helptext for s in action.option_strings):
            raise SystemExit(
                "argparse 的私有 action 结构可能已变化（"
                f"{action.dest} 在 format_help 里找不到）；"
                "请改用 format_help 解析而不是 _get_*_actions()。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成文档里的 AUTO 区块")
    ap.add_argument("--check", action="store_true",
                    help="只校验，不写文件；有差异退出 1")
    args = ap.parse_args(argv)
    _self_check()
    if args.check:
        problems = check()
        for diff in problems:
            sys.stderr.write(diff)
        if problems:
            sys.stderr.write("\n文档与代码不一致："
                             "运行 python3 tools/gen_cli_docs.py 后提交。\n")
            return 1
        return 0
    for path, text in expected().items():
        path.write_text(text, encoding="utf-8")
        print(f"已更新 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
