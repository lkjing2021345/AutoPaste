# Changelog

## 1.0.0 — 2026-10-27

首个公开发布版本。

- 单一可执行脚本 `autopaste.py`（Python ≥ 3.8，零第三方依赖，直接使用 XTEST）。
- 命令行模式：文本 / 文件 / stdin / 组合键，字符级延时，倒计时。
- 交互 REPL：终端括号粘贴、行编辑、`:s` `:d` `:nl` `:k` `:f` `:r` `:t` `:h` `:q`。
- 任意 Unicode 输入：临时重映射空闲 keycode，退出/异常时还原。
- 安全加固：拒绝 root、控制字符拦截、终端多行二次确认、`--dry-run`、
  输入与延时上限、只读普通文件、信号与 `atexit` 双保险的键盘映射还原。
- 文档：README（中/英）、SECURITY.md、本 CHANGELOG。
