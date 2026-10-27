# AutoPaste

**把一段文本"敲"进当前聚焦的窗口** —— Linux 键盘注入工具。X11 走 XTEST；
Wayland 走 `wtype` 或 `ydotool`；零 Python 依赖、不需要 root。

**和 xdotool 的区别**：它不是通用窗口操作 CLI，而是把"安全输入一段内容"做完整——
倒计时显示目标窗口、`--window` 定向并在输入前复核焦点、终端多行二次确认、
控制字符默认拒绝，另有交互 REPL（括号粘贴多行零转义）和大段文本的剪贴板直贴。

> ⚠️ **安全警告**：本程序等价于"替你在键盘上打字"。多行文本打进终端等于逐行执行
> 命令；它拒绝 root、拒绝控制字符、拒绝 FIFO/设备，但这些闸门**不能判断内容是否
> 恶意**。只在你能看到屏幕时使用。完整威胁模型见 [SECURITY.md](SECURITY.md)。

## 30 秒上手

```bash
autopaste 'hello world'        # 敲进当前窗口
autopaste -s 3 '切窗口后输入'   # 3 秒倒计时，期间任意键取消
cat note.txt | autopaste -s 3 -  # 从管道读
autopaste                      # 交互 REPL：粘贴多行文本，零转义
autopaste --dry-run 'x < y'    # 只预览目标与内容，不发按键
```

X11 开箱即用；Wayland 先确认后端：

```bash
autopaste --list-backends      # 可用性、版本、探测依据
```

## 安装

```bash
git clone git@github.com:lkjing2021345/AutoPaste.git
cd AutoPaste
pipx install .                 # 推荐：独立环境 + autopaste 命令
# 或者
./install.sh                   # 软链到 ~/.local/bin/autopaste
```

要求 Linux + Python ≥ 3.8。X11 只需系统自带的 `libX11`/`libXtst`；Wayland 需要
`wtype`（wlroots/KWin）或 `ydotool`（GNOME 等，需要 `ydotoold` 与 `/dev/uinput` 权限）：

```bash
sudo pacman -S wtype ydotool   # Arch
sudo apt install wtype ydotool # Debian/Ubuntu
```

细节与 GNOME 选择顺序见 [docs/backends.md](docs/backends.md)。

## 能力摘要

| 能力 | x11 | wtype | ydotool |
|---|---|---|---|
| 任意 Unicode | ✔ | ✔ | ✘（仅 ASCII） |
| 组合键 | ✔ | ✔ | ✔ |
| 窗口定向 / 枚举 | ✔ | ✘ | ✘ |
| 额外依赖 | 无（系统 X 库） | wtype + 合成器支持 | ydotool + ydotoold |

- **REPL**：`↑`/`↓` 历史前缀搜索、`:p` 预览、`:w` 保存、`:win` 定向；
  历史明文落盘（0600），敏感内容加 `--no-history`。
- **配置**：`~/.config/autopaste/config.toml` 设 `delay/sleep/backend/auto_enter/paste_mode`；
  命令行 > 配置 > 默认，安全开关不可配置。
- **可观测**：`-v/--verbose` 诊断，`-q/--quiet` 只保留目标行、风险与错误。
- **大段文本**：`--paste-mode` 写剪贴板 + 一次粘贴键，默认备份/恢复原剪贴板。

完整参数与退出码见 [docs/cli.md](docs/cli.md)，使用流程见 [docs/usage.md](docs/usage.md)。

## 文档与开发

| 文档 | 内容 |
|---|---|
| [docs/README.md](docs/README.md) | 文档索引与维护规则 |
| [docs/usage.md](docs/usage.md) | CLI/REPL 全流程、配置、故障排查 |
| [docs/cli.md](docs/cli.md) | 参数表（由 `--help` 生成）、键名、退出码 |
| [docs/backends.md](docs/backends.md) | 能力矩阵、auto 规则、安装/权限 |
| [docs/design.md](docs/design.md) | 架构、数据流、安全闸门 |
| [SECURITY.md](SECURITY.md) | 威胁模型与防不了什么 |
| [CHANGELOG.md](CHANGELOG.md) | 版本历史 |

```bash
python3 -m unittest discover -s tests -v < /dev/null   # 测试不需要 DISPLAY
python3 tools/gen_cli_docs.py --check                  # 文档与 --help 一致性
```

贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。MIT License。
