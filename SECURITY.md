# 安全说明（SECURITY.md）

AutoPaste 是一个**键盘注入**工具：它通过可插拔后端向"当前获得焦点的窗口"发送按键事件——X11 会话走 XTEST；Wayland 会话走 `wtype`（合成器虚拟键盘协议）或 `ydotool`（`/dev/uinput`）。换句话说，它等同于"有人坐在你的键盘前打字"，只是打得更快、更整齐。

请把本工具当作**特权工具**对待。

---

## 1. 威胁模型

### 它能做什么（也就是风险面）

| 能力 | 风险 |
|---|---|
| 向任意焦点窗口发送任意按键 | 能完成你在该窗口能做的一切：执行命令、提交表单、确认对话框、删除文件 |
| 输入多行文本 | 在终端里等于**逐行执行 shell 命令** |
| 临时改写一个空闲 keycode 的键盘映射（仅 X11 后端） | 如果程序被强制杀死，可能留下异常键位（不影响数据） |
| 模拟组合键 | 可触发快捷键（如 `ctrl+alt+t` 打开终端、`ctrl+s` 保存） |
| `--window` 激活指定窗口 | 可把焦点从你当前的窗口切走；激活后会轮询确认焦点，失败则退出 |
| `--paste-mode` 读写系统剪贴板 | 会把 payload 写进全局剪贴板，并默认尝试备份/恢复原内容；剪贴板管理器可能留存历史 |
| 以固定 argv 调用外部工具 `wtype` / `ydotool`（仅 Wayland 后端） | 工具本身的能力边界：`ydotool` 需要 `/dev/uinput` 权限，能向内核注入任意按键 |
| 以固定 argv 调用 `xclip` / `xsel` / `wl-copy`（仅显式 `--paste-mode`） | 这些工具能读整个剪贴板；本程序只读一次用于备份，随后写入 payload |

### 它不能做什么

- **不联网**：没有任何 socket / HTTP 调用（`--window`/`--paste-mode` 也不联网）。
- **默认不读剪贴板**：不加 `--paste-mode` 时完全不碰 X selection / Wayland clipboard；
  内容只来自命令行参数、`--file`、stdin 或 REPL 输入。加了 `--paste-mode` 才会
  读一次原剪贴板用于备份，并在结束后恢复。
- **不执行 shell**：没有 `os.system` / `eval`。Wayland 后端会以固定 argv 调用外部命令 `wtype` / `ydotool`（Python `subprocess`，`shell=False`），参数数组直传、**从不经过 shell**；输入文本只作为 `wtype`/`ydotool` 的命令行参数传递，不进入任何其他通道。X11 后端不 spawn 进程（`--paste-mode` 的剪贴板工具是唯一例外）。
- **不读外部数据**：不访问网络；除显式 `--paste-mode` 外不读剪贴板；Wayland 后端仅探测 `wtype`/`ydotool` 的可执行文件与本地 socket 是否存在。
- **不写文件**：除了 REPL 里用 `:f` 读文件，不会创建或修改任何文件。
- **不提升权限**：默认拒绝 root 运行。

### 不适用的场景

以下情况**不要**用 AutoPaste：

- 无人值守的自动化（CI、cron、远程 orchestrator）——它需要人盯着屏幕确认焦点窗口；
- 把来自不可信来源的文本（网页抓取、别人发来的内容、上游 API 返回）直接喂给它；
- 会话里有你不想让它碰的敏感窗口（密码管理器、SSH 会话、银行页面）；
- 共享/多人使用的机器；
- 需要"保证不误操作"的关键流程。

---

## 2. 已内建的安全措施

