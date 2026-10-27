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

## 三、Features

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:feature -->



## 四、Infrastructure

| # | Task | Doc | Status | Defer | Effect |
|---|------|-----|--------|-------|--------|
<!-- w1mer:task:infra -->
| 01 | 工程化基线：pyproject 打包 + unittest 测试套件 + CI | 让项目可 pip/pipx 安装（console_scripts=autopaste），把版本号收敛到单一来源；新增 tests/ 用假后端覆盖 payload 校验、参数解析、REPL 行编辑、键盘映射还原，要求整套测试无需 DISPLAY 即可跑；CI 增加测试 job。 | done | 0 | 2026-10-27 716ddca · pipx/pip 可安装（版本唯一来源 autopaste.__version__）；62 个 unittest 无 DISPLAY 全绿；CI 增 tests/package job |


## 五、Backlog

(Items awaiting re-prioritization)
<!-- w1mer:task:backlog -->
| 02 | 后端抽象 + Wayland 支持（wtype/ydotool） | 把 X11 调用抽成 Backend 接口，新增 --backend auto\|x11\|wtype\|ydotool；auto 依据 XDG_SESSION_TYPE/工具存在性选择；统一 Typer API（type_text/press/capabilities），X11 行为不变，纯 Wayland 可用；缺工具时给出可执行的安装提示。 | todo | 0 |
| 03 | 目标窗口定向 + 输入提速（--window / --paste-mode） | 新增 --window 按 WM_CLASS/标题匹配并激活目标窗口（XGetInputFocus 校验、找不到就报错退出）；新增 --paste-mode 用 xclip/xsel + ctrl+v 发送大段文本；X11 路径改为批量 XTEST 事件 + 单次 flush 降低每字符开销；保持逐字符模式的精确性。 | todo | 0 |
| 04 | REPL 与诊断增强（历史/预览/保存、--verbose、配置文件） | REPL 增加上下键历史、:p 预览、:w 保存到文件；新增 --verbose/--quiet 与统一日志出口；支持 ~/.config/autopaste/config.toml 设置默认 delay/sleep/backend；退出码与错误信息统一。 | todo | 0 |
| 05 | 文档体系与 1.1.0 发布 | docs/ 拆分（usage/backends/security/design），README 精简为入口并链接细节；CHANGELOG 补齐 1.1.0；更新 SECURITY.md 覆盖新后端与 --window；打 tag v1.1.0 并推送。 | todo | 0 |


