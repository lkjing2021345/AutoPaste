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
- **REPL history, preview and save** — `↑`/`↓` recall (inline prefix search), `:p` previews the last payload without sending anything, `:w <file>` saves it (mode 0600, symlinks refused). History lives in `$XDG_STATE_HOME/autopaste/history`; disable with `--no-history`.
- **Config file and log levels** — `~/.config/autopaste/config.toml` (flat TOML subset) sets `delay/sleep/backend/auto_enter/paste_mode`; CLI wins over config. Security switches such as `allow_root` are rejected with exit 2. `-q/--quiet` keeps the countdown target line and all warnings; `-v/--verbose` prints diagnostics.
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
| `-v, --verbose` | diagnostics (config source, backend, capabilities, timing); exclusive with `-q` |
| `-q, --quiet` | keep only the countdown target line, warnings, confirmations and errors |
| `--config PATH` | explicit config file |
| `--no-config` | ignore any config file |
| `--no-history` | do not read or write REPL history (does not even create the directory) |

Exit codes: `0` success, `1` payload/value validation failure (including unreadable
files), `2` usage/config/environment/safety refusal (argparse errors, root, no
backend, bad or insecure config, `--repl` without a tty), `3` user cancelled at a
safety confirmation (or focus left the `--window` target), `130` interrupted.

Ambiguous `--window` matches never guess: if the currently active window is among
the matches it is used, otherwise candidates are printed and the tool exits 2
(interactive terminals get a numbered prompt). `--dry-run` never activates a
window and never touches the clipboard. `--paste-mode` writes to the global
clipboard; clipboard managers may keep history — restore is not erase.

Special key names: `enter tab esc backspace delete insert home end pageup pagedown up down left right space f1`–`f12`; modifiers `ctrl alt shift super`/`win`/`meta` (the wtype backend maps `super`/`win`/`meta` to its `logo` modifier).

## REPL

Run without arguments. `:s <sec>` countdown, `:d <sec>` per-char delay, `:nl`
toggle auto-Enter, `:k <combo>`, `:f <file>`, `:r` repeat, `:t` status/target,
`:p` preview (sends nothing), `:w <file>` save the last payload (0600), `:win
<pattern>` target a window (`:win -` clears), `:h` help, `:q` quit. `↑`/`↓`
(Ctrl-P/Ctrl-N) browse history; text already on the line is used as a prefix
filter and `↓` returns to the draft. Multi-line entries are recalled as a
collapsed `[历史 N 字符 / M 行]` block.

History is stored in plain text in `$XDG_STATE_HOME/autopaste/history`
(`~/.local/state/autopaste/history` by default, 0600, last 300 entries); use
`--no-history` when pasting secrets.

### Config file

`~/.config/autopaste/config.toml` (or `$XDG_CONFIG_HOME/...`) supports the flat
subset `delay`, `sleep`, `backend`, `auto_enter`, `paste_mode`. CLI > config >
default. Security switches (`allow_root`, `allow_control`, `yes`,
`no_restore_clipboard`, and `allow-*` spellings) are rejected with exit 2; the
file must be a regular file owned by the current user and not group/other
writable, otherwise the tool refuses to run rather than silently ignoring it.

## Limitations

- Wayland needs an external tool: `wtype` (compositor must support virtual-keyboard; GNOME/mutter does not) or `ydotool` (ASCII literal text only, `ydotoold` + `/dev/uinput`). ydotool is always called with `-e 0`, so backslashes are never decoded into control characters. On GNOME, `auto` picks ydotool first; if wtype reports an unsupported protocol the program fails with a `--backend ydotool` hint instead of silently retrying.
- The focused window cannot be inspected on Wayland, so every multi-line payload requires confirmation there. `--window`/`--list-windows` need the X11 backend (Wayland cannot see X11 windows); requesting them elsewhere exits 2 instead of silently ignoring the flag.
- Input methods (fcitx5/ibus) may swallow individual keys under some XIM apps — `export XMODIFIERS=` helps.
- Follows the active keyboard layout; unmappable characters use a temporary keycode remap.
- `--window` activation may be blocked by the WM (focus-stealing prevention); the tool then exits 2 rather than typing into whatever held the focus.
- REPL history is written in plain text (0600) and may be read by your user, backups or sync tools; use `--no-history` for sensitive input.
- `--paste-mode` briefly replaces the global clipboard. Some apps ignore `Ctrl+V`; the tool cannot tell whether the paste actually happened. Batch injection can emit up to ~250ms of already-queued keys after an interrupt.

## License

[MIT](LICENSE) © 2026 lkjing2021345
