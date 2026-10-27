# Changelog

## 未发布

### Added
- 可插拔 `Backend` 抽象：`Typer` 退化为会话外观，X11 实现原样搬入 `X11Backend`。
- Wayland 支持：`wtype`（虚拟键盘协议）与 `ydotool`（`/dev/uinput`，仅 ASCII）后端。
- `--backend auto|x11|wtype|ydotool` 与 `--list-backends`（可用性、版本、探测依据）。
- Wayland 会话的安装/权限诊断文案（ydotoold、`/dev/uinput`、发行版命令）。
- `--window <pattern>` 目标窗口定向：EWMH `_NET_CLIENT_LIST` 优先、`XQueryTree`
  兜底枚举；默认大小写不敏感子串，支持 `class:` `title:` `pid:` `id:` `re:`
  前缀；发送 `_NET_ACTIVE_WINDOW` 后轮询 `XGetInputFocus` 确认焦点，
  倒计时结束再复核一次，焦点被抢走则取消（退出码 3）。
- `--list-windows`（TSV，6 列：ID/CLASS/PID/DESKTOP/ACTIVE/TITLE）与
  `--list-windows --json`，只读、不激活、不注入。
- `--paste-mode`：把 payload 写入剪贴板并发送一次粘贴键（终端自动用
  `ctrl+shift+v`，xterm 系用 `shift+insert`，其他 `ctrl+v`；`--paste-key` 可覆盖）。
  默认先备份原剪贴板（仅文本、≤4 MiB）并在粘贴后恢复；备份失败即停止。
- `--paste-wait`（按体积默认 0.5–3s）与 `--no-clipboard-backup`
  （别名 `--no-restore-clipboard`）。
- `tools/bench.py`：不依赖真实焦点窗口的事件发送基准。
- REPL 历史：JSONL 持久化到 `$XDG_STATE_HOME/autopaste/history`（0600，最多
  300 条、单条 ≤8 KiB/200 行）；`↑`/`↓`（`Ctrl-P`/`Ctrl-N`）翻阅、行内前缀
  搜索、`↓` 回到草稿；多行条目折叠回忆。`--no-history` 完全不读写。
  目录/文件权限、属主、符号链接任一不安全即禁用历史并降级为内存。
- REPL `:p`：复用 `--dry-run` 的转义规则预览上一次内容（字符数/行数/预计
  耗时/目标/多行风险），不发送任何按键；`:w <文件>`：原子写入（0600，拒绝
  符号链接/目录/FIFO/设备）。
- `-v/--verbose`、`-q/--quiet` 与统一的 `Log` 出口：stdout 只放用户请求的数据，
  诊断/警告/倒计时一律 stderr；`--quiet` 保留单行目标提示、可取消，且不静默
  风险提示/确认/错误。
- 配置文件 `~/.config/autopaste/config.toml`（`$XDG_CONFIG_HOME` 优先），支持
  扁平 TOML 子集 `delay/sleep/backend/auto_enter/paste_mode`；优先级
  **命令行 > 配置 > 默认**。安全键（`allow_root`/`allow_control`/`yes`/
  `no_restore_clipboard` 及 `allow-*` 变体）出现即退出 2；文件必须属主正确、
  非组/其他可写、非符号链接，否则退出 2。
- `--config PATH` / `--no-config`。

### Fixed
- REPL 定向窗口（`:win`）在倒计时结束后**复核焦点**：焦点离开则跳过本次输入
  并提示，与 CLI 一致；此前可能把内容盲打进当前窗口。
- 倒计时在 stdin 已 EOF（如 `cat note.txt | autopaste -s 3 -`）时不再把 EOF
  当作按键取消，倒计时正常走完（仍是 130 的只有真正的按键/信号取消）。
