# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

## [1.1.0] - 2026-10-27

### Added

- 可插拔 `Backend` 抽象：`Typer` 退化为会话外观，X11 实现搬入 `X11Backend`；
  libX11/libXtst 改为惰性加载，无 X 库的机器与 CI 也能 `--help`/`--version`/`--dry-run`。
- Wayland 支持：`wtype`（合成器虚拟键盘协议）与 `ydotool`（`/dev/uinput`，仅 ASCII）。
- `--backend auto|x11|wtype|ydotool` 与 `--list-backends`（可用性、版本、探测依据）。
- `--window <pattern>` 定向窗口（默认大小写不敏感子串，前缀 `class:`/`title:`/`pid:`/
  `id:`/`re:`）：激活后轮询确认焦点，倒计时结束再复核一次，焦点离开即取消（退出 3）；
  `--list-windows`（6 列 TSV）与 `--list-windows --json`，只读、不激活、不注入。
- `--paste-mode` / `--paste-key` / `--paste-wait` / `--no-clipboard-backup`
  （别名 `--no-restore-clipboard`）：剪贴板直贴大段文本，默认备份并恢复原剪贴板。
- `--safe`：X11 退回逐字符（每字符 flush + sleep）的保守发送路径。
- REPL 历史：JSONL 持久化到 `$XDG_STATE_HOME/autopaste/history`（0600、最近 300 条、
  单条 ≤8 KiB/200 行）；`↑`/`↓`（`Ctrl-P`/`Ctrl-N`）翻阅、行内前缀搜索、`↓` 回到草稿；
  多行条目折叠回忆；`--no-history` 完全不读写。
- REPL `:p` 预览上一段内容（不发按键）；`:w <文件>` 原子保存（0600，拒绝符号链接/
  非普通文件）；`:win <pattern>` 定向窗口（`:win -` 清除）。
- `-v/--verbose`、`-q/--quiet` 与统一的 `Log` 出口：诊断/警告/倒计时走 stderr，
  stdout 只放用户请求的数据；`--quiet` 仍显示目标窗口行并保留取消入口。
- 配置文件 `~/.config/autopaste/config.toml`（`$XDG_CONFIG_HOME` 优先），扁平 TOML 子集
  `delay/sleep/backend/auto_enter/paste_mode`；优先级 **命令行 > 配置 > 默认**；
  `--config PATH` / `--no-config`。
- `tools/bench.py`：不依赖真实焦点窗口的事件发送基准。
- `docs/` 文档体系（usage/cli/backends/design）、`CONTRIBUTING.md`、issue 模板；
  `docs/cli.md` 的参数表与 `docs/backends.md` 的能力矩阵由 `tools/gen_cli_docs.py`
  从源码生成，`tests/test_docs.py` 与 CI 校验漂移。
- `MANIFEST.in`：sdist 现在包含 `docs/`、`tools/`、`tests/` 与根目录文档。

### Changed

- 移除“没有 `DISPLAY` 就退出”的硬检查，改为后端解析失败才退出 2。
- Wayland 的 CLI 多行输入一律要求确认（`-y` 可跳过）；X11 仅对终端目标确认。
- X11 逐字符注入改为批量事件：分块 flush（服务端排队 ≤250ms），`--delay` 放到
  XTest press 事件；事件序列与旧版一致，批量模式下 `--delay` 有 1ms 下限。
  对时序敏感或目标丢字时用 `--safe`。
- 退出码收敛为 5 个（0/1/2/3/130）并由 `Abort.code` 单一决定；argparse 用法错误明确为 2。
- 倒计时与诊断输出从 stdout 移到 stderr；`--dry-run`、`--list-backends`、
  `--list-windows`、`:p`、`:t`、`--help` 等用户请求的数据仍在 stdout。
- 组合键解析提取为共享的 `parse_combo()`；Wayland 后端按 UTF-8 边界分片（≤32 KiB）
  避免 `E2BIG`。
- REPL 每次提交走“激活 → 倒计时 → 复核焦点 → 注入”四段式，焦点离开则跳过本次输入。
- README 精简为入口，参数/后端/设计细节移入 `docs/`；`pyproject.toml` 去掉“仅 X11”表述。

