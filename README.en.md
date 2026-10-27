# AutoPaste

Type text into **the focused window** by injecting real keystrokes: XTEST on X11,
`wtype` or `ydotool` on Wayland. Pure Python standard library, no third-party
packages, no root.

**Unlike xdotool**, AutoPaste focuses on one job: typing one payload safely — the
countdown shows the target, `--window` verifies focus, terminal-bound multi-line text
needs confirmation, control characters are rejected. A REPL and an opt-in clipboard
path cover large text.

> ⚠️ **Security warning**: equivalent to typing on your keyboard; multi-line text sent
> to a terminal runs commands. Only use it while you can see the screen.
> See [SECURITY.md](SECURITY.md).

```bash
autopaste 'hello world'         # type into the focused window
autopaste -s 3 'text'           # 3-second countdown (any key cancels)
cat note.txt | autopaste -s 3 - # read stdin
autopaste                       # interactive REPL
autopaste --list-backends       # backend availability / detection reasons
```

## Install

```bash
git clone git@github.com:lkjing2021345/AutoPaste.git
cd AutoPaste && pipx install .   # or ./install.sh
```

Linux + Python ≥ 3.8. X11 works out of the box; Wayland needs `wtype` (wlroots/KWin)
or `ydotool` (GNOME; `ydotoold` + `/dev/uinput`). On GNOME, `auto` prefers `ydotool`.

## Documentation

The reference docs are in Chinese and generated/checked from source:
[usage](docs/usage.md) · [CLI reference](docs/cli.md) · [backends](docs/backends.md) ·
[design](docs/design.md) · [index](docs/README.md) · [SECURITY](SECURITY.md) ·
[CHANGELOG](CHANGELOG.md). Entry point: [README.md](README.md).

Exit codes: `0` success, `1` payload/value validation, `2` argparse usage /
config / environment / safety refusal, `3` cancelled at a safety prompt
(or focus left the `--window` target), `130` interrupted.

## License

[MIT](LICENSE) © 2026 lkjing2021345