- `ydotool type` 固定传 `-e 0`：反斜杠文本按字面量发送，不再被解码成 `\n`/`\t`/`\xNN`，从而绕过控制字符与多行确认两道闸门。
- `wtype` 修饰键名映射：`super`/`win`/`meta` → `logo`，未知修饰键在发送前直接拒绝。
- GNOME 下 `auto` 优先选 `ydotool`；wtype 报协议不支持时明确提示 `--backend ydotool`，不静默重试。

### Changed
- 退出码统一为 5 个：0 成功、1 内容/数值校验失败、2 用法/配置/环境/安全拒绝
  （含 argparse 自身与 `--repl` 非 tty）、3 安全确认处取消、130 中断；
  `main()` 的异常收口改为 `except Abort` + `e.code`。
- `-d/--delay` 与 `--backend` 的 argparse 默认值改为 `None`，由
  `resolve_settings()` 按"命令行 > 配置 > 默认"填回（`-s` 原本就是 `None`）。
- `--repl` 在非 tty 下明确退出 2（不再 traceback）；`-f`/stdin 的 `OSError`
  转为"无法读取文件/stdin"（退出 1）；`--list-backends | head` 的
  `BrokenPipeError` 安静退出 0，不再打印 "Exception ignored"。
- REPL 窗口指令由 `:w` 改名为 **`:win`**，`:w` 现在表示"保存到文件"。
- 倒计时与诊断输出从 stdout 移到 stderr（`--dry-run`、`--list-backends`、
  `:p`、`:t`、`--help` 等用户请求的数据仍在 stdout）。
- libX11/libXtst 改为惰性加载：无 X 库的机器/CI 也能 `--help`、`--version`。
- 移除"没有 DISPLAY 就退出"的硬检查，改为后端解析失败才退出 2。
- Wayland 读不到焦点窗口：所有多行输入一律要求确认（`-y` 可跳过），比原先只拦终端更严。
- 组合键解析提取为共享的 `parse_combo()`；Wayland 后端按 UTF-8 边界分片（≤32 KiB）避免 `E2BIG`。
- **X11 逐字符注入改为批量事件**：不再每字符 `XFlush` + 客户端 `sleep`，
  而是分块 flush（服务端排队 ≤250ms）并把 `--delay` 放到 XTest 的 press 事件上。
  事件序列与旧版逐字符模式完全一致；`--delay` 语义仍是"相邻字符间隔"，
  精度变为 1ms（批量模式下 `--delay` 有 1ms 下限）。
  实测（模拟后端）`--delay 0` 约 45.8 万 → 75.6 万字符/秒，flush 次数
  20000 → 79；`--delay 0.008` 受 8ms 间隔限制，两种模式都约 122 字符/秒。
  大文本请用 `--paste-mode`（1 MiB 逐字符仍要约 2.3 小时）。
- 新增 `--safe`：强制回到逐字符（每字符 flush + sleep）的保守路径；
  若某些 Xorg/驱动上 XTest 的 delay 会造成整机输入卡顿，也可用
  `USE_XTEST_DELAY=False` 全局关闭批量（见 `tools/bench.py`）。
- 退出码明确化：0 成功、1 参数/安全检查、2 环境或目标窗口不可用、
  3 用户取消/焦点复核失败、130 中断。

## 1.0.0 — 2026-10-27

首个公开发布版本。

- 单一可执行脚本 `autopaste.py`（Python ≥ 3.8，零第三方依赖，直接使用 XTEST）。
- 命令行模式：文本 / 文件 / stdin / 组合键，字符级延时，倒计时。
- 交互 REPL：终端括号粘贴、行编辑、`:s` `:d` `:nl` `:k` `:f` `:r` `:t` `:h` `:q`。
- 任意 Unicode 输入：临时重映射空闲 keycode，退出/异常时还原。
- 安全加固：拒绝 root、控制字符拦截、终端多行二次确认、`--dry-run`、
  输入与延时上限、只读普通文件、信号与 `atexit` 双保险的键盘映射还原。
- 文档：README（中/英）、SECURITY.md、本 CHANGELOG。
