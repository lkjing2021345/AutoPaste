# 模块：autopaste.py（单文件 CLI）

| 区域 | 行 | 职责 |
|---|---|---|
| 常量 | 86-152 | `__version__`、`MAX_PAYLOAD/MAX_DELAY/MAX_COUNTDOWN`、历史/配置/窗口/剪贴板上限 |
| X11 绑定 | 153-329 | ctypes 原型、结构体、`_load_x11()` 惰性 dlopen、`_require_x11()` |
| 异常 + Log | 330-417 | `Abort`/`BackendError`/`BackendUnavailable`/`WindowUnavailable`/`WindowCancelled`/`ClipboardUnsafe`/`ConfigError`；唯一 stderr 出口 |
| 窗口查询/枚举 | 418-985 | `focused_window`、`WindowInfo`、`_NET_CLIENT_LIST` + `XQueryTree` 枚举、pattern 匹配、`pick_window`、`activate_window_x11` |
| 校验 | 986-1011 | `validate_payload`（控制字符/1 MiB）、`check_regular_file`（拒绝 FIFO/设备） |
| 后端抽象 | 1012-1339 | `Backend` 契约、`X11Backend`（XTEST、键位重映射、批量事件/`batch_enabled`） |
| 外部命令/选择 | 1340-1725 | `_run`（固定 argv、shell=False）、`WtypeBackend`、`YdotoolBackend`（`-e` 版本探测）、`detect_session`/`choose_backend`/`make_backend` |
| 剪贴板 | 1726-1946 | `Clipboard`/`CommandClipboard`（xclip/xsel/wl-copy/wl-paste）、备份/恢复、`paste_text`/`send_text` |
| 会话外观 | 1947-2060 | `Typer`（后端 + delay）、`target_label`、`multiline_risk`、`parse_combo` |
| 历史 | 2061-2332 | `default_history_path`（XDG）、`History`（JSONL v1、0600/0700、逐级 `O_NOFOLLOW`、1 MiB 尾部压缩） |
| 行编辑器 | 2333-2595 | cbreak、方向键/Home/End/退格、括号粘贴（1 MiB）、历史前缀搜索与草稿 |
| 配置 | 2596-2826 | 扁平 TOML 子集、`load_config`（`O_NOFOLLOW`/属主/≤64 KiB）、`resolve_settings`（CLI > config > default） |
| 交互界面 | 2827-3226 | `Settings`、`countdown`（tty 按键/VEOF 取消、EOF 后 sleep）、`:p`/`:w`/`repl` |
| CLI | 3227-3783 | `resolve_window`、`build_parser`、`_num`、`_confirm`、`format_backends`、`main`、`console_main` |

扩展点：
- 新后端：实现 `Backend` 的 10 个方法 + 在 `choose_backend()` 注册；不要按后端名分支，
  只按 `capabilities()` 降级。
- 新 REPL 命令：`repl()` 的命令分发 + BANNER/HELP + `tests/test_repl_commands.py`。
- 新 CLI flag：`build_parser()` → 跑 `tools/gen_cli_docs.py` → `tests/test_docs.py` 会校验
  文档/退出码/flag 是否同步。
- 安全闸门位置见 `docs/design.md` 的“安全闸门清单”；不要新增绕过 `validate_payload()`
  或焦点复核的注入路径。
