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
| 以固定 argv 调用外部工具 `wtype` / `ydotool`（仅 Wayland 后端） | 工具本身的能力边界：`ydotool` 需要 `/dev/uinput` 权限，能向内核注入任意按键 |

### 它不能做什么

- **不联网**：没有任何 socket / HTTP 调用。
- **不读剪贴板**：不碰 X selection，内容只来自命令行参数、`--file`、stdin 或 REPL 输入。
- **不执行 shell**：没有 `os.system` / `eval`。Wayland 后端会以固定 argv 调用外部命令 `wtype` / `ydotool`（Python `subprocess`，`shell=False`），参数数组直传、**从不经过 shell**；输入文本只作为 `wtype`/`ydotool` 的命令行参数传递，不进入任何其他通道。X11 后端完全不 spawn 进程。
- **不读外部数据**：不读剪贴板、不访问网络；Wayland 后端仅探测 `wtype`/`ydotool` 的可执行文件与本地 socket 是否存在。
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

---

## 4. 报告安全问题

如果发现可被利用的缺陷（例如能让 AutoPaste 绕过 root 检查、绕过控制字符拦截，或在异常路径上留下被按住的修饰键），请**不要直接开公开 issue**：

- 邮箱：`1908499668@qq.com`
- 或使用 GitHub 的 [Private vulnerability reporting](https://github.com/lkjing2021345/AutoPaste/security/advisories/new)

请附：版本号、`--dry-run` 输出、复现步骤、以及你认为的影响范围。一般 72 小时内回复。
