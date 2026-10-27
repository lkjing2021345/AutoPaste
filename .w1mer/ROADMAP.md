# {project} — Roadmap

> Section order in the file = priority order (the template puts performance
> first, backlog last); custom sections work the same way — `list` and
> `re-rank` follow file order.
> Within a section, open rows are ranked by `Defer` (desc), then ID.
> `Defer` counts how many times the task was skipped (`w1mer defer <id>`).
> Each completed task: mark `[x]` + date + commit, add a one-line measured
> effect summary and `done`; full data goes to the results archive.
> Insert new items with derived sub-ids (`05.1`, `05.1.2`); never batch-renumber.

## ID rules

- Unbounded rolling hierarchical IDs: `01`, `05`, `05.1`, `05.1.1`, ...
- Children branch deeper on demand (sub-batch fixes, review follow-ups).
- Ordering: pre-order traversal (parent first, children right behind).
- Cross-references stay stable; derived ids link review/results/bug docs.

## Status states

`todo` `done` `reviewed` `issue`

## 一、Performance

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:perf -->

## 二、Bug fixes

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:bug -->
| 02.3 | 后端文档/诊断对齐（SECURITY #9、REPL 多行、DISPLAY、list-backends） | SECURITY #9 的 Typer._tap/press/close 已不存在且释放保证只适用 X11；#4/README/CHANGELOG 的『所有多行必确认』与 REPL 只警告不符；README -y 未写 Wayland 全多行；auto 无 DISPLAY 时诊断只讲 Wayland；--list-backends 在 XWayland/库缺失时结论矛盾；CHANGELOG 的 parse_combo 共享说法超前。逐条修正并补文档契约测试。 | todo | 0 |
| 02.4 | 后端边界测试与 _run 编码健壮性 | 补 --list-backends 在 CDLL 抛 OSError 下的用例（现有 test_help_works_without_libx11 未触发 _load_x11）；补 ydotool 反斜杠文本与 -e 0 断言；_run(text=True) 显式 encoding=utf-8/errors=replace，避免非 UTF-8 locale 读 stderr 抛 UnicodeDecodeError。 | todo | 0 |
| 03.1 | REPL 窗口指令缺少倒计时后焦点复核（盲打风险） | repl() 里 :w/:win 走 activate()+countdown() 后直接 type_text，没有像 CLI 主路径那样复核 is_window_focused；与 SECURITY.md #14 不符。并入任务 04 一起修（同日改 repl()）。 | done | 0 | 2026-10-27 / 214d6e8: REPL 提交前复核焦点，失败跳过本次注入并提示 |
| 03.2 | countdown 把 stdin EOF 当成按键，管道 + -s 立即取消 | select 对已 EOF 的 stdin 恒可读，os.read 返回 b'' 被当作按键取消，导致 README 的 cat note.txt \| autopaste -s 3 - 直接退 130。并入任务 04 修。 | done | 0 | 2026-10-27 / 214d6e8: countdown 读到 stdin EOF 后停止监听，倒计时不再被误取消 |
| 01.1 | 修复 pip/pipx 入口绕过信号处理 | console_scripts 指向 autopaste:main，而 _install_signals() 只在 __main__ 分支调用；安装后 SIGTERM/SIGHUP 直接终止，atexit 不执行，SECURITY.md #9 的键位还原承诺失效。新增 cli() 入口（安装信号后调用 main）或把 _install_signals 移入 main，并补一条入口级测试。 | reviewed | 0 | 2026-10-27 / 0b4c6e9：新增 console_main，pip 安装入口也注册 SIGINT/SIGTERM/SIGHUP 并返回 130；新增 4 条入口测试 |
| 02.1 | wtype 修饰键名映射与 evdev 键码表补全 | wtype 的 -M 只认 shift/capslock/ctrl/logo/win/alt/altgr，MODIFIERS 里的 super/meta 被原样传入导致 --backend wtype -k super+l 失败；在 WtypeBackend.press 前做名字映射。EVDEV 补 backslash(43) 等符号键，asterisk 改为 shift+8。补对应 argv 测试。 | done | 0 | ydotool -e 0 + wtype 修饰键 + GNOME 顺序 |
| 02.1.1 | ydotool -e 0 需按版本/能力探测（<1.0.4 不支持 -e） | R_02_1 | reviewed | 0 | 2026-10-27 / d52a247：ydotool 初始化探测 -e 能力，旧版不传 -e 且拒绝反斜杠文本，capabilities/--list-backends 暴露 escapes_supported |
| 02.2 | GNOME Wayland auto 选择与 wtype 失败回退 | choose_backend 未实现探索计划 §3.2：XDG_CURRENT_DESKTOP 含 GNOME 时应 ydotool 优先；auto 下 wtype 因协议不支持失败时应提示并回退 ydotool（显式 --backend wtype 不回退）。补测试。 | done | 0 | 并入 02.1 |








