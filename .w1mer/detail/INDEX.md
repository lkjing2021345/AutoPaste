# Detail Index (detail/)

> Dynamic layer: recent changes, field-level details, concerns — anything too
> volatile for the stable codebase layer. Each detail gets its own file; this
> INDEX is a build artifact (`w1mer build`).

## Details

| ID | Title | State | Doc |
|----|-------|-------|-----|
<!-- w1mer:rows -->
| "01" | 01 实施记录 | active | [01_01_实施记录.md](01_01_实施记录.md) |
| "02" | 后端抽象 + Wayland 支持（wtype/ydotool）— 实施计划 | active | [02_backend-abstraction.md](02_backend-abstraction.md) |
<!-- /w1mer:rows -->

## Lifecycle

- Created via `w1mer new detail --title "..."`.
- A periodic **compact** promotes settled details into the stable `codebase/`
  docs, then the detail is archived.
- `CHANGES.md` holds recent architecture-impact deltas written by reviewers.
