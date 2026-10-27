# Detail Index (detail/)

> Dynamic layer: recent changes, field-level details, concerns — anything too
> volatile for the stable codebase layer. Each detail gets its own file; this
> INDEX is a build artifact (`w1mer build`).

## Details

| ID | Title | State | Doc |
|----|-------|-------|-----|
<!-- w1mer:rows -->
| "01" | "02 实施记录" | active | [01_02_实施记录.md](01_02_实施记录.md) |
| "02" | 后端抽象 + Wayland 支持（wtype/ydotool）— 实施计划 | active | [02_backend-abstraction.md](02_backend-abstraction.md) |
| "03" | 03 目标窗口定向 + 输入提速 — 实施计划 | active | [01_03_窗口定向与提速方案.md](01_03_窗口定向与提速方案.md) |
| "04" | 04 REPL 与诊断增强 — 实施计划 | active | [01_04_repl_与诊断增强方案.md](01_04_repl_与诊断增强方案.md) |
| "05" | 文档体系与 1.1.0 发布 — 可直接执行清单 | active | [05_文档体系与发布方案.md](05_文档体系与发布方案.md) |
<!-- /w1mer:rows -->

## Lifecycle

- Created via `w1mer new detail --title "..."`.
- A periodic **compact** promotes settled details into the stable `codebase/`
  docs, then the detail is archived.
- `CHANGES.md` holds recent architecture-impact deltas written by reviewers.
