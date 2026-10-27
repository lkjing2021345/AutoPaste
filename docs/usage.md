# 使用指南

[← README](../README.md) · [文档索引](README.md)

从"敲一段话"到"定向窗口、直贴大段文本、用 REPL 粘多行"的完整流程。
参数默认值与上限只看 [cli.md](cli.md#flags)，后端选择看 [backends.md](backends.md)。

## 三种输入来源

```bash
autopaste '直接写在参数里'
autopaste -f note.txt                 # 普通文件，≤1 MiB；FIFO/设备文件直接拒绝
cat note.txt | autopaste -s 3 -      # stdin；超过 1 MiB 直接退出 1
```

`-n/--no-newline` 去掉文件/stdin 结尾的换行。多个位置参数会用空格拼成一段。
没有文本、文件、`-k` 且 stdin 是终端时，自动进入 REPL；`--repl` 可强制进入。

## 命令行流程

1. 校验参数与内容（控制字符、长度、文件类型）；
2. 解析后端（`--backend` 或 `auto`），必要时打印安装建议；
3. 解析 `--window` 目标（只读匹配，不猜歧义）；
4. `--dry-run` 到这里就打印并退出，不激活、不碰剪贴板、不发按键；
5. 多行风险确认（终端目标；Wayland 下所有多行）；
6. 激活定向窗口（如有）；
7. 倒计时显示目标窗口，按任意键或 `Ctrl-C` 取消；
8. 复核焦点，然后输入文本/组合键，或用 `--paste-mode` 写剪贴板 + 一次粘贴键。

```bash
autopaste 'hello'
autopaste -s 3 '切窗口后自动输入'
autopaste -d 0.05 '慢一点'
autopaste -k ctrl+alt+t
autopaste -k ctrl+v -k enter
autopaste --dry-run 'x < y'
autopaste --backend wtype 'hi'        # 显式指定后端
autopaste --list-backends             # 探测依据
```

`-s` 倒计时期间按**任意键**取消（退出 130）；stdin 已 EOF 时（如管道输入）
倒计时照常走完，不会把 EOF 误当按键。倒计时一直显示目标窗口，避免敲错地方。

## 定向窗口（X11）

```bash
autopaste --list-windows                      # 6 列 TSV：ID CLASS PID DESKTOP ACTIVE TITLE
autopaste --list-windows --json               # JSON 数组
autopaste --window class:firefox 'hi'
autopaste --window title:文档 'hi'
autopaste --window pid:1234 'hi'
autopaste --window id:0x03e00005 'hi'
autopaste --window 're:^(\[)?nvim' 'hi'
```

pattern 默认是**大小写不敏感子串**（WM_CLASS/instance/标题）；`re:` 是唯一显式
正则入口。多个候选时：当前激活窗口在候选里就用它，否则非交互环境列出候选并退出 2，
交互终端会让你选择。激活后会轮询确认焦点，倒计时结束再复核一次；焦点离开就退出 3，
绝不退回当前焦点盲打。

## 大段文本：`--paste-mode`

```bash
autopaste --paste-mode -f big.txt
autopaste --paste-mode --paste-key ctrl+shift+v -f big.txt
autopaste --paste-mode --paste-wait 1.0 -f big.txt
```

把内容写进**系统剪贴板**并发送一次粘贴键（终端自动用 `ctrl+shift+v`，xterm 系用
`shift+insert`，其他 `ctrl+v`），适合上千行文本。默认先备份原剪贴板（仅文本、≤4 MiB），
备份失败/超限/非文本即退出 2；恢复放在 `finally`。`--no-clipboard-backup`
（别名 `--no-restore-clipboard`）明确放弃备份/恢复。`--paste-mode` 不能与 `-k` 混用，
REPL 内也不支持（退出 1）。注意：**恢复不等于擦除**，剪贴板管理器仍可能留存历史。

`--safe` 让 X11 回到逐字符 `flush`+`sleep` 的保守路径；批量模式中断后最多还会送出
约 250ms 已排队按键，对时序敏感时用 `--safe`。

## 交互 REPL

不带参数运行即进入（需要 tty）：

```
$ autopaste
autopaste REPL ── 回车后倒计时再输入；粘贴多行文本没有任何引号/转义问题
[kitty] autopaste> 直接粘贴一大段代码
[已粘贴 482 字符 / 21 行]
  ← 回车，倒数 2 秒后开始输入
```

| 命令 | 作用 |
|---|---|
| `:s 3` | 每次回车后的倒计时（秒，`0` 为不等） |
| `:d 0.02` | 每个字符的间隔 |
| `:nl` | 切换"输完自动按回车" |
| `:k ctrl+v` | 发送组合键（同样先倒计时） |
| `:f note.txt` | 输入文件内容 |
| `:r` | 重复上一次提交的内容 |
| `:t` | 显示当前设置（含来源）、目标窗口 |
| `:p` | 预览上一次提交的内容（转义显示，不发任何按键） |
| `:w <文件>` | 把上一次提交的内容保存到文件（0600，拒绝符号链接/非普通文件） |
| `:win <pattern>` | 设置定向窗口（`:win -` 清除） |
| `:h` / `:q` | 帮助 / 退出 |

行编辑：方向键、`Home`/`End`、`Backspace`/`Delete`、`Ctrl-U`、`Ctrl-W`、`Ctrl-C` 取消、
`Ctrl-D` 退出。`↑`/`↓`（或 `Ctrl-P`/`Ctrl-N`）翻历史；按 `↑` 时行内已有内容是
**前缀过滤器**，`↓` 回到开始翻之前的草稿。多行历史条目折叠成
`[历史 N 字符 / M 行]`，回车原样重发。

历史保存在 `$XDG_STATE_HOME/autopaste/history`（默认
`~/.local/state/autopaste/history`），文件 0600、目录 0700、最近 300 条；
目录/文件权限、属主或任一级祖先目录被换成符号链接都会**禁用历史并降级为仅内存**。
历史是明文，粘过令牌/密码请用 `--no-history`。

## 配置文件 {#config}

`~/.config/autopaste/config.toml`（或 `$XDG_CONFIG_HOME/autopaste/config.toml`）：

```toml
delay = 0.02          # 每个字符间隔（0–5）
sleep = 2.5           # 倒计时；REPL 与 CLI 都生效
backend = "wtype"     # auto | x11 | wtype | ydotool
auto_enter = false    # 仅 REPL：提交后自动回车
paste_mode = false    # 仅 CLI：默认用剪贴板直贴
```

- 只支持这个扁平子集（注释、可选 `[autopaste]` 段头、字符串/布尔/数字）；
  数组、日期、多行字符串一律报错（带 `路径:行号`）。
- 优先级 **命令行 > 配置文件 > 默认值**；REPL 的 `:s`/`:d`/`:nl` 只改本次会话。
- `allow_root`/`allow_control`/`yes`/`no_restore_clipboard` 等安全开关不允许出现在
  配置里，出现即退出 2。
- 配置文件必须属于当前用户、不可被组/其他用户写、不能是符号链接，否则退出 2。
- `paste_mode = true` 只把默认发送方式切成剪贴板直贴，fail-closed 闸门全部照旧。

## 日志：`--verbose` / `--quiet`

stdout 只放你请求的数据（`--dry-run`、`--list-backends`、`--list-windows`、`:p`、`:t`、
提示符）；诊断、警告、倒计时一律走 stderr。`--quiet` 把倒计时压成一行目标提示
（**仍显示目标、仍可取消**），但风险提示、确认询问与错误照常输出。

## 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `zsh: event not found: x)` | zsh 把 `!` 当历史展开。用单引号，或进 REPL 粘贴 |
| 退出 2 + "无可用后端" | `--list-backends` 看探测依据；Wayland 装 wtype/ydotool，X11 检查 `DISPLAY` |
| 什么都没敲出来 | 先 `--dry-run` 看目标窗口对不对；Wayland 下确认焦点只能靠你自己 |
| 部分字符丢字 | 先 `export XMODIFIERS=`；仍有问题附 `--dry-run` 输出开 issue |
| `--window` 激活失败（退出 2） | WM 的焦点窃取防护（i3/sway、KWin、GNOME）；手动切过去再用 `-s` |
| 输入法/键盘映射被改了 | 正常退出会还原；被 `kill -9` 后重新登录会话即可恢复 |
| 敏感内容进了历史 | 用 `--no-history`；历史文件 0600 但仍是明文 |
