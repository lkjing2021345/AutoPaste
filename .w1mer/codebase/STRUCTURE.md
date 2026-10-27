# STRUCTURE

单文件程序 `autopaste.py`（约 950 行），自上而下分为 7 段：

```
常量/安全上限  →  X11 ctypes 绑定  →  输入校验  →  Typer(注入核心)
      →  LineEditor(raw 行编辑)  →  REPL  →  argparse CLI + main()
```

数据流（CLI 模式）：
`argv/stdin/--file` → `validate_payload()` → `Typer` → XTEST 按键事件 → 焦点窗口

数据流（REPL 模式）：
`LineEditor.read()` → 命令分发(`:`)/提交文本 → `countdown()` → `Typer.type_text()`

关键约束：
- `Typer` 是唯一持有 X Display 的对象，`close()` 幂等，`atexit` 注册兜底。
- 每次 Unicode 输入会临时改写一个空闲 keycode（`spare_kc`），必须在 `finally` 里还原。
- `countdown()` 是唯一显示"目标窗口"的位置（安全可视化）。
- 所有用户输入路径都必须经过 `validate_payload()`（控制字符/长度闸门）。
