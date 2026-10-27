# 命令行参数

[← README](../README.md) · [文档索引](README.md)

`autopaste` 的 CLI 参数、键名与退出码。参数表由 `tools/gen_cli_docs.py` 从
`build_parser()` 生成；**不要手改 AUTO 区块**，改代码后运行
`python3 tools/gen_cli_docs.py`。

## 参数 {#flags}

<!-- BEGIN AUTO:flags -->
| 参数 | 取值 | 说明 |
|---|---|---|
| `text` | text … | 要输入的文本；'-' 表示从 stdin 读取 |
| `-d`, `--delay` | 浮点 | 每个字符之间的延时秒数，默认 0.008（可被配置文件覆盖），上限 5 |
| `-s`, `--sleep` | 浮点 | 开始前倒计时秒数（REPL 中为每次提交的默认值） |
| `-f`, `--file` | file | 从文件读取内容（仅普通文件） |
| `-k`, `--key` | key（可重复） | 按键或组合键，可重复: -k ctrl+v -k enter |
| `-n`, `--no-newline` | — | 文件/stdin 输入时去掉结尾换行 |
| `--repl` | — | 强制进入交互 REPL |
| `--dry-run` | — | 只打印将要输入的内容与目标窗口，不发送任何按键 |
| `--safe` | — | X11 逐字符保守发送（每字符 flush+sleep），不用批量事件 |
| `-y`, `--yes` | — | 跳过确认提示（终端目标 + 多行文本时使用） |
| `--allow-control` | — | 允许输入控制字符（ESC 等），确认安全后再用 |
| `--backend` | auto / x11 / wtype / ydotool | 键盘注入后端：auto\|x11\|wtype\|ydotool，默认 auto（可被配置文件覆盖） |
| `--list-backends` | — | 列出各后端可用性与探测依据后退出 |
| `--window` | PATTERN | 定向到匹配的窗口：默认大小写不敏感子串（class/标题），可用 class: title: pid: id: re: 前缀 |
| `--list-windows` | — | 列出可定向的窗口（TSV）后退出；可与 --window 过滤合用 |
| `--json` | — | 配合 --list-windows 输出 JSON |
| `--paste-mode` | — | 把内容写入剪贴板并发送一次粘贴键（大段文本首选） |
| `--paste-key` | COMBO | 覆盖粘贴快捷键，如 ctrl+v / ctrl+shift+v / shift+insert |
| `--paste-wait` | 秒 | 发送粘贴键后等目标取走内容的秒数（默认按体积，0-10） |
| `--no-clipboard-backup`, `--no-restore-clipboard` | — | 不备份/不恢复原剪贴板（默认 fail-closed：备份失败即停止） |
| `-v`, `--verbose` | — | 输出诊断信息（配置来源/后端/capabilities/耗时） |
| `-q`, `--quiet` | — | 静默模式：只保留倒计时目标行、风险提示与错误 |
| `--no-history` | — | 不读写 REPL 历史文件（默认保存到 XDG state 目录） |
| `--config` | PATH | 显式指定配置文件（默认 XDG_CONFIG_HOME/autopaste/config.toml） |
| `--no-config` | — | 不读取任何配置文件，全部使用默认值 |
| `--allow-root` | — | 允许以 root 运行（不推荐） |
| `-V`, `--version` | — | 打印版本号后退出 |
<!-- END AUTO:flags -->

> 默认值可被 `config.toml` 覆盖（命令行 > 配置 > 默认）；`-d`/`-s`/`--backend`
> 的"默认"是最终回退值。配置文件细节见 [usage.md](usage.md#config)。

## 键名 {#key-names}

可作为 `-k` / `:k` 的值：

- 普通键：`enter` `tab` `esc` `backspace` `delete` `insert` `home` `end`
  `pageup` `pagedown` `up` `down` `left` `right` `space` `f1`–`f12`
- 修饰键：`ctrl` `alt` `shift` `super`（`win`/`meta` 同义）
- 组合键：`ctrl+alt+t`、`ctrl+shift+v`、`shift+insert`（`+` 连接，最后一段是主键）

单个字符会按文本输入；`enter` 等特殊键名与含 `+` 的组合键走按键路径。
wtype 后端会把 `super`/`win`/`meta` 映射成它认识的 `logo`，未知修饰键在发送前直接拒绝。
文本里的 `\n` 按回车、`\t` 按 Tab（REPL 粘贴的真实换行/Tab 同理）；ydotool 后端下反斜杠
只是字面字符，不做转义解码。

## 退出码 {#exit-codes}

| 码 | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | 注入完成、`--dry-run`、`--list-backends`、`--list-windows`、`--help`/`--version`、REPL 正常退出、管道被 `head` 关闭 |
| 1 | 内容/数值校验失败 | `--delay 99`、`--sleep -1`、控制字符、超过 1 MiB、文件读不出来、`--paste-mode` 参数契约、REPL 内用 `--paste-mode` |
| 2 | 用法/配置/环境/安全拒绝 | **argparse 用法错误**（未知选项、缺值、非法 `--backend` 取值）、root、无可用后端、配置文件语法/权限/安全键、`--repl` 非 tty、`--window` 歧义或后端不支持 |
| 3 | 用户在安全确认处取消 | 多行确认回 `n`、窗口候选里按 `q`/EOF、倒计时后焦点已离开定向窗口 |
| 130 | 中断 | `Ctrl-C`/`SIGINT`/`SIGTERM`/`SIGHUP`，或倒计时被按键取消 |

两条容易混淆的路径：

- **argparse 自身的用法错误退 2**：未知选项、缺值、`--backend foo`、`--delay abc`、
  `-q` 与 `-v` 同给。只有**数值越界**（如 `--delay 99`）才由 `_num()` 退 **1**。
- **REPL 不支持 `--paste-mode`**：交互模式下给 `--paste-mode` 直接退 **1**（不是 2）。

## 示例

```bash
# 最常用
autopaste 'hello world'
autopaste -s 3 '切窗口后自动输入'
cat note.txt | autopaste -s 3 -
autopaste --dry-run 'x < y'

# 定向与剪贴板
autopaste --list-windows
autopaste --window class:firefox 'hi'
autopaste --paste-mode -f big.txt
autopaste --paste-mode --paste-key ctrl+shift+v -f big.txt

# 诊断与配置
autopaste --list-backends
autopaste -v --dry-run 'hi'
autopaste --config ./my.toml 'hi'
```