### Breaking

- **REPL 窗口命令由 `:w` 改名为 `:win`；`:w` 现在表示“保存上一段内容到文件”。**
  旧习惯 `:w kitty` 不会再定向窗口，而会在当前目录创建一个名为 `kitty` 的文件并写入
  上一段内容。请改用 `:win kitty`。
- 退出码语义收紧：以前笼统归为 1 的“参数错误”现在区分 argparse 用法错误（2）与
  数值越界/内容校验（1）。依赖旧退出码的脚本需要按 `docs/cli.md` 的退出码表调整。

### Fixed

- `ydotool type` 按版本探测 `-e`：≥1.0.4 传 `-e 0` 关闭反斜杠转义；旧版不传并拒绝
  含反斜杠/C0 控制字符的文本（绝不静默打错字）。
- `wtype` 修饰键名映射：`super`/`win`/`meta` → `logo`；未知修饰键发送前直接拒绝。
- GNOME 下 `auto` 优先选 `ydotool`；wtype 报协议不支持时明确提示，不静默重试。
- 括号粘贴超限行为明确为“截断到 1 MiB + 排空到终止序列”，不会把 `[201~` 留给后续输入。
- 倒计时在 stdin 已 EOF（如 `cat note.txt | autopaste -s 3 -`）时正常走完，不再把 EOF
  当按键取消；管道 EOF 后停止轮询并睡眠，不再忙等烧 CPU。
- 交互 tty 上 `Ctrl-D`（VEOF）不再永久关闭取消入口：倒计时中按 `Ctrl-D`/任意键仍取消。
- `--window` 解析失败、`--repl` 非 tty、`-f`/stdin 的 `OSError`、
  `--list-backends | head` 的 `BrokenPipeError` 均收口为明确退出码/提示，不再 traceback。

### Security

- pip/pipx 入口统一为 `console_main`：`SIGINT/SIGTERM/SIGHUP` 在进入 `main()` 前转成
  `KeyboardInterrupt`，`atexit` 的键位还原不再被 `SIGTERM` 绕过。
- 历史文件 fail-closed：从 `/` 起逐级 `O_DIRECTORY|O_NOFOLLOW` 打开目录，
  `$XDG_STATE_HOME` 或任一祖先被换成符号链接即禁用历史并降级为仅内存；文件 0600、
  目录 0700、属主不符或权限无法收紧同样禁用。
- 配置文件 fail-closed：`O_NOFOLLOW`、必须是本用户普通文件、`mode & 0o022 == 0`、
  ≤64 KiB；安全键（含 `allow-*` 变体）出现即退出 2。
- `--window`：多候选不猜（非交互环境退出 2）；XID 复用检测；焦点复核失败即取消。
- `--paste-mode`：备份失败/非文本/超 4 MiB 一律退出 2（fail-closed），恢复放在 `finally`。
- 批量注入中断后最多再送出约 250ms 已排队按键；对时序敏感时用 `--safe`。
- 控制字符默认拒绝；stdin 超过 1 MiB 直接退出 1（不截断执行）。

## [1.0.0] - 2026-10-27

首个公开发布版本。

- 单一可执行脚本 `autopaste.py`（Python ≥ 3.8，零第三方依赖，直接使用 XTEST）。
- 命令行模式：文本 / 文件 / stdin / 组合键，字符级延时，倒计时。
- 交互 REPL：终端括号粘贴、行编辑、`:s` `:d` `:nl` `:k` `:f` `:r` `:t` `:h` `:q`。
- 任意 Unicode 输入：临时重映射空闲 keycode，退出/异常时还原。
- 安全加固：拒绝 root、控制字符拦截、终端多行二次确认、`--dry-run`、
  输入与延时上限、只读普通文件、键盘映射还原。
- 文档：README（中/英）、SECURITY.md、本 CHANGELOG。

[未发布]: https://github.com/lkjing2021345/AutoPaste/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/lkjing2021345/AutoPaste/compare/f75c037...v1.1.0
[1.0.0]: https://github.com/lkjing2021345/AutoPaste/tree/f75c037
