# AutoPaste

把一段文本"敲"进任意 X11 窗口的键盘注入工具。纯 Python 标准库实现，直接调用 X11 的 XTEST 扩展，**不需要 xdotool、不需要 root、不需要装依赖**。

适合这些场景：往虚拟机/远程桌面里粘贴长命令、往不允许粘贴的输入框里填内容、往编辑器里灌一段代码、重复输入同一段样板文字。

```bash
autopaste "hello world"        # 直接敲进去
autopaste -s 3 "倒数 3 秒再敲"  # 留时间切窗口
autopaste                      # 交互 REPL：粘贴多行文本，零转义
```

---

## 特性

- **零依赖**：只用 `ctypes` 调 `libX11` / `libXtst`，不经过剪贴板，也不依赖 `xdotool`。
- **任意 Unicode**：中文、emoji、全角标点都能输入（内部临时重映射一个空闲 keycode，输完还原）。
- **REPL 模式**：支持终端括号粘贴，粘进来的多行文本原样输入，不用管引号、`!`、`$`、反引号；
  `↑`/`↓` 取回历史（行内前缀搜索）、`:p` 预览、`:w` 保存到文件。
- **可配置、可静默**：`~/.config/autopaste/config.toml` 设置默认值；
  `-q/--quiet` 只保留目标窗口与风险提示，`-v/--verbose` 输出诊断。
- **组合键**：`-k ctrl+alt+t`、`-k ctrl+shift+v`、方向键 / F1~F12 / Home / End 等。
- **目标可见**：倒计时期间会显示"即将输入到哪个窗口"，避免敲错地方。
- **定向窗口**：`--window class:firefox` 按 WM_CLASS/标题/PID 选中窗口并校验焦点；
  `--list-windows` 列出可定向的窗口。
- **大段文本直贴**：`--paste-mode` 写入剪贴板后按一次粘贴键（终端自动用
  `Ctrl+Shift+V`），并默认备份/恢复原剪贴板。
- **安全默认值**：拒绝 root 运行、拒绝控制字符、拒绝 FIFO/设备文件、终端里的多行文本二次确认。详见 [SECURITY.md](SECURITY.md)。

## 安装

```bash
git clone git@github.com:lkjing2021345/AutoPaste.git
cd AutoPaste
pipx install .               # 推荐：装进独立环境，直接得到 autopaste 命令
# 或者
./install.sh                 # 软链到 ~/.local/bin/autopaste
```

或者不安装，直接 `python3 autopaste.py ...`。

要求：Linux + Python ≥ 3.8。X11 会话开箱即用；Wayland 会话需要 `wtype`（推荐，wlroots/KWin）或 `ydotool`（GNOME 等，需要 `ydotoold` 与 `/dev/uinput` 权限）。`auto` 在 GNOME 下优先选 `ydotool`（mutter 不支持 wtype 依赖的 virtual-keyboard 协议），其他桌面优先选 `wtype`：

```bash
sudo pacman -S wtype ydotool          # Arch
sudo apt install wtype ydotool        # Debian/Ubuntu
sudo dnf install wtype ydotool        # Fedora
```

## 用法

### 命令行

```bash
autopaste "文本"                     # 输入文本
autopaste -s 3 "文本"                # 倒数 3 秒（切窗口用）
autopaste -d 0.05 "慢一点"            # 每字符间隔 50ms（默认 8ms）
autopaste -f note.txt -s 2           # 输入整个文件
cat note.txt | autopaste -s 3 -      # 从管道读取
autopaste -k ctrl+alt+t              # 组合键
autopaste -k ctrl+v -k enter         # 多个动作连发
autopaste --dry-run "x < y"          # 只预览目标和内容，不敲键
autopaste --backend wtype "hello"    # 显式指定后端（auto|x11|wtype|ydotool）
autopaste --list-backends            # 查看各后端可用性、版本与探测依据
autopaste --list-windows             # 列出可定向的窗口（TSV，可 grep）
autopaste --window class:firefox "hi"  # 定向到 Firefox（X11 里还会校验焦点）
autopaste --paste-mode -f big.txt    # 大段文本：写剪贴板 + 一次粘贴键
autopaste --paste-mode --dry-run -f big.txt  # 只预览，不碰剪贴板
autopaste -q -s 3 "文本"              # 静默：只留一行目标窗口提示
autopaste -v --dry-run "hi"          # 诊断：配置来源/后端/capabilities
autopaste --no-history               # REPL 不读写历史文件
autopaste --config ./my.toml "hi"    # 指定配置文件
```

