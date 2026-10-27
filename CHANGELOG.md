# Changelog

## 未发布

### Added
- 可插拔 `Backend` 抽象：`Typer` 退化为会话外观，X11 实现原样搬入 `X11Backend`。
- Wayland 支持：`wtype`（虚拟键盘协议）与 `ydotool`（`/dev/uinput`，仅 ASCII）后端。
- `--backend auto|x11|wtype|ydotool` 与 `--list-backends`（可用性、版本、探测依据）。
- Wayland 会话的安装/权限诊断文案（ydotoold、`/dev/uinput`、发行版命令）。

### Fixed
- `ydotool type` 固定传 `-e 0`：反斜杠文本按字面量发送，不再被解码成 `\n`/`\t`/`\xNN`，从而绕过控制字符与多行确认两道闸门。
- `wtype` 修饰键名映射：`super`/`win`/`meta` → `logo`，未知修饰键在发送前直接拒绝。
- GNOME 下 `auto` 优先选 `ydotool`；wtype 报协议不支持时明确提示 `--backend ydotool`，不静默重试。

### Changed
- libX11/libXtst 改为惰性加载：无 X 库的机器/CI 也能 `--help`、`--version`。
- 移除"没有 DISPLAY 就退出"的硬检查，改为后端解析失败才退出 2。
- Wayland 读不到焦点窗口：所有多行输入一律要求确认（`-y` 可跳过），比原先只拦终端更严。
- 组合键解析提取为共享的 `parse_combo()`；Wayland 后端按 UTF-8 边界分片（≤32 KiB）避免 `E2BIG`。

## 1.0.0 — 2026-10-27

首个公开发布版本。

- 单一可执行脚本 `autopaste.py`（Python ≥ 3.8，零第三方依赖，直接使用 XTEST）。
- 命令行模式：文本 / 文件 / stdin / 组合键，字符级延时，倒计时。
- 交互 REPL：终端括号粘贴、行编辑、`:s` `:d` `:nl` `:k` `:f` `:r` `:t` `:h` `:q`。
- 任意 Unicode 输入：临时重映射空闲 keycode，退出/异常时还原。
- 安全加固：拒绝 root、控制字符拦截、终端多行二次确认、`--dry-run`、
  输入与延时上限、只读普通文件、信号与 `atexit` 双保险的键盘映射还原。
- 文档：README（中/英）、SECURITY.md、本 CHANGELOG。
