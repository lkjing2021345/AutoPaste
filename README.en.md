# AutoPaste

Type text into **the focused window** by injecting keystrokes: XTEST on X11, `wtype` or `ydotool` on Wayland. Pure Python standard library — no third-party packages, no root.

```bash
autopaste "hello world"        # type it into the focused window
autopaste -s 3 "text"          # 3-second countdown, so you can switch windows
autopaste                      # interactive REPL: paste multi-line text as-is
```

## Why

Clipboard-based pasting fails in terminals that block it, in VMs/remote desktops, in password-less paste boxes, and in apps that read raw key events. AutoPaste sends real key events, so it works anywhere a keyboard works.

## Features

- **Zero Python dependencies** — `ctypes` + `libX11`/`libXtst` on X11; on Wayland it execs `wtype`/`ydotool` with a fixed argv (never through a shell). No clipboard access.
- **Any Unicode** — CJK, emoji, full-width punctuation (temporarily remaps a free keycode, then restores it).
- **REPL with bracketed paste** — paste multi-line code without quoting/escaping anything.
- **Key combos** — `-k ctrl+alt+t`, arrows, `F1`–`F12`, `Home`, `End`, …
- **Shows the target window** during the countdown, so you know where keystrokes go.
- **Window targeting** — `--window class:firefox` activates a matching window (WM_CLASS/title/PID/regex) and verifies the focus landed there; `--list-windows` prints a greppable TSV table.
- **Large payloads** — `--paste-mode` puts the text on the clipboard and sends one paste shortcut (Ctrl+Shift+V in terminals), backing up and restoring the previous clipboard.
- **Faster X11 injection** — batched XTest events with one flush per chunk; `--safe` keeps the old per-character timing.
- **Secure defaults** — refuses root, rejects control characters, rejects FIFOs/devices, double-checks multi-line text aimed at a terminal. See [SECURITY.md](SECURITY.md).

## Install

```bash
git clone git@github.com:lkjing2021345/AutoPaste.git
cd AutoPaste && ./install.sh     # symlink to ~/.local/bin/autopaste
```

Requirements: Linux, Python ≥ 3.8 (CI runs the full suite on 3.8, 3.9 and 3.13). X11 works out of the box; Wayland needs `wtype` (recommended; wlroots/KWin) or `ydotool` (GNOME; requires `ydotoold` and `/dev/uinput` access). On GNOME, `auto` prefers `ydotool` (mutter lacks the virtual-keyboard protocol wtype needs); other desktops prefer `wtype`.

## CLI

| Option | Meaning |
|---|---|
| `-d, --delay` | delay between characters (default `0.008`s, max 5) |
| `-s, --sleep` | countdown before typing (REPL: default per submission) |
| `-f, --file` | read payload from a regular file (max 1 MiB) |
| `-n, --no-newline` | strip trailing newline of file/stdin payload |
| `--backend` | `auto` \| `x11` \| `wtype` \| `ydotool` (default `auto`) |
| `--list-backends` | show availability, versions and detection reasons |
| `-k, --key` | key or combo, repeatable |
| `--dry-run` | show target window and payload preview, send nothing |
| `--safe` | X11: send one char at a time (flush+sleep), no batching |
| `-y, --yes` | skip the "terminal + multi-line" confirmation |
| `--allow-control` | allow control characters (ESC etc.), rejected by default |
| `--allow-root` | allow running as root (not recommended) |
| `--window PATTERN` | target a window: case-insensitive substring, or `class:` `title:` `pid:` `id:` `re:` |
| `--list-windows` | list windows (TSV) and exit; combinable with `--window` |
| `--json` | JSON output for `--list-windows` |
| `--paste-mode` | write payload to the clipboard and send one paste shortcut |
| `--paste-key COMBO` | override the paste shortcut |
| `--paste-wait SEC` | wait before restoring the clipboard (default 0.5–3s by size) |
| `--no-clipboard-backup` | do not back up/restore the clipboard (default: fail closed) |

Exit codes: `0` success, `1` bad arguments/safety block, `2` environment or
target-window unavailable / clipboard not safely backupable, `3` user cancelled
or focus left the `--window` target before typing, `130` interrupted.

Ambiguous `--window` matches never guess: if the currently active window is among
the matches it is used, otherwise candidates are printed and the tool exits 2
(interactive terminals get a numbered prompt). `--dry-run` never activates a
window and never touches the clipboard. `--paste-mode` writes to the global
clipboard; clipboard managers may keep history — restore is not erase.

Special key names: `enter tab esc backspace delete insert home end pageup pagedown up down left right space f1`–`f12`; modifiers `ctrl alt shift super`/`win`/`meta` (the wtype backend maps `super`/`win`/`meta` to its `logo` modifier).

## REPL

Run without arguments. `:s <sec>` countdown, `:d <sec>` per-char delay, `:nl` toggle auto-Enter, `:k <combo>`, `:f <file>`, `:r` repeat, `:t` status/target, `:h` help, `:q` quit.

## Limitations

- Wayland needs an external tool: `wtype` (compositor must support virtual-keyboard; GNOME/mutter does not) or `ydotool` (ASCII literal text only, `ydotoold` + `/dev/uinput`). ydotool is always called with `-e 0`, so backslashes are never decoded into control characters. On GNOME, `auto` picks ydotool first; if wtype reports an unsupported protocol the program fails with a `--backend ydotool` hint instead of silently retrying.
- The focused window cannot be inspected on Wayland, so every multi-line payload requires confirmation there. `--window`/`--list-windows` need the X11 backend (Wayland cannot see X11 windows); requesting them elsewhere exits 2 instead of silently ignoring the flag.
- Input methods (fcitx5/ibus) may swallow individual keys under some XIM apps — `export XMODIFIERS=` helps.
- Follows the active keyboard layout; unmappable characters use a temporary keycode remap.
- `--window` activation may be blocked by the WM (focus-stealing prevention); the tool then exits 2 rather than typing into whatever held the focus.
- `--paste-mode` briefly replaces the global clipboard. Some apps ignore `Ctrl+V`; the tool cannot tell whether the paste actually happened. Batch injection can emit up to ~250ms of already-queued keys after an interrupt.

## License

[MIT](LICENSE) © 2026 lkjing2021345
