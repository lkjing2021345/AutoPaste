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
- **REPL 模式**：支持终端括号粘贴，粘进来的多行文本原样输入，不用管引号、`!`、`$`、反引号。
- **组合键**：`-k ctrl+alt+t`、`-k ctrl+shift+v`、方向键 / F1~F12 / Home / End 等。
- **目标可见**：倒计时期间会显示"即将输入到哪个窗口"，避免敲错地方。
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

要求：Linux + Python ≥ 3.8。X11 会话开箱即用；Wayland 会话需要 `wtype`（推荐，wlroots/KWin）或 `ydotool`（GNOME 等，需要 `ydotoold` 与 `/dev/uinput` 权限）：

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
```

| 参数 | 说明 |
|---|---|
| `-d, --delay 秒` | 每个字符之间的间隔，默认 `0.008`，上限 5 |
| `-s, --sleep 秒` | 开始前倒计时；REPL 中作为每次提交的默认值 |
| `-f, --file 路径` | 从文件读取（仅普通文件，上限 1 MiB） |
| `-n, --no-newline` | 去掉文件/stdin 结尾的换行 |
| `-k, --key 键名` | 按键或组合键，可重复 |
| `--dry-run` | 打印目标窗口、字符数、内容预览，不发送按键 |
| `-y, --yes` | 跳过"终端 + 多行"确认 |
| `--allow-control` | 允许控制字符（ESC 等），默认拒绝 |
| `--allow-root` | 允许 root 运行，默认拒绝 |
| `-V, --version` | 版本号 |

支持的特殊键名：`enter` `tab` `esc` `backspace` `delete` `insert` `home` `end` `pageup` `pagedown` `up` `down` `left` `right` `space` `f1`~`f12`，修饰键 `ctrl` `alt` `shift` `super`/`win`。文本中的 `\n` 按回车，`\t` 按 Tab。

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
| `:t` | 显示当前设置与目标窗口 |
| `:h` / `:q` | 帮助 / 退出 |

行编辑：方向键、`Home`/`End`、`Backspace`/`Delete`、`Ctrl-U`、`Ctrl-W`、`Ctrl-C` 取消、`Ctrl-D` 退出。

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
9. 不联网、不读剪贴板、不写文件；Wayland 后端只以固定 argv 调用 `wtype`/`ydotool`
   （不经 shell），X11 后端不启动任何外部进程。

完整威胁模型与"本工具防不了什么"见 **[SECURITY.md](SECURITY.md)**。

## 已知限制

- **Wayland 需外部工具**。X11 走 XTEST；Wayland 需 `wtype`（合成器需支持 virtual-keyboard 协议，GNOME/mutter 不支持）或 `ydotool`（内核层，但只支持 ASCII，且需要 `ydotoold` + `/dev/uinput` 权限）。
- **Wayland 下看不到焦点窗口**，倒计时只显示"Wayland 会话"；因此所有多行输入都会要求确认。
- **输入法（fcitx5/ibus）可能拦截个别按键**。之前排查到本机 `XMODIFIERS=@im=fcitx` 时，某些 XIM 程序会吞掉个别单键；遇到时 `export XMODIFIERS=` 再跑。
- **依赖当前键盘布局**。程序按 keysym 找 keycode，切换布局后行为随之变化；找不到的字符会走临时重映射。
- 少数程序用扫描码/原始设备事件（如部分游戏、反作弊），XTEST 注入不生效。

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

## 许可证

[MIT](LICENSE) © 2026 lkjing2021345