`--window` 的 pattern 默认是**大小写不敏感子串**（匹配 WM_CLASS/instance/标题），
也可用前缀精确限定：

| pattern | 含义 |
|---|---|
| `class:kitty` | 只匹配 WM_CLASS / instance |
| `title:文档` | 只匹配窗口标题 |
| `pid:1234` | 按 `_NET_WM_PID` 精确匹配 |
| `id:0x03e00005` | 按 X11 window id 精确匹配 |
| `re:^(\[)?nvim` | 正则（唯一显式 opt-in 的正则入口，大小写不敏感） |

多个窗口匹配时：若当前激活窗口在候选里就用它，否则列出候选（`id`/`class`/`pid`/`title`）
并退出 2；在交互终端里会让你选择。`--list-windows` 输出 6 列 TSV
（`ID CLASS PID DESKTOP ACTIVE TITLE`），加 `--json` 输出 JSON。

| 参数 | 说明 |
|---|---|
| `-d, --delay 秒` | 每个字符之间的间隔，默认 `0.008`（可被配置文件覆盖），上限 5 |
| `-s, --sleep 秒` | 开始前倒计时；REPL 中作为每次提交的默认值 |
| `-f, --file 路径` | 从文件读取（仅普通文件，上限 1 MiB） |
| `-n, --no-newline` | 去掉文件/stdin 结尾的换行 |
| `-k, --key 键名` | 按键或组合键，可重复 |
| `--dry-run` | 打印目标窗口、字符数、内容预览，不发送按键 |
| `--safe` | X11 逐字符保守发送（每字符 flush+sleep），不用批量事件 |
| `-y, --yes` | 跳过"终端 + 多行"确认 |
| `--allow-control` | 允许控制字符（ESC 等），默认拒绝 |
| `--allow-root` | 允许 root 运行，默认拒绝 |
| `--window PATTERN` | 定向窗口（默认子串；`class:` `title:` `pid:` `id:` `re:`） |
| `--list-windows` | 列出窗口（TSV）后退出，可与 `--window` 过滤合用 |
| `--json` | 配合 `--list-windows` 输出 JSON |
| `--paste-mode` | 写剪贴板 + 发送一次粘贴键（大段文本首选） |
| `--paste-key COMBO` | 覆盖粘贴快捷键（默认按目标自动选） |
| `--paste-wait 秒` | 粘贴后等待目标取走内容的秒数（默认按体积 0.5–3s） |
| `--no-clipboard-backup` | 不备份/不恢复剪贴板（默认备份失败即停止） |
| `-v, --verbose` | 输出诊断（配置来源、后端、capabilities、耗时）；与 `-q` 互斥 |
| `-q, --quiet` | 静默：只保留倒计时目标行、风险提示、确认与错误 |
| `--config PATH` | 显式指定配置文件 |
| `--no-config` | 不读取配置文件 |
| `--no-history` | 不读写 REPL 历史文件（连目录都不创建） |
| `-V, --version` | 版本号 |

### 退出码

| 码 | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | 注入完成、`--dry-run`、`--list-backends`、`--help/--version`、REPL 正常退出、管道被 `head` 关闭 |
| 1 | 内容/数值校验失败 | `--delay 99`、`--sleep -1`、控制字符、超过 1 MiB、文件读不出来、`:w` 路径非法 |
| 2 | 用法/配置/环境/安全拒绝 | argparse 未知选项/缺值/互斥冲突、root、无可用后端、配置文件语法/权限/安全键、`--repl` 非 tty |
| 3 | 用户在安全确认处取消 | 多行确认回 `n`、窗口候选里按 `q`、倒计时后焦点已离开 |
| 130 | 中断 | `Ctrl-C`/`SIGINT`/`SIGTERM`、倒计时被按键取消 |

`argparse` 自身的用法错误（未知选项、缺值、`-q` 与 `-v` 同给）也统一退 **2**。

支持的特殊键名：`enter` `tab` `esc` `backspace` `delete` `insert` `home` `end` `pageup` `pagedown` `up` `down` `left` `right` `space` `f1`~`f12`，修饰键 `ctrl` `alt` `shift` `super`/`win`/`meta`（wtype 后端会把 `super`/`win`/`meta` 映射成它认识的 `logo`）。文本中的 `\n` 按回车，`\t` 按 Tab（REPL 粘贴的真实换行/Tab；`ydotool` 后端下反斜杠只是字面字符，不做转义解码）。

