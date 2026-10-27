---
id: CHANGES
title: Recent changes (architecture-impact deltas)
state: active
---

# Recent Changes

> Architecture-impact deltas recorded by reviewers during main batches.
> A periodic compact merges these into the stable `codebase/` docs and clears
> this log. One cache invalidation per compact — keep stable periods long.

## Deltas

> One bullet per delta, tagged with the target module doc:
> `- [gc] contract X moved`. Tags = module names from
> `codebase/INDEX.md`. Untagged → unassigned.
> `w1mer sync` shows a grouped preview; `w1mer sync --apply` writes + clears.

- [autopaste] packaging contract added: project is now an installable package (`py-modules=["autopaste"]`, console entry `autopaste:main`, dynamic version from `autopaste.__version__`); script and installed entry diverge on `_install_signals()` (only `__main__` installs it).
- [autopaste] test seam: `tests/` replaces module-level `autopaste.x11`/`autopaste.xtst` with fakes; no production abstractions added. Fake histories are per-backend, so remap/tap ordering is not observable (review M4).
- [ci] CI gains headless `tests` job (3.9/3.13) and `package` job; `requires-python >=3.8` remains untested (review M2).
- [autopaste] backend contract added: `Backend` + `X11Backend`/`WtypeBackend`/`YdotoolBackend`; `Typer` is now a pure façade (delay/close/focus/capabilities forwarding, no `.dpy`). New shared selectors `detect_session`/`choose_backend`/`make_backend`, `parse_combo`, `target_label`, `multiline_risk`, and the `capabilities()` dict (`can_focus_info`/`unicode`/`needs_remap`) become the cross-backend/UI contract.
- [autopaste] `x11`/`xtst` are no longer import-time globals: they are lazily assigned module globals (`_load_x11`, `none` until first X11 backend). Tests still patch `autopaste.x11`/`autopaste.xtst`; keep that seam in sync if backends are later split into a package.
- [autopaste] new process boundary: all Wayland injection goes through `_run()` (fixed argv, `shell=False`, `check=False`); ydotool `type` must carry `-e 0` because upstream enables backslash-escape by default — `validate_payload` no longer covers control chars/newlines for that backend (review B1).
- [autopaste] external-tool argv is now versioned, not universal: `-e 0` exists only in ydotool >=1.0.4 (older releases fail or treat `0` as text), so `YdotoolBackend` needs a capability/version probe before adding the flag (review R_02_1 N1).
- [autopaste] backend vocabularies split: shared `MODIFIERS` stays user-facing/X11 keysyms, while `WTYPE_MODIFIERS` maps `super/win/meta -> logo` for wtype before argv construction. Backend selection gained a desktop dimension (`XDG_CURRENT_DESKTOP` contains GNOME ⇒ ydotool→wtype); runtime wtype protocol errors never retry another backend (no duplicate input).
- [autopaste] single 1544-line module still appropriate; defer package split (`autopaste/backends/*`, `cli.py`) until task 03/04 adds window targeting/paste-mode.
- [autopaste] entry contract unified on `console_main(argv) -> int`: script guard, `python -m autopaste` and the pip/pipx console script (`pyproject [project.scripts] = "autopaste:console_main"`) all install SIGINT/SIGTERM/SIGHUP before `main()`; signal installation is now an entry-point duty, `main()` stays side-effect-free for library/test callers. `Typer.close()` remains the atexit restore path.
- [autopaste] external-tool argv became capability-driven: `YdotoolBackend.escapes_supported` is probed once in `__init__` via `ydotool type --help` (fail-closed on error/rc≠0/empty stdout), drives whether `-e 0` is emitted, rejects `\`/C0/DEL on non-supporting versions, and is exposed through `capabilities()` and `--list-backends` via the same `_ydotool_escapes()`.
- [autopaste] bracketed-paste contract changed: `LineEditor._read_paste()` now truncates to exactly `MAX_PAYLOAD` bytes, drains the remaining stream to `\x1b[201~` (bounded by another MAX_PAYLOAD) and warns on stderr; the terminator is always consumed. Byte cap (paste) and character cap (`validate_payload`) are separate limits.
- [ci] `tests` matrix runs the full suite on Python 3.8/ubuntu-22.04 in addition to 3.9/3.13, matching `requires-python >=3.8`.
- [test-infra] `tests/support.py` gained a shared `EventLog` used by both FakeX11 and FakeXtst, making remap/tap/restore ordering observable; dry-run is now guarded by `assert_not_called` on every injection method.
