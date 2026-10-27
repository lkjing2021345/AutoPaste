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
- [autopaste] single 1544-line module still appropriate; defer package split (`autopaste/backends/*`, `cli.py`) until task 03/04 adds window targeting/paste-mode.