### 交互 REPL（推荐）

不带参数运行即进入：

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
| `:nl` | 切换"输完自动按回车"（适合聊天框/命令行提交） |
| `:k ctrl+v` | 发送组合键（同样先倒计时） |
| `:f note.txt` | 输入文件内容 |
| `:r` | 重复上一次提交的内容 |
| `:t` | 显示当前设置（含来源）、目标窗口 |
| `:p` | 预览上一次提交的内容（转义显示，不发任何按键） |
| `:w <文件>` | 把上一次提交的内容保存到文件（0600，拒绝符号链接/非普通文件） |
| `:win <pattern>` | 设置定向窗口（`class:`/`title:`/`pid:`/`id:`/`re:`），`:win -` 清除 |
| `:h` / `:q` | 帮助 / 退出 |

行编辑：方向键、`Home`/`End`、`Backspace`/`Delete`、`Ctrl-U`、`Ctrl-W`、`Ctrl-C` 取消、`Ctrl-D` 退出。

- `↑`/`↓`（或 `Ctrl-P`/`Ctrl-N`）翻历史；按 `↑` 时行内已有的内容作为**前缀过滤器**
  （先输 `:s` 再 `↑` 只会找回 `:s ...`）。`↓` 回到开始翻之前的草稿。
- 多行历史条目以 `[历史 N 字符 / M 行]` 折叠显示，回车原样重发、退格清除。
- 历史保存在 `$XDG_STATE_HOME/autopaste/history`（默认 `~/.local/state/autopaste/history`），
  文件 0600、目录 0700，只保留最近 300 条；`--no-history` 完全不读写。
  **历史里是明文**，粘贴过令牌/密码请用 `--no-history`。

### 配置文件

`~/.config/autopaste/config.toml`（或 `$XDG_CONFIG_HOME/autopaste/config.toml`）：

```toml
delay = 0.02          # 每个字符间隔（0–5）
sleep = 2.5           # 倒计时；REPL 与 CLI 都生效
backend = "wtype"     # auto | x11 | wtype | ydotool
auto_enter = false    # 仅 REPL：提交后自动回车
paste_mode = false    # 仅 CLI：默认用剪贴板直贴
```

- 只支持这个**扁平子集**（注释、可选的 `[autopaste]` 段头、字符串/布尔/数字），
  数组、日期、多行字符串一律报错（带 `路径:行号`）。
- 优先级：**命令行 > 配置文件 > 默认值**；REPL 的 `:s`/`:d`/`:nl` 只改本次会话，不回写。
- `allow_root`/`allow_control`/`yes`/`no_restore_clipboard` 等安全开关**不允许**出现在
  配置里（含 `allow-root` 这类拼写变体），出现即退出 2。
- 配置文件必须属于当前用户、不可被组/其他用户写、不能是符号链接，否则退出 2
  （不会静默忽略被篡改的配置）。
- `paste_mode = true` 只是把默认发送方式切成剪贴板直贴，剪贴板备份失败/非文本等
  fail-closed 闸门全部照旧。

## 安全设计

这个程序的本质是"替你在键盘上打字"，所以按**特权工具**对待：

1. 默认拒绝 root（`--allow-root` 才能绕过）；
2. 倒计时期间显示目标窗口的 `[WM_CLASS] 标题`，敲之前能看见敲到哪；
3. 默认拒绝控制字符（终端转义序列可能被用来伪造输出、写剪贴板等），需要时 `--allow-control`；
4. 目标是终端且内容多行时，会提示"将逐行执行"并要求确认（非交互环境直接拒绝）；
5. `--dry-run` 可以先验证目标窗口和内容；
6. 输入上限 1 MiB、延时有上限，避免误喂 `yes`/超大管道；
7. 只接受普通文件，不读 FIFO/设备文件；
8. 异常/`Ctrl-C`/`SIGTERM` 时都会还原被临时改写的键盘映射，不在系统里留下坏键位；
9. 不联网；除 REPL 历史文件、显式 `:w <文件>` 与配置文件读取外不写文件；
   Wayland 后端只以固定 argv 调用 `wtype`/`ydotool`
   （不经 shell），X11 后端不启动外部进程。唯一的例外是显式 `--paste-mode`：
   它会调用 `xclip`/`xsel`/`wl-copy` 读写**系统剪贴板**（先备份、贴完恢复；
   剪贴板管理器仍可能留存历史），以及 `--window` 会在确认后主动激活目标窗口。
