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
- **Secure defaults** — refuses root, rejects control characters, rejects FIFOs/devices, double-checks multi-line text aimed at a terminal. See [SECURITY.md](SECURITY.md).

## Install

```bash
git clone git@github.com:lkjing2021345/AutoPaste.git
cd AutoPaste && ./install.sh     # symlink to ~/.local/bin/autopaste
```

Requirements: Linux, Python ≥ 3.8. X11 works out of the box; Wayland needs `wtype` (recommended; wlroots/KWin) or `ydotool` (GNOME; requires `ydotoold` and `/dev/uinput` access).

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
| `-y, --yes` | skip the "terminal + multi-line" confirmation |
| `--allow-control` | allow control characters (ESC etc.), rejected by default |
| `--allow-root` | allow running as root (not recommended) |

Special key names: `enter tab esc backspace delete insert home end pageup pagedown up down left right space f1`–`f12`; modifiers `ctrl alt shift super`.

## REPL

Run without arguments. `:s <sec>` countdown, `:d <sec>` per-char delay, `:nl` toggle auto-Enter, `:k <combo>`, `:f <file>`, `:r` repeat, `:t` status/target, `:h` help, `:q` quit.

## Limitations

- Wayland needs an external tool: `wtype` (compositor must support virtual-keyboard; GNOME/mutter does not) or `ydotool` (ASCII only, `ydotoold` + `/dev/uinput`).
- The focused window cannot be inspected on Wayland, so every multi-line payload requires confirmation there.
- Input methods (fcitx5/ibus) may swallow individual keys under some XIM apps — `export XMODIFIERS=` helps.
- Follows the active keyboard layout; unmappable characters use a temporary keycode remap.

## License

[MIT](LICENSE) © 2026 lkjing2021345
