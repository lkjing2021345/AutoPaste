# 后端与能力矩阵

[← README](../README.md) · [文档索引](README.md)

Linux 桌面有 X11 与 Wayland 两套输入栈，AutoPaste 用可插拔后端适配：
`x11`（XTEST）、`wtype`（合成器虚拟键盘协议）、`ydotool`（`/dev/uinput`）。
矩阵由 `tools/gen_cli_docs.py` 从 `Backend.capabilities()` 生成，**不要手改**。

## 能力矩阵 {#backends-matrix}

<!-- BEGIN AUTO:matrix -->
| 后端 | 看焦点 | Unicode | 重映射 | 组合键 | 每字符延时 | 守护进程 | 窗口定向 | 转义支持 |
|---|---|---|---|---|---|---|---|---|
| `x11` | ✔ | ✔ | ✔ | ✔ | ✔ | ✘ | ✔ | ✔ |
| `wtype` | ✘ | ✔ | ✘ | ✔ | ✔ | ✘ | ✘ | ✔ |
| `ydotool` | ✘ | ✘ | ✘ | ✔ | ✔ | ✔ | ✘ | 按版本探测（≥1.0.4） |
<!-- END AUTO:matrix -->

- **看焦点**：能否查询当前焦点窗口 / 枚举窗口（Wayland 后端看不到 X11 窗口）。
- **重映射**：是否依赖临时改写空闲 keycode 来输入 Unicode（仅 X11）。
- **守护进程**：ydotool 需要 `ydotoold` 在跑，并找到它的 socket。
- **转义支持**：ydotool 的运行期探测结果；旧版（< 1.0.4）不支持 `-e`，程序会拒绝
  含反斜杠/C0 控制字符的文本，而不是静默打错。

## auto 选择顺序

1. `XDG_SESSION_TYPE` 优先：`wayland` / `x11`（XWayland 下有 `DISPLAY` 也不算 X11）。
2. 否则 `WAYLAND_DISPLAY` → wayland，`DISPLAY` → x11，否则 unknown。
3. 真 X11 → `x11`，**永不跨会话回退**。
4. Wayland：`XDG_CURRENT_DESKTOP` 含 `GNOME` → `ydotool` → `wtype`；
   其他桌面 → `wtype` → `ydotool`。ydotool 还必须找到 socket
   （`YDOTOOL_SOCKET` / `$XDG_RUNTIME_DIR/.ydotool_socket` / `/tmp/.ydotool_socket`）。
5. 都不可用 → 退出 2，并给出发行版安装命令。
6. 显式 `--backend` 原样使用，不做静默跨会话回退。

## 安装与权限

| 会话 | 后端 | 需要什么 |
|---|---|---|
| X11 | `x11` | 系统已有 `libX11.so.6` / `libXtst.so.6`（几乎所有发行版默认安装）；不需要 root |
| Wayland（wlroots/KWin） | `wtype` | `wtype` 可执行文件；合成器需支持 `zwp_virtual_keyboard_manager_v1` |
| Wayland（GNOME 等） | `ydotool` | `ydotool` + 运行中的 `ydotoold`；当前用户对 `/dev/uinput` 可写（通常在 `input` 组） |

```bash
sudo pacman -S wtype ydotool          # Arch
sudo apt install wtype ydotool        # Debian/Ubuntu
sudo dnf install wtype ydotool        # Fedora
```

GNOME/mutter 不支持 wtype 依赖的 virtual-keyboard 协议，所以 GNOME 下 `auto` 优先
`ydotool`；若显式用 `--backend wtype` 并遇到"协议不支持"，程序会明确报错并提示改用
`ydotool`，不会静默重试。

## 读 `--list-backends`

```text
后端      可用  探测依据
x11        是    DISPLAY=:0，libX11/libXtst 可加载
wtype      否    未在 PATH 找到
ydotool    否    已安装但找不到 socket（ydotoold 未运行？）
当前会话: x11
auto 将选择: x11
```

最后两行恒为 `当前会话:` 与 `auto 将选择:`（无可用后端时显示
`（无可用后端）`）。探测依据随环境变化；ydotool 行的 `-e` 说明也按实际版本追加，
不要把它当固定文案。
