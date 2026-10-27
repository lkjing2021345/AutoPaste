# AutoPaste

Type text into **any focused X11 window** by injecting keystrokes through the XTEST extension. Pure Python standard library — no `xdotool`, no root, no dependencies.

```bash
autopaste "hello world"        # type it into the focused window
autopaste -s 3 "text"          # 3-second countdown, so you can switch windows
autopaste                      # interactive REPL: paste multi-line text as-is
```

## Why

Clipboard-based pasting fails in terminals that block it, in VMs/remote desktops, in password-less paste boxes, and in apps that read raw key events. AutoPaste sends real key events, so it works anywhere a keyboard works.

## Features

- **Zero dependencies** — `ctypes` + `libX11` + `libXtst` only, no clipboard, no shell.
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

Requirements: Linux, X11 session, Python ≥ 3.8. Native Wayland is not supported (XTEST only reaches the X server); use `ydotool` there.

## CLI

| Option | Meaning |
|---|---|
| `-d, --delay` | delay between characters (default `0.008`s, max 5) |
| `-s, --sleep` | countdown before typing (REPL: default per submission) |
| `-f, --file` | read payload from a regular file (max 1 MiB) |
| `-n, --no-newline` | strip trailing newline of file/stdin payload |
| `-k, --key` | key or combo, repeatable |
| `--dry-run` | show target window and payload preview, send nothing |
| `-y, --yes` | skip the "terminal + multi-line" confirmation |
| `--allow-control` | allow control characters (ESC etc.), rejected by default |
| `--allow-root` | allow running as root (not recommended) |

Special key names: `enter tab esc backspace delete insert home end pageup pagedown up down left right space f1`–`f12`; modifiers `ctrl alt shift super`.

## REPL

Run without arguments. `:s <sec>` countdown, `:d <sec>` per-char delay, `:nl` toggle auto-Enter, `:k <combo>`, `:f <file>`, `:r` repeat, `:t` status/target, `:h` help, `:q` quit.

## Limitations

- X11 only (XWayland-hosted X clients work; native Wayland does not).
- Input methods (fcitx5/ibus) may swallow individual keys under some XIM apps — `export XMODIFIERS=` helps.
- Follows the active keyboard layout; unmappable characters use a temporary keycode remap.

## License

[MIT](LICENSE) © 2026 lkjing2021345
