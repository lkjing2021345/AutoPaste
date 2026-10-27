# 设计

[← README](../README.md) · [文档索引](README.md)

面向维护者：程序怎么分层、一次注入经过哪些闸门、为什么不用剪贴板默认路径。
操作方式见 [usage.md](usage.md)，威胁模型见 [SECURITY.md](../SECURITY.md)。

## 为什么存在

`xdotool type` 已经能做键盘注入，但它是通用窗口操作 CLI，默认没有"目标可视化 +
内容风险确认 + 焦点复核 + 失败即停"的组合。AutoPaste 把范围收窄到一件事：
**把一段用户明确给出的文本/组合键安全地敲进当前聚焦窗口**。

与 xdotool / 剪贴板的差异：

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| xdotool type | 通用、成熟 | 多行/控制字符直接发；无倒计时目标可视化；窗口定向靠外部脚本 |
| 剪贴板 + 粘贴键 | 大文本快 | 改写全局剪贴板；终端常禁用 `Ctrl+V`；需要额外工具链 |
| AutoPaste 默认 | 真按键事件；目标可视化；多行确认；控制字符默认拒绝 | 逐字符注入慢（大文本用 `--paste-mode`） |

`--paste-mode` 是显式的第二路径：默认不碰剪贴板，只有用户点名时才写，并 fail-closed
备份/恢复。

## 分层

单文件 `autopaste.py`，自上而下：

```
常量/安全上限
  → 后端抽象（Backend / X11Backend / WtypeBackend / YdotoolBackend）
  → 输入校验与窗口查询
  → Typer（会话外观：选后端、延迟、关闭）
  → 剪贴板与窗口定向辅助
  → History / Log / 配置解析
  → LineEditor（cbreak 行编辑）
  → REPL / countdown
  → argparse CLI + main() / console_main()
```

`Typer` 是唯一的会话对象，`close()` 幂等；X11 的 Display、临时键位映射都在
`X11Backend` 内，`atexit` + 信号处理兜底。

### Backend 契约

- `capabilities()` 返回固定的能力字典（`window_control`/`unicode`/`combos` 等），
  上层只按能力降级，不按后端名分支；
- `type_text` / `type_char` / `press` 失败抛 `BackendError`；
- `focus_info()` / `list_windows()` / `focused_window_id()` 拿不到就返回 `None`/空列表，
  不阻塞；
- `activate_window()` 必须验证焦点是否真的到位；
- `close()` 幂等且不抛异常。

新增后端 = 实现这些方法 + 在 `choose_backend()` 注册，不改 `main()` 主干。

## 数据流

CLI：

```
argv / --file / stdin
  → validate_payload()（控制字符、1 MiB）
  → 解析配置 + 后端（CLI > config > default）
  → resolve_window()（只读）
  → [--dry-run 在此退出]
  → multiline_risk() + _confirm()
  → activate_window() + focus 复核
  → countdown()（显示目标、可取消）
  → Typer.type_text()/press() 或 paste_text()
```

REPL：

```
LineEditor.read()（cbreak；括号粘贴 ≤1 MiB；历史前缀搜索）
  → 命令分发（:s :d :nl :k :f :r :t :p :w :win :h :q）/ 普通文本
  → activate() → countdown() → focus_ok()
  → type_text()/press()
```

失败一律转成 `Abort` 子类 + 退出码（`Abort.code`），不在各层打印不一致的错误。

## 安全闸门清单

| 闸门 | 位置 | 行为 |
|---|---|---|
| 拒绝 root | `main()` 开头 | `geteuid()==0` 退 2，除非 `--allow-root` |
| 控制字符 | `validate_payload()` | 默认拒绝 C0 与 DEL；`--allow-control` 显式放行 |
| 长度/参数上限 | `MAX_PAYLOAD` / `_num()` | 1 MiB、`--delay ≤5s`、`--sleep ≤3600s` |
| 普通文件 | `check_regular_file()` | 拒绝 FIFO/设备/目录，避免阻塞或无限流 |
| 多行确认 | `multiline_risk()` / `_confirm()` | CLI 终端目标确认；Wayland 所有多行确认；非 tty 拒绝 |
| 倒计时可视化 | `countdown()` | 显示 `[WM_CLASS] 标题`，任意键/`Ctrl-C` 取消 |
| 焦点复核 | `main()` / `repl()` | 激活后与倒计时后各查一次，焦点离开退 3/跳过本次 |
| 定向不猜 | `pick_window()` | 歧义候选非交互退 2，交互必须手选 |
| 剪贴板 fail-closed | `paste_text()` | 备份失败/非文本/超 4 MiB 退 2，恢复放 `finally` |
| 历史文件 | `History` | 逐级 `O_NOFOLLOW`、0600/0700、属主校验；异常降级为仅内存 |
| 配置文件 | `load_config()` | 非符号链接、本用户、组/其他不可写、≤64 KiB，否则退 2 |
| `:w` 保存 | `_cmd_save()` | 0600 临时文件 + `os.replace`，拒绝符号链接/非普通文件 |
| 批量注入有界 | `X11Backend.type_text()` | 服务端排队 ≤250ms；`--safe` 退回逐字符 |

## 不变量

- stdout 只放用户请求的数据；诊断/警告/倒计时只走 stderr（`Log` 是唯一诊断出口）。
- 所有注入路径先过 `validate_payload()`，再过焦点闸门；没有第二条旁路。
- 修饰键释放与临时键位映射还原放在 `try/finally` 与 `atexit`；`SIGINT/SIGTERM/SIGHUP`
  在 `console_main()` 里转成 `KeyboardInterrupt` 并返回 130。
- 版本号唯一来源是 `autopaste.__version__`，`pyproject.toml` 动态读取；文档参数表与
  能力矩阵由 `tools/gen_cli_docs.py` 生成并由 `tests/test_docs.py` / CI 校验。
