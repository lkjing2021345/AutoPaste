#!/bin/sh
# 把 autopaste 软链到 PATH 里（默认 ~/.local/bin）
set -eu

src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/autopaste.py
dest=${1:-$HOME/.local/bin}

[ -f "$src" ] || { echo "找不到 $src" >&2; exit 1; }
mkdir -p "$dest"
chmod +x "$src"
ln -sf "$src" "$dest/autopaste"

echo "已安装: $dest/autopaste -> $src"
case ":$PATH:" in
  *":$dest:"*) ;;
  *) echo "提示: $dest 不在 PATH 里，把它加进 shell 配置即可。" ;;
esac