10. 大文本请用 `--paste-mode`。X11 逐字符注入已改为批量事件（吞吐提升、flush
    次数大幅下降），但 `--delay` 是真实等待：1 MiB 文本按默认 8ms 仍要约 2.3 小时。
    批量模式下 `--delay` 最小 1ms；若目标应用丢字或你要求旧时序，用 `--safe`。

完整威胁模型与"本工具防不了什么"见 **[SECURITY.md](SECURITY.md)**。

## 已知限制

- **Wayland 需外部工具**。X11 走 XTEST；Wayland 需 `wtype`（合成器需支持 virtual-keyboard 协议，GNOME/mutter 不支持）或 `ydotool`（内核层，但只支持 ASCII 字面量，且需要 `ydotoold` + `/dev/uinput` 权限）。ydotool 调用固定带 `-e 0`，文本中的反斜杠不会被解码成控制字符。GNOME 下 `auto` 会先选 ydotool；若坚持用 wtype 并遇到"协议不支持"，程序会明确报错并提示改用 `--backend ydotool`（不会静默重试）。
- **Wayland 下看不到焦点窗口**，倒计时只显示"Wayland 会话"；因此所有多行输入都会要求确认。
- **输入法（fcitx5/ibus）可能拦截个别按键**。之前排查到本机 `XMODIFIERS=@im=fcitx` 时，某些 XIM 程序会吞掉个别单键；遇到时 `export XMODIFIERS=` 再跑。
- **依赖当前键盘布局**。程序按 keysym 找 keycode，切换布局后行为随之变化；找不到的字符会走临时重映射。
- 少数程序用扫描码/原始设备事件（如部分游戏、反作弊），XTEST 注入不生效。
- **`--window` 可能被 WM 拒绝**：i3/sway 的 `focus_on_window_activation`、KWin 的
  焦点窃取防护、GNOME 的激活策略都可能阻止焦点切换。程序会轮询确认并在失败时
  退出 2，不会退回去敲当前焦点窗口。
- **`--paste-mode` 会短暂改写系统剪贴板**：默认备份/恢复，但恢复不等于擦除，
  剪贴板管理器（klipper/clipman 等）可能留下历史；某些应用不响应 `Ctrl+V`，
  本工具无法检测"是否贴上"，建议先 `--dry-run` 确认目标。
- **批量注入中断后**最多还会送出约 250ms 已排队的按键（无法撤回）；对时序敏感
  的场合用 `--safe` 或调大 `--delay`。
- **REPL 历史会落盘**：提交过的文本（含命令）写进 0600 的历史文件，可被本用户、
  备份/同步工具读取；不需要就加 `--no-history`。

## 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `zsh: event not found: x)` | zsh 把 `!` 当历史展开。用单引号，或进 REPL 粘贴 |
| `无法连接 X 服务器` | 没有 `DISPLAY`，或不在图形会话里 |
| 什么都没敲出来 | 先 `--dry-run` 看目标窗口对不对（焦点是否在期望的窗口） |
| 部分字符丢字 | 先试 `export XMODIFIERS=`；仍有问题请开 issue 附 `--dry-run` 输出 |
| 输入法/键盘映射被改了 | 正常情况下退出即还原；若进程被 `kill -9`，重新登录会话即可恢复 |

## 开发

单文件实现，改完直接跑：

```bash
python3 -m py_compile autopaste.py
python3 -m unittest discover -s tests -v   # 单元测试，不需要 DISPLAY
python3 autopaste.py --version
./autopaste.py --dry-run -- "test 测试 123"
```

测试只用标准库 `unittest`，通过替换模块级 `autopaste.x11` / `autopaste.xtst`
为假后端来覆盖校验、行编辑与键盘注入逻辑，不会真的发送按键。

打包用 `pyproject.toml`（PEP 621），版本号唯一来源是 `autopaste.py` 里的
`__version__`：`pipx install .` 或 `pip install .` 即可安装。

CI 覆盖声明的最低版本：Python 3.8（ubuntu-22.04）与 3.9/3.13 都会跑完整单元测试，
`check` job 另覆盖 3.9–3.13。

## 许可证

[MIT](LICENSE) © 2026 lkjing2021345