## 三、Features

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:feature -->
| 02 | 后端抽象 + Wayland 支持（wtype/ydotool） | 把 X11 调用抽成 Backend 接口，新增 --backend auto\|x11\|wtype\|ydotool；auto 依据 XDG_SESSION_TYPE/工具存在性选择；统一 Typer API（type_text/press/capabilities），X11 行为不变，纯 Wayland 可用；缺工具时给出可执行的安装提示。 | done | 0 | 2026-10-27 / 981c9bd：抽象+wtype+ydotool 三后端落地，119 测试全绿，X11 dry-run 与旧版逐行一致 |
| 03 | 目标窗口定向 + 输入提速（--window / --paste-mode） | 新增 --window 按 WM_CLASS/标题匹配并激活目标窗口（XGetInputFocus 校验、找不到就报错退出）；新增 --paste-mode 用 xclip/xsel + ctrl+v 发送大段文本；X11 路径改为批量 XTEST 事件 + 单次 flush 降低每字符开销；保持逐字符模式的精确性。 | done | 0 | 241 tests; --window/--paste-mode/批量提速 |


## 四、Infrastructure

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:infra -->
| 01 | 工程化基线：pyproject 打包 + unittest 测试套件 + CI | 让项目可 pip/pipx 安装（console_scripts=autopaste），把版本号收敛到单一来源；新增 tests/ 用假后端覆盖 payload 校验、参数解析、REPL 行编辑、键盘映射还原，要求整套测试无需 DISPLAY 即可跑；CI 增加测试 job。 | reviewed | 0 | 62 项 unittest、pyproject 可安装、CI 双 job |
| 01.2 | 让 Python 3.8 支持声明与 CI 一致 | pyproject requires-python/classifiers 与 README 宣称 >=3.8，但 CI 仅测 3.9–3.13。二选一：在 ubuntu-22.04 增加 3.8 测试 job（并纳入 check/tests 矩阵），或把下限与文档统一提升到 3.9。静态扫描显示当前代码无 3.9+ 运行时语法，先补验证即可。 | reviewed | 0 | 2026-10-27 / 357b543：tests 矩阵补 3.8（ubuntu-22.04），README 中英文同步 CI 覆盖版本 |
| 01.3 | 补强基线测试：dry-run 零注入、remap 时序、超限粘贴契约 | 三个假绿/弱断言：(1) test_dry_run 只断言退出码，Typer 为 MagicMock，未断言 type_text/press 未调用；(2) FakeX11 与 FakeXtst 各记一条历史，无法发现 restore 发生在 tap 之前；(3) 超限粘贴测试把 MAX_PAYLOAD+1 固化，且未验证剩余字节是否被消费。改用共享事件日志、显式 assert_not_called、定义并验证 drain/truncate 契约。 | reviewed | 0 | 2026-10-27 / 158b2a9：dry-run 零注入断言、共享 EventLog 断言 remap→tap→restore、粘贴契约改为截断+排空到终止序列；135 测试全绿 |




## 五、Backlog

(Items awaiting re-prioritization)
<!-- w1mer:task:backlog -->
| 04 | REPL 与诊断增强（历史/预览/保存、--verbose、配置文件） | REPL 增加上下键历史、:p 预览、:w 保存到文件；新增 --verbose/--quiet 与统一日志出口；支持 ~/.config/autopaste/config.toml 设置默认 delay/sleep/backend；退出码与错误信息统一。 | done | 0 | 2026-10-27 / 739985f: 历史/:p/:w/Log/配置/退出码统一落地，329 测试全绿 |
| 05 | 文档体系与 1.1.0 发布 | docs/ 拆分（usage/backends/security/design），README 精简为入口并链接细节；CHANGELOG 补齐 1.1.0；更新 SECURITY.md 覆盖新后端与 --window；打 tag v1.1.0 并推送。 | todo | 0 |
