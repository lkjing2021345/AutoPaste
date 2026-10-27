# 模块：autopaste.py（单文件 CLI）

| 区域 | 行 | 职责 |
|---|---|---|
| 常量 | 54-68 | `__version__`、`MAX_PAYLOAD/MAX_DELAY/MAX_COUNTDOWN`、`TERMINAL_CLASSES` |
| X11 绑定 | 70-135 | ctypes 函数原型、`XClassHint`（字段用 `POINTER(c_char)`，见注释：`c_char_p` 会被转成 bytes 导致 XFree 崩溃） |
| 校验 | 137-226 | `Abort`、`_char_keysym`、`validate_payload`、`check_regular_file` |
| 窗口查询 | 141-200 | `focused_window` / `describe_window` / `is_terminal` |
| `Typer` | 229-402 | `keycode_for`、`_tap`、`_remap/_restore_keymap`、`type_text/type_char/press`、`close` |
| `LineEditor` | 405-554 | cbreak 行编辑：方向键/Home/End/退格/删除/Ctrl-U/W/括号粘贴（1 MiB 上限） |
| REPL | 557-740 | `Settings`、`countdown`（显示目标窗口）、`warn_terminal_target`、`repl`、`_num` |
| CLI | 743-950 | `build_parser`、`_esc_preview`、`_confirm`、`main`、`is_combo`、`_install_signals` |

扩展点（任务 02 引入后端抽象时要动的地方）：
- `Typer.__init__` 里硬编码 `XOpenDisplay`；`_tap/type_char/press` 直接调 `xtst.*`。
- `focused_window`/`describe_window` 是 X11 专用，Wayland 后端需要自己的实现或降级为空。