| # | 措施 | 位置 |
|---|---|---|
| 1 | **拒绝 root**：`geteuid() == 0` 直接退出（除非 `--allow-root`） | `main()` |
| 2 | **目标可视化**：倒计时显示 `[WM_CLASS] 窗口标题`，REPL 提示符里也带着当前目标 | `focused_window()` / `countdown()` |
| 3 | **控制字符拦截**：默认拒绝除 `\n` `\t` 外的 C0 控制字符与 DEL（终端转义序列可被用于伪造输出、OSC 52 写剪贴板等），需要时 `--allow-control` | `validate_payload()` |
| 4 | **多行二次确认**：X11 下目标 `WM_CLASS` 命中常见终端且内容含换行时要求确认；Wayland 下读不到焦点，**所有多行输入一律要求确认**（收紧而非放宽）；非交互环境（无 tty）默认拒绝，`-y` 可显式跳过 | `multiline_risk()` / `_confirm()` |
| 5 | **dry-run**：`--dry-run` 只打印目标、字符数、转义预览，不发出任何 XTEST 事件 | `main()` |
| 6 | **输入长度上限**：单次 1 MiB（stdin 也按上限截断读取），拖延误喂 `yes` 之类的无限流 | `MAX_PAYLOAD` |
| 7 | **参数有界**：`--delay` ≤ 5s、`--sleep` ≤ 3600s，避免长时间占用会话 | `_num()` |
| 8 | **只读普通文件**：`--file` / `:f` 拒绝 FIFO、设备、目录，避免阻塞或读到无限流 | `check_regular_file()` |
| 9 | **异常安全**：`try/finally` 保证修饰键一定释放、临时键盘映射一定还原；`SIGINT/SIGTERM/SIGHUP` 与 `atexit` 双重兜底；`close()` 幂等 | `Typer._tap()` / `press()` / `close()` |
| 10 | **不做无边界读取**：REPL 的括号粘贴缓冲也受 1 MiB 限制 | `LineEditor._read_paste()` |
| 11 | **后端选择可预测**：真 X11 会话永远走 x11；Wayland 默认按 wtype → ydotool 探测，GNOME 则反序（mutter 不支持 virtual-keyboard 协议），失败给出具体安装命令；显式 `--backend` 不做静默跨会话回退 | `detect_session()` / `choose_backend()` |
| 12 | **ydotool 仅接受 ASCII 字面量**：不能可靠输入 Unicode，遇到非 ASCII 直接拒绝并给替代方案，绝不静默丢字；调用 `type` 时固定传 `-e 0` 关闭上游的反斜杠转义，`\n`/`\t`/`\xNN` 只作为字面字符发送，不会在工具侧"复活"成控制字符 | `YdotoolBackend.type_text()` |
| 13 | **无 X 库也能安全降级**：libX11/libXtst 惰性加载，纯 Wayland/CI 上 `--help`/`--version`/`--dry-run` 不会因缺库崩溃 | `_load_x11()` |
| 14 | **`--window` 只敲定向窗口**：EWMH/XQueryTree 枚举 → pattern 匹配 → 激活后轮询 `XGetInputFocus`（含 parent 链）确认焦点；倒计时结束再复核一次，焦点离开立即取消（退出 3），绝不退回当前焦点 | `resolve_window()` / `activate_window_x11()` / `main()` |
| 15 | **歧义不猜**：多个窗口匹配且当前激活窗口不在其中时，非交互环境退出 2 并列出候选；交互环境必须手动选择 | `pick_window()` |
| 16 | **XID 复用检测**：激活前重读 WM_CLASS，与解析时不一致就拒绝 | `_same_window()` |
| 17 | **剪贴板 fail-closed**：`--paste-mode` 默认先备份（仅文本、≤4 MiB），备份失败/超限/非文本就退出 2；只有显式 `--no-clipboard-backup` 才覆盖；恢复放在 `finally`，按粘贴键抛异常也会恢复 | `paste_text()` |
| 18 | **`--dry-run` 零副作用**：不激活窗口、不读写剪贴板、不发送按键 | `main()` |
| 19 | **批量注入有界**：X11 分块 flush，服务端最多排队 250ms；`--delay` 在批量模式下有 1ms 下限；`--safe`（或 `USE_XTEST_DELAY=False`）可退回逐字符 | `X11Backend.type_text()` |

---

## 3. 本工具防不了什么（务必知晓）

1. **焦点被抢**：从按回车到开始输入之间（倒计时内），任何程序都可能抢走焦点。**用 `-s` 留足时间、看清提示里的目标窗口**，这是最重要的一条操作习惯。
2. **多行文本在终端会被执行**：确认对话框只是"提醒 + 要求确认"，不能判断内容是否恶意。
3. **`kill -9` 之后**：临时键位映射可能残留（仅 X11 后端），重新登录图形会话即可恢复（数据不受影响）。
4. **X11 本身没有隔离**：同一 X 服务器上的任意客户端都可以抓键、读窗口内容。AutoPaste 不改变这一点，也不会削弱它（它不启用任何全局抓键）。
5. **输入法/第三方软件**：fcitx5 等 XIM 程序可能看到你在输入什么，这是输入法的固有行为，与本工具无关。
6. **无障碍/辅助技术**：XTEST 注入与真实键盘一致，某些安全软件可能不区分二者。
7. **Wayland 下读不到焦点窗口**：`wtype`/`ydotool` 后端无法像 X11 那样查询目标窗口，倒计时只显示"Wayland 会话（看不到焦点窗口）"。这降低了"敲错窗口"的可视化保护，请务必自己确认焦点；多行输入会强制确认。
8. **ydotool 的内核级权限**：`ydotool` 需要用户对 `/dev/uinput` 可写（通常在 `input` 组）。这个权限本身允许向系统注入任意输入事件，请只授予你自己的用户，不要用 `sudo` 运行本程序。`wtype` 依赖合成器实现 `zwp_virtual_keyboard_manager_v1`（wlroots 系与 KWin 支持，GNOME/mutter 不支持；因此 GNOME 下 `auto` 优先选 ydotool，wtype 运行期报"协议不支持"时只会明确报错、不会静默重试）。`wtype` 只接受自己的修饰键名，本程序在发送前把 `super`/`win`/`meta` 映射为 `logo`，其余未知修饰键直接拒绝。
9. **`--window` 的焦点抢占**：激活请求可能被窗口管理器拒绝（i3/sway 的 `focus_on_window_activation`、KWin 的焦点窃取防护、GNOME 策略）。程序会确认失败并退出 2；但"激活成功"也不代表窗口内容可信——仍是你在该窗口里能做的任何操作。
10. **`--paste-mode` 的剪贴板风险**：payload 会进入**全局剪贴板**，同会话的任何程序/剪贴板管理器都能读取并留存历史；恢复原内容不等于擦除。剪贴板里的图片/大对象无法安全备份时默认拒绝覆盖。某些应用不响应 `Ctrl+V`，本工具无法检测是否真的贴上。
11. **批量注入的残留窗口**：为降低每字符开销，X11 事件会成批排队（≤250ms）。`Ctrl-C`/`SIGTERM` 后最多还会再送出约 250ms 的按键，无法撤回；对时序敏感的场合用 `--safe`。

---

## 4. 报告安全问题

如果发现可被利用的缺陷（例如能让 AutoPaste 绕过 root 检查、绕过控制字符拦截，或在异常路径上留下被按住的修饰键），请**不要直接开公开 issue**：

- 邮箱：`1908499668@qq.com`
- 或使用 GitHub 的 [Private vulnerability reporting](https://github.com/lkjing2021345/AutoPaste/security/advisories/new)

请附：版本号、`--dry-run` 输出、复现步骤、以及你认为的影响范围。一般 72 小时内回复。
