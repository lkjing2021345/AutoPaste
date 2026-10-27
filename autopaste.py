#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autopaste - 把文本"敲"进当前聚焦的窗口（多后端键盘注入，零依赖）

设计目标: 粘贴一段文本到"当前聚焦的窗口"，适用于任何支持键盘输入的程序
（浏览器、终端、IDE、聊天软件……），不需要 xdotool，不需要 root。

常用:
    autopaste                          # 交互 REPL（推荐，粘贴文本零转义）
    autopaste "hello world"            # 直接输入一段文本
    autopaste -s 3 "切窗口后自动输入"    # 倒数 3 秒再输入
    autopaste -f note.txt              # 输入整个文件
    cat note.txt | autopaste -s 3 -    # 从管道读取，倒数后输入
    autopaste -k ctrl+alt+t            # 模拟组合键
    autopaste --dry-run "check me"     # 只预览、不真的敲键
    autopaste --backend wtype "hi"     # 显式指定后端（auto|x11|wtype|ydotool）
    autopaste --list-backends          # 查看各后端可用性与探测依据
    autopaste --list-windows           # 列出可定向的窗口（TSV）
    autopaste --window class:kitty "hi"# 定向窗口并校验焦点（X11）
    autopaste --paste-mode -f big.txt  # 大段文本：写剪贴板 + 一次粘贴键
    autopaste --dry-run --window class:kitty "hi"  # 只预览，不激活/不碰剪贴板

后端: X11 用 XTEST；Wayland 用 wtype（虚拟键盘协议）或 ydotool（/dev/uinput，
仅 ASCII）。auto 规则: 真 X11 会话 -> x11；Wayland -> wtype -> ydotool。

REPL 内命令:
    :s 3          每次回车后的倒计时（秒，0 = 不等待）
    :d 0.02       每个字符之间的间隔（秒）
    :nl           切换"输完自动按回车"
    :k ctrl+v     发送组合键（同样先倒计时）
    :f note.txt   输入文件内容
    :r            重复上一次提交的内容
    :t            查看当前设置与目标窗口
    :h            帮助
    :q            退出

安全提示:
    * 本程序等价于"替你在键盘上打字"，请只在你能看到屏幕时使用。
    * 它拒绝以 root 身份运行（除非显式 --allow-root）。
    * 多行文本打进终端 = 逐行执行命令，因此 CLI 模式下会二次确认；
      Wayland 下读不到焦点窗口，所有多行输入一律要求确认（-y 可跳过）。
    * 完整威胁模型见 SECURITY.md。

zsh 提示: 命令行参数里的 ! 会被历史展开，含 ! 的文本请用单引号，
          或直接在 REPL 里粘贴（没有转义问题）。
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import ctypes
import ctypes.util
import dataclasses
import json
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import termios
import time
import tty
import unicodedata

__version__ = "1.0.0"

# ----------------------------------------------------------------- 安全常量
MAX_PAYLOAD = 1 << 20        # 单次输入上限 1 MiB，避免误喂超大文件/管道
MAX_DELAY = 5.0              # 每字符间隔上限 5s
MAX_COUNTDOWN = 3600.0       # 倒计时上限 1h

# ----------------------------------------------------------------- 历史常量
#: 历史只保存"能原样重发"的内容：超限条目宁可不存，也不存半截/摘要
HIST_MAX_ENTRIES = 300
HIST_MAX_ENTRY_BYTES = 8 * 1024
HIST_MAX_ENTRY_LINES = 200
HIST_MAX_FILE_BYTES = 1 << 20

# ----------------------------------------------------------------- 窗口常量
WINDOW_ACTIVATE_TIMEOUT = 1.5    # 激活后轮询焦点确认的总时长（秒）
FOCUS_POLL_INTERVAL = 0.05
FOCUS_WALK_LIMIT = 32            # 焦点沿 parent 链向上找的最大层数
TITLE_MAX_BYTES = 8 * 1024       # 单个窗口标题读取上限
PROP_MAX_ITEMS = 64              # EWMH 窗口列表最多取 64 项
X_CLIENT_MESSAGE = 33
SUBSTRUCT_MASK = 0x00080000 | 0x00100000   # SubstructureNotify|Redirect
#: 这些窗口类型不是键盘输入目标（对话框/工具窗口保留）。
SKIP_WINDOW_TYPES = {
    "_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK",
    "_NET_WM_WINDOW_TYPE_TOOLTIP", "_NET_WM_WINDOW_TYPE_MENU",
    "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU", "_NET_WM_WINDOW_TYPE_POPUP_MENU",
}

# ----------------------------------------------------------------- 剪贴板常量
CLIP_READ_TIMEOUT = 1.0      # 读剪贴板超时
CLIP_WRITE_TIMEOUT = 2.0     # 写剪贴板超时
CLIP_SETTLE = 0.08           # 等 selection owner 生效
MAX_CLIP_BACKUP = 4 << 20    # 4 MiB；超过就认为无法安全备份
MAX_PASTE_WAIT = 10.0
#: 这些终端用 Shift+Insert 粘贴（Ctrl+Shift+V 不生效）
PASTE_SHIFT_INSERT = {
    "xterm", "uxterm", "urxvt", "rxvt", "st", "st-256color", "termite",
}
#: 能安全当作文本备份的 clipboard targets（大小写不敏感精确匹配）
TEXT_TARGETS = {
    "utf8_string", "string", "text", "text/plain", "text/plain;charset=utf-8",
    "text/plain;charset=utf8", "text/html", "text/x-moz-url",
}

# 会把换行当"执行"的终端程序（按 WM_CLASS，小写比较）
TERMINAL_CLASSES = {
    "xterm", "uxterm", "urxvt", "rxvt", "alacritty", "kitty", "wezterm",
    "konsole", "gnome-terminal", "gnome-terminal-server", "xfce4-terminal",
    "tilix", "terminator", "st", "st-256color", "foot", "qterminal",
    "lxterminal", "termite", "sakura", "guake", "yakuake", "tilda",
    "terminology", "hyper", "cool-retro-term", "kgx", "blackbox",
}

# ----------------------------------------------------------------- X11 绑定
# 惰性 dlopen：纯 Wayland 机器/没有 libXtst 的 CI 上，import 本模块与
# --help/--version/--dry-run 都不得因为缺 X 库而失败。
x11 = None
xtst = None

P = ctypes.POINTER


class XClassHint(ctypes.Structure):
    # 注意: 字段用 POINTER(c_char) 而不是 c_char_p —— c_char_p 字段在被读取时
    # 会被 ctypes 自动转成 Python bytes, 那样就无法再拿到原始指针去 XFree,
    # 强行 cast 会 free 掉 Python 自己的内存 (glibc: invalid size)。
    _fields_ = [("res_name", P(ctypes.c_char)),
                ("res_class", P(ctypes.c_char))]


class XWindowAttributes(ctypes.Structure):
    # 只用到 map_state / override_redirect，但必须按 libX11 的完整布局声明，
    # 否则字段偏移全错（读到随机的"映射状态"）。
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong),
        ("class_", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
    ]


class XClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20),
                ("s", ctypes.c_short * 10),
                ("l", ctypes.c_long * 5)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                ("data", XClientMessageData)]


class XEvent(ctypes.Union):
    # Xlib 的 XEvent 在 LP64 上是 24 个 long（192 字节）；缓冲区必须够大，
    # 否则 XSendEvent 会越界读写。
    _fields_ = [("type", ctypes.c_int), ("xclient", XClientMessageEvent),
                ("pad", ctypes.c_long * 24)]


def _load_x11() -> None:
    """按需加载 libX11/libXtst 并声明函数原型（幂等）。"""
    global x11, xtst
    if x11 is not None:
        return
    try:
        libx11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        libxtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
    except OSError as e:
        x11 = xtst = None
        raise BackendUnavailable(
            f"无法加载 X11 库（libX11/libXtst）：{e}\n"
            f"纯 Wayland 会话请用 --backend wtype 或 --backend ydotool。")

    libx11.XOpenDisplay.restype = ctypes.c_void_p
    libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    libx11.XFlush.argtypes = [ctypes.c_void_p]
    libx11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libx11.XFree.argtypes = [ctypes.c_void_p]
    libx11.XStringToKeysym.restype = ctypes.c_ulong
    libx11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    libx11.XKeysymToKeycode.restype = ctypes.c_ubyte
    libx11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    libx11.XDisplayKeycodes.argtypes = [ctypes.c_void_p, P(ctypes.c_int),
                                        P(ctypes.c_int)]
    libx11.XGetKeyboardMapping.restype = P(ctypes.c_ulong)
    libx11.XGetKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                           ctypes.c_int, P(ctypes.c_int)]
    libx11.XChangeKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_int, P(ctypes.c_ulong),
                                              ctypes.c_int]
    libx11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
    libx11.XkbKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                          ctypes.c_int, ctypes.c_int]
    libx11.XGetInputFocus.argtypes = [ctypes.c_void_p, P(ctypes.c_ulong),
                                      P(ctypes.c_int)]
    libx11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  P(ctypes.c_char_p)]
    libx11.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                     P(XClassHint)]

    # ---- 窗口枚举/激活（03.1）
    libx11.XDefaultRootWindow.restype = ctypes.c_ulong
    libx11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    libx11.XInternAtom.restype = ctypes.c_ulong
    libx11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                   ctypes.c_int]
    libx11.XGetWindowProperty.restype = ctypes.c_int
    libx11.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
        ctypes.c_long, ctypes.c_int, ctypes.c_ulong, P(ctypes.c_ulong),
        P(ctypes.c_int), P(ctypes.c_ulong), P(ctypes.c_ulong),
        P(P(ctypes.c_ubyte))]
    libx11.XQueryTree.restype = ctypes.c_int
    libx11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  P(ctypes.c_ulong), P(ctypes.c_ulong),
                                  P(P(ctypes.c_ulong)), P(ctypes.c_uint)]
    libx11.XGetWindowAttributes.restype = ctypes.c_int
    libx11.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                            P(XWindowAttributes)]
    libx11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    libx11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                      ctypes.c_int, ctypes.c_ulong]
    libx11.XSendEvent.restype = ctypes.c_int
    libx11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  ctypes.c_int, ctypes.c_long, P(XEvent)]
    libx11.XSetErrorHandler.restype = ctypes.c_void_p
    libx11.XSetErrorHandler.argtypes = [ctypes.c_void_p]

    libxtst.XTestFakeKeyEvent.restype = ctypes.c_int
    libxtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_ulong]

    x11, xtst = libx11, libxtst


def _require_x11() -> None:
    if x11 is None:
        raise BackendUnavailable(
            "X11 后端未初始化（当前进程没有加载 libX11）。"
            "纯 Wayland 会话请用 --backend wtype 或 --backend ydotool。")


NoSymbol = 0

SPECIAL = {
    "enter": "Return", "return": "Return", "tab": "Tab",
    "esc": "Escape", "escape": "Escape", "backspace": "BackSpace",
    "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "space": "space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}

MODIFIERS = {
    "ctrl": "Control_L", "control": "Control_L",
    "alt": "Alt_L", "shift": "Shift_L",
    "super": "Super_L", "win": "Super_L", "meta": "Super_L",
}

#: wtype 的 -M/-m 只接受它自己的修饰键名（见上游 mod_names[]），
#: 不接受 X11 的 Super_L 或 fancier 的 "super"；在发送前显式校验。
WTYPE_MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "shift": "shift",
    "super": "logo", "win": "logo", "meta": "logo",
}

KEYSYM_SHIFT = 0xFFE1  # Shift_L

#: 批量路径：服务端最多排队 250ms 的按键（Ctrl-C 后残留上限）
QUEUE_BUDGET_MS = 250
#: delay==0 时每发送多少字符 flush 一次
FLUSH_EVERY_NODELAY = 256
#: 某些 Xorg/驱动把 XTest 的 delay 实现成"服务器睡眠"（会卡整机输入）；
#: 遇到这种情况把它设为 False，退回"客户端 sleep"的保守路径（--safe 同理）。
USE_XTEST_DELAY = True


class Abort(Exception):
    """用户取消 / 安全检查拦截"""

    code = 1


class BackendError(Abort):
    """键盘后端运行期失败（外部工具报错等）"""


class BackendUnavailable(BackendError):
    """环境缺失：没装工具、没有会话、权限不足、socket 不存在等"""

    code = 2


class WindowUnavailable(Abort):
    """--window 找不到目标、或激活/焦点确认失败（退出码 2）"""

    code = 2


class WindowCancelled(Abort):
    """用户在候选列表里取消（退出码 3）"""

    code = 3


class ClipboardUnsafe(Abort):
    """剪贴板无法安全备份，拒绝覆盖（退出码 2）"""

    code = 2


class ClipboardError(Exception):
    """剪贴板读写失败（尚未按粘贴键时，调用方可降级为逐字符）。"""


class Log:
    """单一日志出口：所有诊断/警告/倒计时走 stderr，stdout 只放用户要的数据。

    级别：quiet < normal < verbose。``err`` 仅测试注入，**不得**在 import 期
    缓存 ``sys.stderr``（否则 ``redirect_stderr`` 失效）。
    """

    QUIET, NORMAL, VERBOSE = 0, 1, 2

    def __init__(self, level: int = 1, err=None):
        self.level = level
        self._err = err

    def _stream(self):
        return self._err if self._err is not None else sys.stderr

    def _emit(self, min_level: int, msg: str) -> None:
        if self.level < min_level:
            return
        stream = self._stream()
        stream.write(msg if msg.endswith("\n") else msg + "\n")
        stream.flush()

    def info(self, msg: str) -> None:       # normal+
        self._emit(self.NORMAL, msg)

    def detail(self, msg: str) -> None:     # verbose only
        self._emit(self.VERBOSE, msg)

    def warn(self, msg: str) -> None:       # normal+（错误同样保留）
        self._emit(self.QUIET, "⚠  " + msg)

    def error(self, msg: str) -> None:      # 永远
        self._emit(self.QUIET, msg)


LOG = Log()


def set_log(level: int) -> None:
    """原地改级别，保证测试里 patch 出来的 LOG 实例也生效。"""
    LOG.level = level


def _char_keysym(ch: str) -> int:
    """单个字符 -> X11 keysym（Latin-1 直接用码点，其余用 Unicode keysym）"""
    cp = ord(ch)
    return cp if 0x20 <= cp <= 0xFF else 0x01000000 + cp


def _decode(b) -> str | None:
    if not b:
        return None
    return b.decode("utf-8", "replace")


def focused_window(dpy) -> dict | None:
    """读取当前焦点窗口的标题/类名，用于让用户确认"会敲到哪里" """
    _require_x11()
    try:
        xid = focused_xid(dpy)
        if xid is None:
            return None
        info = _window_info_raw(dpy, xid)
        if info is None:
            return None
        return {"title": info.title or None, "name": info.instance or None,
                "class": info.cls or None}
    except Exception:
        return None


def describe_window(info: dict | None) -> str:
    if not info:
        return "未知窗口"
    cls = info.get("class") or info.get("name") or "?"
    title = (info.get("title") or "").strip()
    title = " ".join(title.split())
    if len(title) > 48:
        title = title[:47] + "…"
    return f"[{cls}] {title}" if title else f"[{cls}]"


def is_terminal(info: dict | None) -> bool:
    if not info:
        return False
    for key in ("class", "name"):
        v = (info.get(key) or "").lower()
        if v in TERMINAL_CLASSES:
            return True
    return False


# ------------------------------------------------- 窗口枚举/匹配/激活（X11 专用）
@dataclasses.dataclass(frozen=True)
class WindowInfo:
    """一个可定向的顶层客户端窗口。"""

    xid: int
    cls: str = ""            # WM_CLASS 第二段（res_class）
    instance: str = ""       # WM_CLASS 第一段（res_name）
    title: str = ""          # _NET_WM_NAME 优先，WM_NAME 兜底
    pid: int | None = None
    desktop: int | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _root(dpy) -> int:
    return x11.XDefaultRootWindow(dpy)


def _atom(dpy, name: str, only_if_exists: bool = False) -> int:
    return x11.XInternAtom(dpy, name.encode("ascii"), bool(only_if_exists))


def _attrs(dpy, win: int):
    """XGetWindowAttributes；窗口不存在返回 None（不抛）。"""
    a = XWindowAttributes()
    try:
        if not x11.XGetWindowAttributes(dpy, win, ctypes.byref(a)):
            return None
    except Exception:
        return None
    return a


def _prop(dpy, win: int, atom: int, max_bytes: int = TITLE_MAX_BYTES):
    """读一个属性：format=32 返回 list[int]，format=8 返回 bytes，缺失 None。"""
    if not atom:
        return None
    actual_type = ctypes.c_ulong(0)
    actual_format = ctypes.c_int(0)
    nitems = ctypes.c_ulong(0)
    bytes_after = ctypes.c_ulong(0)
    data = P(ctypes.c_ubyte)()
    try:
        status = x11.XGetWindowProperty(
            dpy, win, atom, 0, max(1, max_bytes // 4), False, 0,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(bytes_after),
            ctypes.byref(data))
    except Exception:
        return None
    if status != 0 or not data:
        if data:
            x11.XFree(ctypes.cast(data, ctypes.c_void_p))
        return None
    try:
        if actual_format.value == 32:
            arr = ctypes.cast(data, P(ctypes.c_ulong))
            return [int(arr[i]) for i in range(nitems.value)]
        return ctypes.string_at(data, nitems.value)
    finally:
        x11.XFree(ctypes.cast(data, ctypes.c_void_p))


def _prop32(dpy, win: int, name: str,
            limit: int = PROP_MAX_ITEMS) -> list[int] | None:
    atom = _atom(dpy, name, True)
    if not atom:
        return None
    vals = _prop(dpy, win, atom, max_bytes=limit * 8)
    return vals if isinstance(vals, list) else None


def _prop_text(dpy, win: int, name: str) -> bytes | None:
    atom = _atom(dpy, name, True)
    if not atom:
        return None
    raw = _prop(dpy, win, atom)
    return raw if isinstance(raw, bytes) else None


def _decode_title(raw: bytes) -> str:
    """WM_NAME 在老程序里可能是 latin-1：先严格 utf-8，再 latin-1。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


def _fetch_name(dpy, win: int) -> str | None:
    name = ctypes.c_char_p()
    try:
        if x11.XFetchName(dpy, win, ctypes.byref(name)) and name.value:
            return _decode_title(name.value)
        return None
    finally:
        if name:
            x11.XFree(ctypes.cast(name, ctypes.c_void_p))


def _class_hint(dpy, win: int) -> tuple[str, str]:
    hint = XClassHint()
    cls = inst = ""
    try:
        if not x11.XGetClassHint(dpy, win, ctypes.byref(hint)):
            return "", ""
        if hint.res_name:
            inst = ctypes.string_at(hint.res_name).decode("utf-8", "replace")
        if hint.res_class:
            cls = ctypes.string_at(hint.res_class).decode("utf-8", "replace")
    finally:
        for field in (hint.res_name, hint.res_class):
            if field:
                x11.XFree(ctypes.cast(field, ctypes.c_void_p))
    return cls, inst


def _window_info_raw(dpy, xid: int) -> WindowInfo | None:
    if _attrs(dpy, xid) is None:
        return None
    title = ""
    raw = _prop_text(dpy, xid, "_NET_WM_NAME")
    if raw is not None:
        title = raw.decode("utf-8", "replace")
    else:
        title = _fetch_name(dpy, xid) or ""
    cls, instance = _class_hint(dpy, xid)
    pids = _prop32(dpy, xid, "_NET_WM_PID", limit=1)
    pid = pids[0] if pids else None
    desktops = _prop32(dpy, xid, "_NET_WM_DESKTOP", limit=1)
    desktop = desktops[0] if desktops else None
    if desktop == 0xFFFFFFFF:        # "所有桌面"
        desktop = None
    if pid in (0, 0xFFFFFFFF):
        pid = None
    return WindowInfo(xid=xid, cls=cls, instance=instance, title=title,
                      pid=pid, desktop=desktop)


def _children(dpy, win: int) -> list[int]:
    root_ret = ctypes.c_ulong(0)
    parent_ret = ctypes.c_ulong(0)
    children = P(ctypes.c_ulong)()
    count = ctypes.c_uint(0)
    try:
        if not x11.XQueryTree(dpy, win, ctypes.byref(root_ret),
                              ctypes.byref(parent_ret),
                              ctypes.byref(children), ctypes.byref(count)):
            return []
        return [int(children[i]) for i in range(count.value)] if children else []
    except Exception:
        return []
    finally:
        if children:
            x11.XFree(ctypes.cast(children, ctypes.c_void_p))


def _parent_of(dpy, win: int) -> int | None:
    root_ret = ctypes.c_ulong(0)
    parent_ret = ctypes.c_ulong(0)
    children = P(ctypes.c_ulong)()
    count = ctypes.c_uint(0)
    try:
        if not x11.XQueryTree(dpy, win, ctypes.byref(root_ret),
                              ctypes.byref(parent_ret),
                              ctypes.byref(children), ctypes.byref(count)):
            return None
        return int(parent_ret.value) or None
    except Exception:
        return None
    finally:
        if children:
            x11.XFree(ctypes.cast(children, ctypes.c_void_p))


def _skip_window_type(dpy, xid: int) -> bool:
    """菜单/提示框/桌面/面板不是键盘输入目标。"""
    types = _prop32(dpy, xid, "_NET_WM_WINDOW_TYPE")
    if not types:
        return False
    skip = {_atom(dpy, name) for name in SKIP_WINDOW_TYPES}
    return any(t in skip and t != 0 for t in types)


def _walk_clients(dpy, win: int, depth: int = 0) -> list[int]:
    """XQueryTree 兜底枚举：viewable、非 override_redirect、有类名或标题。"""
    if depth > 16:
        return []
    out: list[int] = []
    for child in _children(dpy, win):
        a = _attrs(dpy, child)
        if a is None or a.override_redirect:
            continue
        if a.map_state == 2:                 # IsViewable
            cls, inst = _class_hint(dpy, child)
            if cls or inst or _prop_text(dpy, child, "_NET_WM_NAME"):
                out.append(child)
        out.extend(_walk_clients(dpy, child, depth + 1))
    return out


def list_windows_x11(dpy) -> list[WindowInfo]:
    """EWMH `_NET_CLIENT_LIST` 优先，缺失时 XQueryTree 递归兜底。

    返回顺序为叠放顺序（最上层在最后）。最小化窗口仍保留在 EWMH 列表里。
    """
    root = _root(dpy)
    clients = _prop32(dpy, root, "_NET_CLIENT_LIST", limit=1024)
    stacking = _prop32(dpy, root, "_NET_CLIENT_LIST_STACKING",
                       limit=1024) or []
    if not clients:
        clients = _walk_clients(dpy, root)
    order = {xid: i for i, xid in enumerate(stacking)}
    out = []
    for xid in clients:
        if _skip_window_type(dpy, xid):
            continue
        info = _window_info_raw(dpy, xid)
        if info and (info.cls or info.instance or info.title):
            out.append(info)
    out.sort(key=lambda w: order.get(w.xid, -1))
    return out


def _focus_raw(dpy) -> int | None:
    win = ctypes.c_ulong(0)
    rev = ctypes.c_int(0)
    try:
        x11.XGetInputFocus(dpy, ctypes.byref(win), ctypes.byref(rev))
    except Exception:
        return None
    if win.value in (0, 1):              # None / PointerRoot
        return None
    return int(win.value)


def focused_xid(dpy) -> int | None:
    """当前焦点 xid；焦点在客户端子窗口时向上找有 WM_CLASS 的祖先。"""
    xid = _focus_raw(dpy)
    depth = 0
    while xid and depth < FOCUS_WALK_LIMIT:
        cls, inst = _class_hint(dpy, xid)
        if cls or inst:
            return xid
        parent = _parent_of(dpy, xid)
        if not parent or parent == xid:
            break
        xid = parent
        depth += 1
    return xid


def active_window_x11(dpy) -> int | None:
    """`_NET_ACTIVE_WINDOW` 优先，缺失时退回真实输入焦点。"""
    vals = _prop32(dpy, _root(dpy), "_NET_ACTIVE_WINDOW", limit=1)
    if vals and vals[0] not in (0, 1):
        return int(vals[0])
    return focused_xid(dpy)


def parse_pattern(pat: str) -> tuple[str, str]:
    for prefix in ("class:", "title:", "pid:", "id:", "re:"):
        if pat.startswith(prefix):
            return prefix[:-1], pat[len(prefix):]
    return "any", pat


def match_windows(wins: list[WindowInfo], pattern: str) -> list[WindowInfo]:
    """默认大小写不敏感子串；`re:` 是唯一正则入口（显式 opt-in）。"""
    kind, text = parse_pattern(pattern)
    if kind == "id":
        try:
            xid = int(text, 0)
        except ValueError:
            raise Abort(f"--window id: 需要整数（十进制或 0x 十六进制）：{text!r}")
        return [w for w in wins if w.xid == xid]
    if kind == "pid":
        try:
            pid = int(text, 0)
        except ValueError:
            raise Abort(f"--window pid: 需要整数：{text!r}")
        return [w for w in wins if w.pid == pid]
    if kind == "re":
        try:
            rx = re.compile(text, re.IGNORECASE)
        except re.error as e:
            raise Abort(f"--window 正则无效（{pattern!r}）：{e}")
        return [w for w in wins
                if rx.search(f"{w.cls} {w.instance} {w.title}")]
    needle = text.casefold()
    nonempty = [w for w in wins if w.cls or w.instance or w.title]
    if kind == "class":
        return [w for w in nonempty
                if needle in w.cls.casefold() or needle in w.instance.casefold()]
    if kind == "title":
        return [w for w in nonempty if needle in w.title.casefold()]
    return [w for w in nonempty
            if any(needle in (f or "").casefold()
                   for f in (w.cls, w.instance, w.title))]


def _xid(xid: int) -> str:
    return f"0x{xid:08x}"


def _sanitize(s: str) -> str:
    """去掉会破坏 TSV / 终端转义的控制字符。"""
    return "".join(" " if (ord(c) < 32 or ord(c) == 127) else c
                   for c in (s or ""))


def format_window_row(info: WindowInfo, active_xid: int | None = None) -> str:
    """稳定、可 grep 的 TSV：id, class, pid, desktop, active, title。"""
    return "\t".join([
        _xid(info.xid),
        _sanitize(info.cls or info.instance or "?"),
        "-" if info.pid is None else str(info.pid),
        "-" if info.desktop is None else str(info.desktop),
        "*" if active_xid == info.xid else "-",
        _sanitize(info.title),
    ])


WINDOW_HEADER = "ID\tCLASS\tPID\tDESKTOP\tACTIVE\tTITLE"


def format_window_lines(wins: list[WindowInfo],
                        active_xid: int | None = None) -> list[str]:
    return [format_window_row(w, active_xid) for w in wins]


def windows_as_json(wins: list[WindowInfo],
                    active_xid: int | None = None) -> str:
    rows = []
    for w in wins:
        row = w.as_dict()
        row["id"] = _xid(w.xid)
        row["active"] = w.xid == active_xid
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def describe_window_info(info: WindowInfo | None) -> str:
    if info is None:
        return "未知窗口"
    cls = info.cls or info.instance or "?"
    title = " ".join((info.title or "").split())
    if len(title) > 48:
        title = title[:47] + "…"
    base = f"[{cls}] {title}" if title else f"[{cls}]"
    return f"{base} ({_xid(info.xid)})"


def _format_candidate(index: int, info: WindowInfo) -> str:
    cls = info.cls or info.instance or "?"
    pid = "-" if info.pid is None else str(info.pid)
    title = " ".join((info.title or "").split())
    if len(title) > 48:
        title = title[:47] + "…"
    return f"  {index}) {_xid(info.xid)}  {cls:<16} {pid:>7}  {title}"


def pick_window(matches: list[WindowInfo], *,
                active_xid: int | None = None,
                tty: bool = False) -> WindowInfo:
    """在候选中确定唯一目标；歧义时宁可报错/让人选，绝不静默猜。"""
    if not matches:
        raise WindowUnavailable(
            "没有匹配该 pattern 的窗口；用 --list-windows 查看候选，"
            "或换一个更宽的 pattern。")
    if len(matches) == 1:
        return matches[0]
    for w in matches:
        if active_xid is not None and w.xid == active_xid:
            sys.stderr.write(
                f"匹配到 {len(matches)} 个窗口，使用当前激活的 "
                f"{describe_window_info(w)}\n")
            return w
    ordered = list(reversed(matches))        # 最上层在前
    listing = "\n".join(_format_candidate(i, w)
                        for i, w in enumerate(ordered, 1))
    if not tty:
        raise WindowUnavailable(
            f"匹配到 {len(matches)} 个窗口且当前不是交互终端：\n{listing}\n"
            f"请改用 id:/pid:/更精确的 title: 指定目标。")
    while True:
        sys.stdout.write(f"匹配到 {len(matches)} 个窗口（最上层在前）：\n"
                         f"{listing}\n"
                         f"选择 [1-{len(ordered)}，回车=1，q=取消]: ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":                       # EOF
            raise WindowCancelled("已取消。")
        choice = line.strip()
        if choice.lower() in ("q", "quit"):
            raise WindowCancelled("已取消。")
        if choice == "":
            return ordered[0]
        try:
            n = int(choice)
        except ValueError:
            continue
        if 1 <= n <= len(ordered):
            return ordered[n - 1]


#: Xlib 默认错误处理器会 exit(1)：窗口在枚举与激活之间关闭时，
#: 必须临时吞掉 BadWindow/BadMatch，否则"报错退出"变成"进程猝死"。
_ERR_FUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_ERR_FUNC
def _ignore_x_error(dpy, event):
    return 0


@contextlib.contextmanager
def _x_trap(dpy):
    # 全局引用必须由模块持有（_ignore_x_error 已由装饰器固定），否则回调野指针
    old = x11.XSetErrorHandler(ctypes.cast(_ignore_x_error, ctypes.c_void_p))
    try:
        yield
    finally:
        try:
            x11.XSync(dpy, False)
        finally:
            x11.XSetErrorHandler(old)


def _client_message(win: int, message_type: int,
                    values) -> XEvent:
    ev = XEvent()
    ev.xclient.type = X_CLIENT_MESSAGE
    ev.xclient.serial = 0
    ev.xclient.send_event = 1
    ev.xclient.display = None
    ev.xclient.window = win
    ev.xclient.message_type = message_type
    ev.xclient.format = 32
    for i, v in enumerate(list(values)[:5]):
        ev.xclient.data.l[i] = v
    return ev


def _is_focused(dpy, xid: int) -> bool:
    """焦点等于 xid，或落在它的后代/重父化 frame 链上（≤32 层）。"""
    focus = _focus_raw(dpy)
    depth = 0
    while focus and depth < FOCUS_WALK_LIMIT:
        if focus == xid:
            return True
        parent = _parent_of(dpy, focus)
        if not parent or parent == focus:
            return False
        focus = parent
        depth += 1
    return False


def _wait_focus(dpy, xid: int, timeout: float,
                interval: float = FOCUS_POLL_INTERVAL) -> bool:
    end = time.time() + max(0.0, timeout)
    while True:
        if _is_focused(dpy, xid):
            return True
        if time.time() >= end:
            return False
        time.sleep(interval)


def _same_window(cur: WindowInfo, expected: WindowInfo) -> bool:
    """XID 复用检测：类名不一致就认为原窗口已关闭。"""
    if expected.cls or expected.instance:
        return (cur.cls, cur.instance) == (expected.cls, expected.instance)
    return cur.title == expected.title


def activate_window_x11(dpy, info: WindowInfo, *,
                        timeout: float = WINDOW_ACTIVATE_TIMEOUT,
                        interval: float = FOCUS_POLL_INTERVAL) -> bool:
    """激活目标窗口并轮询确认焦点；失败抛 WindowUnavailable（不降级）。"""
    cur = _window_info_raw(dpy, info.xid)
    if cur is None:
        raise WindowUnavailable(
            f"窗口 {_xid(info.xid)} 已关闭；请重新 --list-windows。")
    if not _same_window(cur, info):
        raise WindowUnavailable(
            f"{_xid(info.xid)} 已被另一个窗口复用（XID 复用），"
            f"请重新 --list-windows。")
    if _is_focused(dpy, info.xid):
        return True

    active = _atom(dpy, "_NET_ACTIVE_WINDOW", True)
    if active:
        ev = _client_message(info.xid, active, (2, 0, 0, 0, 0))
        with _x_trap(dpy):
            x11.XSendEvent(dpy, _root(dpy), False, SUBSTRUCT_MASK,
                           ctypes.byref(ev))
            x11.XFlush(dpy)
        if _wait_focus(dpy, info.xid, timeout, interval):
            return True

    # 兜底：没有 EWMH / WM 忽略激活请求时直接 raise + set focus
    with _x_trap(dpy):
        x11.XRaiseWindow(dpy, info.xid)
        x11.XSetInputFocus(dpy, info.xid, 2, 0)   # RevertToParent, CurrentTime
        x11.XFlush(dpy)
    if _wait_focus(dpy, info.xid, min(0.3, timeout), interval):
        return True
    raise WindowUnavailable(
        f"无法把焦点切换到 {_xid(info.xid)} {describe_window_info(info)}。\n"
        f"窗口管理器可能启用了焦点抢占防护（i3/sway 的 "
        f"focus_on_window_activation、KWin 的焦点窃取防护、GNOME 的激活策略）。\n"
        f"可选：1) 去掉 --window，改用 -s 3 手动切换；\n"
        f"      2) 在 WM 设置里允许 activation；\n"
        f"      3) 用 --window id:{_xid(info.xid)} 先确认窗口存在。")


# ------------------------------------------------------------ 输入内容校验
def validate_payload(text: str, allow_control: bool = False) -> None:
    if len(text) > MAX_PAYLOAD:
        raise Abort(f"内容过大（{len(text)} 字符 > 上限 {MAX_PAYLOAD}）")
    if allow_control:
        return
    bad = sorted({c for c in text
                  if (ord(c) < 32 and c not in "\n\t") or ord(c) == 127})
    if bad:
        shown = ", ".join(f"U+{ord(c):04X}" for c in bad[:8])
        raise Abort(
            f"内容含控制字符（{shown}…）。终端转义序列（ESC）等可能被滥用；"
            f"确认安全可加 --allow-control")


def check_regular_file(path: str) -> None:
    try:
        st = os.stat(path)
    except OSError as e:
        raise Abort(f"无法读取文件: {e}")
    if not stat.S_ISREG(st.st_mode):
        raise Abort("只接受普通文件（拒绝 FIFO/设备/目录，避免阻塞或误读）")
    if st.st_size > MAX_PAYLOAD:
        raise Abort(f"文件过大（{st.st_size} 字节 > 上限 {MAX_PAYLOAD}）")


# ------------------------------------------------------------------ 后端抽象
class Backend:
    """键盘注入后端契约。

    约定：
    * ``close()`` 必须幂等且不抛异常；
    * ``type_text`` / ``type_char`` / ``press`` 失败抛 ``BackendError``；
    * ``focus_info()`` 拿不到焦点时返回 ``None``，不得阻塞超过 100ms；
    * ``resolvable()`` 只供 dry-run 使用，不需要字符重映射的后端返回空列表。
    """

    name = "base"
    #: 批量发送事件（仅 X11 有实现；--safe 会把它置 False）
    batch_enabled = True

    def type_text(self, text: str) -> None:
        raise NotImplementedError

    def type_char(self, ch: str) -> None:
        self.type_text(ch)

    def press(self, combo: str) -> None:
        raise NotImplementedError

    def focus_info(self) -> dict | None:
        return None

    def capabilities(self) -> dict:
        return {"name": self.name, "can_focus_info": False,
                "can_paste_mode": False, "unicode": False,
                "needs_remap": False, "combos": False,
                "per_char_delay": False, "daemon": False,
                "escapes_supported": None, "window_control": False}

    def list_windows(self) -> list[WindowInfo]:
        """能看到窗口列表的后端返回之；看不到就返回空列表（不抛）。"""
        return []

    def activate_window(self, info: WindowInfo, *,
                        timeout: float = WINDOW_ACTIVATE_TIMEOUT) -> bool:
        """激活并确认焦点；不能激活返回 False（由调用方报错，不静默）。"""
        return False

    def focused_window_id(self) -> int | None:
        return None

    def is_window_focused(self, xid: int) -> bool:
        # 默认保守：不确定就当没焦点
        return False

    def resolvable(self, text: str) -> list[str]:
        return []

    def close(self) -> None:
        pass


class X11Backend(Backend):
    """XTEST 键盘注入（原 Typer 实现，行为保持不变）。"""

    name = "x11"

    def __init__(self, delay: float = 0.008, remap_wait: float = 0.02):
        _load_x11()
        self.dpy = x11.XOpenDisplay(None)
        if not self.dpy:
            raise BackendUnavailable("无法连接 X 服务器（DISPLAY=%s）"
                                     % os.environ.get("DISPLAY", "(未设置)"))
        self.delay = delay
        self.remap_wait = remap_wait
        self.shift_kc = x11.XKeysymToKeycode(self.dpy, KEYSYM_SHIFT)
        self.per = self._keysyms_per_keycode()
        self.spare_kc = self._find_spare_keycode()
        self._remapped = False
        self._closed = False

    # ------------------------------------------------------------- 查询
    def _keysyms_per_keycode(self) -> int:
        lo, hi = ctypes.c_int(), ctypes.c_int()
        x11.XDisplayKeycodes(self.dpy, ctypes.byref(lo), ctypes.byref(hi))
        n = ctypes.c_int()
        ks = x11.XGetKeyboardMapping(self.dpy, lo.value, 1, ctypes.byref(n))
        x11.XFree(ks)
        return n.value

    def _find_spare_keycode(self) -> int | None:
        lo, hi = ctypes.c_int(), ctypes.c_int()
        x11.XDisplayKeycodes(self.dpy, ctypes.byref(lo), ctypes.byref(hi))
        for kc in range(hi.value, lo.value - 1, -1):
            if all(x11.XkbKeycodeToKeysym(self.dpy, kc, g, l) == NoSymbol
                   for g in range(4) for l in range(2)):
                return kc
        return None

    def keycode_for(self, keysym: int) -> tuple[int | None, bool]:
        kc = x11.XKeysymToKeycode(self.dpy, keysym)
        if not kc:
            return None, False
        if x11.XkbKeycodeToKeysym(self.dpy, kc, 0, 0) == keysym:
            return kc, False
        if x11.XkbKeycodeToKeysym(self.dpy, kc, 0, 1) == keysym:
            return kc, True
        return kc, False

    def resolvable(self, text: str) -> list[str]:
        """dry-run 用: 返回无法直接映射、需要临时重映射的字符"""
        need = []
        for ch in text:
            if ch in "\n\t\r":
                continue
            kc, _ = self.keycode_for(_char_keysym(ch))
            if not kc and self.spare_kc is None:
                need.append(ch)
        return need

    # ------------------------------------------------------------- 注入
    def _tap(self, kc: int, shift: bool = False) -> None:
        d = self.dpy
        if shift and self.shift_kc:
            xtst.XTestFakeKeyEvent(d, self.shift_kc, True, 0)
        try:
            xtst.XTestFakeKeyEvent(d, kc, True, 0)
            xtst.XTestFakeKeyEvent(d, kc, False, 0)
        finally:
            if shift and self.shift_kc:
                xtst.XTestFakeKeyEvent(d, self.shift_kc, False, 0)
            x11.XFlush(d)

    def _remap(self, keysym: int) -> bool:
        if self.spare_kc is None:
            return False
        arr = (ctypes.c_ulong * (self.per * 2))()
        arr[0] = keysym
        arr[1] = keysym
        x11.XChangeKeyboardMapping(self.dpy, self.spare_kc, self.per, arr, 1)
        x11.XSync(self.dpy, False)
        self._remapped = True
        time.sleep(self.remap_wait)
        return True

    def _restore_keymap(self) -> None:
        if not self._remapped or self.spare_kc is None:
            return
        arr = (ctypes.c_ulong * (self.per * 2))()      # 全 NoSymbol
        x11.XChangeKeyboardMapping(self.dpy, self.spare_kc, self.per, arr, 1)
        x11.XSync(self.dpy, False)
        self._remapped = False

    def type_text(self, text: str) -> None:
        """批量发送：单次 flush + XTest 服务端 delay（--delay 语义不变）。

        与逐字符模式产生**完全相同**的 press/release 序列；只是不再每个
        字符都 flush 一次、也不在客户端 sleep。
        """
        if not (USE_XTEST_DELAY and self.batch_enabled):
            return self._type_text_per_char(text)
        delay_ms = _delay_ms(self.delay) if self.delay > 0 else 0
        budget = (max(1, QUEUE_BUDGET_MS // delay_ms) if delay_ms
                  else FLUSH_EVERY_NODELAY)
        n = 0
        try:
            for ch in text:
                if ch == "\n":
                    self._press_raw("enter", delay_ms)
                elif ch == "\t":
                    self._press_raw("tab", delay_ms)
                elif ch == "\r":
                    continue
                else:
                    kc, shift = self.keycode_for(_char_keysym(ch))
                    if kc:
                        self._tap_raw(kc, shift, delay_ms)
                    else:
                        # Unicode 需要 remap 往返 + 还原键位，不能与批量事件交错
                        x11.XFlush(self.dpy)
                        n = 0
                        self.type_char(ch)
                        continue
                n += 1
                if n >= budget:
                    x11.XFlush(self.dpy)
                    n = 0
        finally:
            x11.XFlush(self.dpy)     # 异常/中断也把已排队事件送出去

    def _type_text_per_char(self, text: str) -> None:
        """保守路径（--safe / USE_XTEST_DELAY=False）：保持旧行为。"""
        for ch in text:
            self.type_char(ch)

    def _tap_raw(self, kc: int, shift: bool = False, delay_ms: int = 0) -> None:
        """只排队事件、不 flush；延迟只加在按下事件上。"""
        d = self.dpy
        if shift and self.shift_kc:
            xtst.XTestFakeKeyEvent(d, self.shift_kc, True, 0)
        try:
            xtst.XTestFakeKeyEvent(d, kc, True, delay_ms)
            xtst.XTestFakeKeyEvent(d, kc, False, 0)
        finally:
            if shift and self.shift_kc:
                xtst.XTestFakeKeyEvent(d, self.shift_kc, False, 0)

    def _resolve_key(self, name: str):
        """解析按键名 -> (keycode, shift, 修饰键 keycode 列表, key)。"""
        tokens = name.split("+")
        mods = []
        for t in tokens[:-1]:
            t = t.lower().strip()
            if t not in MODIFIERS:
                raise Abort(f"未知修饰键: {t}")
            mods.append(x11.XKeysymToKeycode(
                self.dpy, x11.XStringToKeysym(MODIFIERS[t].encode())))
        key = tokens[-1].strip()
        low = key.lower()

        if low in MODIFIERS:
            ks_name, ks = MODIFIERS[low], None
        elif low in SPECIAL:
            ks_name, ks = SPECIAL[low], None
        elif len(key) == 1:
            ks_name, ks = None, _char_keysym(key)
        else:
            ks_name, ks = key, None

        if ks is None:
            ks = x11.XStringToKeysym(ks_name.encode())
            if not ks:
                raise Abort(f"未知按键: {key}")
        kc, shift = self.keycode_for(ks)
        return kc, shift, mods, key

    def _press_raw(self, name: str, delay_ms: int = 0) -> None:
        kc, shift, mods, key = self._resolve_key(name)
        if not kc:
            raise Abort(f"当前键盘布局中没有该键: {key}")
        pressed = []
        try:
            for m in mods:
                xtst.XTestFakeKeyEvent(self.dpy, m, True, 0)
                pressed.append(m)
            self._tap_raw(kc, shift, delay_ms)
        finally:
            for m in reversed(pressed):      # 异常时也不留按住的修饰键
                xtst.XTestFakeKeyEvent(self.dpy, m, False, 0)

    def type_char(self, ch: str) -> None:
        if ch == "\n":
            return self.press("enter")
        if ch == "\t":
            return self.press("tab")
        if ch == "\r":
            return
        ks = _char_keysym(ch)
        kc, shift = self.keycode_for(ks)
        if kc:
            self._tap(kc, shift)
        else:
            self._unicode(ch, ks)
        time.sleep(self.delay)

    def _unicode(self, ch: str, ks: int) -> None:
        if not self._remap(ks):
            raise Abort(f"当前键盘布局无法输入字符 {ch!r}")
        try:
            self._tap(self.spare_kc)
            time.sleep(self.delay)
        finally:
            self._restore_keymap()

    def press(self, name: str) -> None:
        """按键或组合键，例如 "enter" / "ctrl+alt+t" """
        kc, shift, mods, key = self._resolve_key(name)
        if not kc:
            if len(key) == 1:
                return self.type_char(key)
            raise Abort(f"当前键盘布局中没有该键: {key}")

        pressed = []
        try:
            for m in mods:
                xtst.XTestFakeKeyEvent(self.dpy, m, True, 0)
                pressed.append(m)
            self._tap(kc, shift)
        finally:
            for m in reversed(pressed):          # 异常时也不留按住的修饰键
                xtst.XTestFakeKeyEvent(self.dpy, m, False, 0)
            x11.XFlush(self.dpy)
        time.sleep(self.delay)

    # ------------------------------------------------------------- 元信息
    def focus_info(self) -> dict | None:
        return focused_window(self.dpy)

    def list_windows(self) -> list[WindowInfo]:
        return list_windows_x11(self.dpy)

    def activate_window(self, info: WindowInfo, *,
                        timeout: float = WINDOW_ACTIVATE_TIMEOUT) -> bool:
        return activate_window_x11(self.dpy, info, timeout=timeout)

    def focused_window_id(self) -> int | None:
        return active_window_x11(self.dpy)

    def is_window_focused(self, xid: int) -> bool:
        return _is_focused(self.dpy, xid)

    def capabilities(self) -> dict:
        return {"name": self.name, "can_focus_info": True,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": True, "combos": True,
                "per_char_delay": True, "daemon": False,
                "escapes_supported": True, "window_control": True}

    # ------------------------------------------------------------- 清理
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._restore_keymap()
        except Exception:
            pass
        try:
            x11.XCloseDisplay(self.dpy)
        except Exception:
            pass


# ------------------------------------------------------------- 外部命令接缝
def _run(argv, *, capture_stdout: bool = False):
    """所有外部命令的唯一出口（测试可整体打桩）。

    始终 check=False：由后端解析返回码并给出可读错误，绝不经过 shell。
    """
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _delay_ms(delay: float) -> int:
    """秒 -> 毫秒整数；0 表示不传 -d（避免不同 wtype/ydotool 版本语义差异）。"""
    if delay <= 0:
        return 0
    return max(1, int(round(min(delay, MAX_DELAY) * 1000)))


def _chunks(text: str, limit: int):
    """按 UTF-8 字节数分片，且绝不在多字节字符中间切断。"""
    buf: list[str] = []
    size = 0
    for ch in text:
        n = len(ch.encode("utf-8"))
        if buf and size + n > limit:
            yield "".join(buf)
            buf, size = [], 0
        buf.append(ch)
        size += n
    if buf:
        yield "".join(buf)


class WtypeBackend(Backend):
    """通过 wtype 向 Wayland 合成器注入按键（虚拟键盘协议）。"""

    name = "wtype"
    MAX_ARG = 32 * 1024            # 远低于 Linux 单参数 128 KiB 上限
    _PROTO_ERR = ("virtual keyboard", "protocol", "not supported",
                  "failed to create")

    def __init__(self, delay: float = 0.008, *, which=None, env=None,
                 runner=None):
        which = shutil.which if which is None else which
        if not which("wtype"):
            raise BackendUnavailable(
                "未找到 wtype。\n" + _missing_hint("wayland"))
        self.delay = delay
        self._run = runner or _run

    # ------------------------------------------------------------- 内部
    def _delay_args(self) -> list[str]:
        ms = _delay_ms(self.delay)
        return [] if ms == 0 else ["-d", str(ms)]

    def _exec(self, args: list[str]) -> None:
        argv = ["wtype", *self._delay_args(), *args]
        try:
            cp = self._run(argv)
        except FileNotFoundError:
            raise BackendUnavailable(
                "未找到 wtype（PATH 可能不完整）。\n" + _missing_hint("wayland"))
        rc = getattr(cp, "returncode", 0)
        if rc != 0:
            stderr = (getattr(cp, "stderr", "") or "").strip().splitlines()
            err = stderr[-1][:200] if stderr else "无输出"
            hint = ""
            low = err.lower()
            if any(mark in low for mark in self._PROTO_ERR):
                hint = ("（当前合成器可能不支持 virtual-keyboard 协议，"
                        "GNOME/mutter 就是如此。请改用 --backend ydotool："
                        "先 systemctl --user enable --now ydotoold；"
                        "本程序不会静默重试，避免重复输入。）")
            raise BackendError(f"wtype 失败（退出码 {rc}）：{err}{hint}")

    # ------------------------------------------------------------- 注入
    def type_text(self, text: str) -> None:
        if not text:
            return
        for chunk in _chunks(text, self.MAX_ARG):
            self._exec(["--", chunk])

    def press(self, combo: str) -> None:
        mods, key = parse_combo(combo)
        low = key.lower()
        if low in MODIFIERS:
            key = MODIFIERS[low]
        elif low in SPECIAL:
            key = SPECIAL[low]
        wmods: list[str] = []
        for mod in mods:
            mapped = WTYPE_MODIFIERS.get(mod)
            if mapped is None:  # parse_combo 已过滤，属于防御性检查
                raise Abort(f"wtype 后端不支持修饰键 {mod!r}")
            wmods.append(mapped)
        args: list[str] = []
        for mod in wmods:
            args += ["-M", mod]
        args += ["-k", key]
        for mod in reversed(wmods):
            args += ["-m", mod]
        self._exec(args)

    def capabilities(self) -> dict:
        return {"name": self.name, "can_focus_info": False,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": False,
                "escapes_supported": True, "window_control": False}


#: evdev keycode 表（ydotool key 用；只收常用键，未知键一律报错不猜）
EVDEV = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11, "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23,
    "o": 24, "p": 25, "leftbrace": 26, "rightbrace": 27,
    "enter": 28, "return": 28,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37,
    "l": 38, "semicolon": 39, "apostrophe": 40, "grave": 41,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "period": 52, "slash": 53,
    "asterisk": 55, "kpasterisk": 55, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "numlock": 69,
    "scrolllock": 70, "f11": 87, "f12": 88,
    "kp7": 71, "kp8": 72, "kp9": 73, "kpminus": 74, "kp4": 75, "kp5": 76,
    "kp6": 77, "kpplus": 78, "kp1": 79, "kp2": 80, "kp3": 81, "kp0": 82,
    "kpdot": 83, "102nd": 86,
    "rightctrl": 97, "rightalt": 100,
    "home": 102, "up": 103, "pageup": 104, "prior": 104,
    "left": 105, "right": 106, "end": 107, "down": 108,
    "pagedown": 109, "next": 109, "insert": 110, "delete": 111,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "leftmeta": 125, "super": 125, "win": 125, "meta": 125,
}

#: 修饰键名 -> evdev 码（与 MODIFIERS 的别名保持一致）
EVDEV_MOD = {"ctrl": 29, "control": 29, "shift": 42, "alt": 56,
             "super": 125, "win": 125, "meta": 125}


def _ydotool_socket(env=None, exists=os.path.exists) -> str | None:
    """按 ydotool 版本差异依次探测 socket。"""
    env = os.environ if env is None else env
    candidates = []
    if env.get("YDOTOOL_SOCKET"):
        candidates.append(env["YDOTOOL_SOCKET"])
    if env.get("XDG_RUNTIME_DIR"):
        candidates.append(os.path.join(env["XDG_RUNTIME_DIR"],
                                       ".ydotool_socket"))
    candidates.append("/tmp/.ydotool_socket")
    for path in candidates:
        if path and exists(path):
            return path
    return None


def _ydotool_socket_ok(env=None) -> bool:
    return _ydotool_socket(env) is not None


def _ydotool_escapes(runner=None) -> bool:
    """探测 ydotool 是否支持 ``-e/--escape``（v1.0.4 起才有）。

    v0.1.8（Debian 12 / Ubuntu 22.04/24.04）遇到 ``-e`` 直接报错退出；
    v1.0.0–1.0.3 会把 ``0`` 当成待输入文本静默多敲一个字符。两种情况都
    不能传 ``-e 0``，所以在后端初始化时探测一次并缓存。

    探测失败（帮助信息读不到、进程异常、非 0 退出）一律按"不支持"处理：
    宁可在含反斜杠/控制字符时拒绝输入，也不静默打错字。
    """
    run = runner or _run
    try:
        cp = run(["ydotool", "type", "--help"], capture_stdout=True)
    except Exception:
        return False
    if getattr(cp, "returncode", 1) != 0:
        return False
    out = (getattr(cp, "stdout", "") or "").lower()
    return "--escape" in out or "-e," in out


class YdotoolBackend(Backend):
    """通过 ydotool（/dev/uinput）注入按键；仅支持 ASCII 字面量。

    ``ydotool type`` 默认开启反斜杠转义（``\\n``/``\\t``/``\\xNN`` 会被
    解码成真实控制字符），因此每次调用都显式传 ``-e 0``，把文本按字面量
    交给工具——否则会绕过 ``validate_payload`` 的控制字符拦截与多行确认。

    ``-e`` 是 v1.0.4 才加入的选项：更老的版本要么报错退出（0.1.8），
    要么把 ``0`` 当文本敲出去（1.0.0–1.0.3）。初始化时探测一次
    ``ydotool type --help``，不支持就不传 ``-e``，并拒绝含反斜杠/控制
    字符的文本（它们会被老版本按字面量输入或根本无转义，语义不可控）。
    """

    name = "ydotool"
    MAX_ARG = 32 * 1024

    def __init__(self, delay: float = 0.008, *, which=None, env=None,
                 runner=None, socket=None, exists=None):
        env = os.environ if env is None else env
        which = shutil.which if which is None else which
        if not which("ydotool"):
            raise BackendUnavailable(
                "未找到 ydotool。\n" + _missing_hint("wayland"))
        if socket is None:
            socket = _ydotool_socket(env, exists or os.path.exists)
        if not socket:
            raise BackendUnavailable(
                "ydotoold 未运行（找不到 socket）。先启动守护进程：\n"
                "  systemctl --user enable --now ydotoold\n"
                "若仍失败，检查 /dev/uinput 权限与当前用户的 input 组：\n"
                "  sudo usermod -aG input $USER   # 或写 udev 规则给 /dev/uinput 0660\n"
                "（本程序拒绝以 root 运行，权限必须授予你自己的用户。）")
        self.delay = delay
        self.socket = socket
        self._run = runner or _run
        # 每个后端实例只探测一次；--list-backends 复用同一个探测函数
        self.escapes_supported = _ydotool_escapes(self._run)

    # ------------------------------------------------------------- 内部
    def _delay_args(self) -> list[str]:
        ms = _delay_ms(self.delay)
        return [] if ms == 0 else ["--key-delay", str(ms)]

    def _exec(self, args: list[str]) -> None:
        argv = ["ydotool", *args]
        try:
            cp = self._run(argv)
        except FileNotFoundError:
            raise BackendUnavailable(
                "未找到 ydotool（PATH 可能不完整）。\n" + _missing_hint("wayland"))
        rc = getattr(cp, "returncode", 0)
        if rc != 0:
            stderr = (getattr(cp, "stderr", "") or "").strip().splitlines()
            err = stderr[-1][:200] if stderr else "无输出"
            low = err.lower()
            if ("uinput" in low or "permission denied" in low
                    or "no such file" in low):
                hint = ("（检查 /dev/uinput 权限：sudo usermod -aG input $USER，"
                        "或写 udev 规则给 /dev/uinput 0660；然后重新登录）")
            else:
                hint = "（确认 ydotoold 正在运行：systemctl --user status ydotoold）"
            raise BackendError(f"ydotool 失败（退出码 {rc}）：{err}{hint}")

    def _keycode(self, key: str) -> int:
        low = key.lower()
        code = EVDEV_MOD.get(low, EVDEV.get(low))
        if code is None:
            raise Abort(f"ydotool 后端不支持按键 {key!r}，"
                        f"请改用 --backend wtype 或 x11")
        return code

    # ------------------------------------------------------------- 注入
    def type_text(self, text: str) -> None:
        bad = next((ch for ch in text if ord(ch) > 0x7E), None)
        if bad is not None:
            raise Abort(
                f"ydotool 无法可靠输入非 ASCII 字符（当前文本含 {bad!r}）。"
                f"GNOME Wayland + 中文请用 X11 会话，或安装 wtype 后 "
                f"用 --backend wtype；绝不静默丢字。")
        if not text:
            return
        if not self.escapes_supported:
            bad = next((ch for ch in text
                        if ch == "\\" or ord(ch) < 0x20 or ord(ch) == 0x7F),
                       None)
            if bad is not None:
                raise Abort(
                    f"当前 ydotool 版本不支持 -e/--escape，无法保证 {bad!r} "
                    f"按字面量输入（旧版会转义或把 -e 的参数当文本）。\n"
                    f"请升级 ydotool 到 >= 1.0.4，或改用 --backend wtype。")
        for chunk in _chunks(text, self.MAX_ARG):
            if self.escapes_supported:
                # -e 0：关闭 ydotool 的反斜杠转义，文本必须按字面量发送。
                self._exec(["type", *self._delay_args(), "-e", "0", "--",
                            chunk])
            else:
                # 老版本本来就没有转义功能，不能传它不认识的 -e。
                self._exec(["type", *self._delay_args(), "--", chunk])

    def press(self, combo: str) -> None:
        mods, key = parse_combo(combo)
        shift = len(key) == 1 and key.isupper()
        keycode = self._keycode(key.lower() if shift else key)
        codes = []
        for mod in mods:
            codes.append(f"{EVDEV_MOD[mod]}:1")
        if shift:
            codes.append(f"{EVDEV_MOD['shift']}:1")
        codes.append(f"{keycode}:1")
        codes.append(f"{keycode}:0")
        if shift:
            codes.append(f"{EVDEV_MOD['shift']}:0")
        for mod in reversed(mods):
            codes.append(f"{EVDEV_MOD[mod]}:0")
        self._exec(["key", *self._delay_args(), *codes])

    def capabilities(self) -> dict:
        return {"name": self.name, "can_focus_info": False,
                "can_paste_mode": False, "unicode": False,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": True,
                "escapes_supported": self.escapes_supported,
                "window_control": False}


# ------------------------------------------------------------- 后端选择
def detect_session(env=None) -> str:
    """返回 "x11" / "wayland" / "unknown"。

    XDG_SESSION_TYPE 优先于 DISPLAY：XWayland 下 DISPLAY 存在，但 XTEST
    事件到不了原生 Wayland 窗口，不能据此认定是 X11 会话。
    """
    env = os.environ if env is None else env
    st = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if st == "wayland":
        return "wayland"
    if st == "x11":
        return "x11"
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def _missing_hint(session: str) -> str:
    return (
        f"没有可用的键盘注入后端（会话类型：{session}）。\n"
        f"Wayland 需要 wtype 或 ydotool 之一，当前都没找到。\n\n"
        f"  Arch          : sudo pacman -S wtype ydotool\n"
        f"  Debian/Ubuntu : sudo apt install wtype ydotool\n"
        f"  Fedora        : sudo dnf install wtype ydotool\n\n"
        f"推荐先装 wtype（无需守护进程、无需额外权限）。\n"
        f"GNOME（mutter 不支持 virtual-keyboard 协议）请装 ydotool，然后：\n"
        f"  systemctl --user enable --now ydotoold\n"
        f"  sudo usermod -aG input $USER   # 或给 /dev/uinput 写 0660 udev 规则\n"
        f"（本程序拒绝以 root 运行，权限必须授予你自己的用户。）")


def choose_backend(requested: str = "auto", env=None, which=None,
                   socket_ok=None) -> str:
    """把 --backend 的取值解析成具体后端名。"""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    socket_ok = _ydotool_socket_ok if socket_ok is None else socket_ok
    if requested != "auto":
        return requested
    session = detect_session(env)
    if session == "x11":
        return "x11"
    if session == "wayland":
        # mutter 不实现 virtual-keyboard 协议，GNOME 上 wtype 必然失败，
        # 因此把 ydotool 放在前面；其他合成器仍优先无需守护进程的 wtype。
        desktop = (env.get("XDG_CURRENT_DESKTOP") or "").upper()
        order = ("ydotool", "wtype") if "GNOME" in desktop else \
                ("wtype", "ydotool")
        for name in order:
            if name == "wtype" and which("wtype"):
                return "wtype"
            if name == "ydotool" and which("ydotool") and socket_ok(env):
                return "ydotool"
    raise BackendUnavailable(_missing_hint(session))


def make_backend(name: str = "auto", *, delay: float = 0.008,
                 remap_wait: float = 0.02, which=None, env=None,
                 runner=None) -> Backend:
    resolved = choose_backend(name, env=env, which=which)
    if resolved == "x11":
        return X11Backend(delay=delay, remap_wait=remap_wait)
    if resolved == "wtype":
        return WtypeBackend(delay=delay, which=which, env=env, runner=runner)
    if resolved == "ydotool":
        return YdotoolBackend(delay=delay, which=which, env=env,
                              runner=runner)
    raise BackendUnavailable(f"未知后端: {resolved}")


# ------------------------------------------------------------------ 剪贴板
def _clip_run(argv, *, timeout=CLIP_READ_TIMEOUT, input_data=None):
    """剪贴板命令的唯一出口（argv 直传，绝不经过 shell）。"""
    return subprocess.run(argv, input=input_data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False,
                          timeout=timeout)


def _out_bytes(cp) -> bytes:
    out = getattr(cp, "stdout", b"") or b""
    return out.encode("utf-8") if isinstance(out, str) else out


def _out_text(cp) -> str:
    out = getattr(cp, "stdout", b"") or b""
    return out if isinstance(out, str) else out.decode("utf-8", "replace")


def _clip_stderr(cp) -> str:
    err = getattr(cp, "stderr", b"") or b""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return " ".join(err.strip().splitlines())[:200] or "无输出"


class Clipboard:
    """剪贴板提供者：读/写/列 targets。失败抛 ClipboardError。"""

    name = "?"

    def targets(self) -> list[str] | None:
        return None

    def read(self) -> bytes:
        raise ClipboardError("没有可用的剪贴板工具")

    def write(self, data: bytes) -> None:
        raise ClipboardError("没有可用的剪贴板工具")


class CommandClipboard(Clipboard):
    def __init__(self, name: str, *, read_argv, write_argv,
                 targets_argv=None, runner=None):
        self.name = name
        self.read_argv = list(read_argv)
        self.write_argv = list(write_argv)
        self.targets_argv = list(targets_argv) if targets_argv else None
        self._runner = runner or _clip_run

    def _run(self, argv, *, timeout, input_data=None):
        try:
            return self._runner(argv, timeout=timeout, input_data=input_data)
        except subprocess.TimeoutExpired:
            raise ClipboardError(f"{self.name} 超时（{timeout}s）")
        except FileNotFoundError:
            raise ClipboardError(f"{self.name} 不在 PATH 中")
        except OSError as e:
            raise ClipboardError(f"{self.name} 执行失败: {e}")

    def targets(self) -> list[str] | None:
        if not self.targets_argv:
            return None
        cp = self._run(self.targets_argv, timeout=CLIP_READ_TIMEOUT)
        if getattr(cp, "returncode", 1) != 0:
            return None
        values = [line.strip() for line in _out_text(cp).splitlines()
                  if line.strip()]
        return values or None

    def read(self) -> bytes:
        cp = self._run(self.read_argv, timeout=CLIP_READ_TIMEOUT)
        if getattr(cp, "returncode", 1) != 0:
            raise ClipboardError(
                f"{self.name} 读取失败（退出码 {getattr(cp, 'returncode', '?')}）"
                f"：{_clip_stderr(cp)}")
        return _out_bytes(cp)

    def write(self, data: bytes) -> None:
        cp = self._run(self.write_argv, timeout=CLIP_WRITE_TIMEOUT,
                       input_data=data)
        if getattr(cp, "returncode", 1) != 0:
            raise ClipboardError(
                f"{self.name} 写入失败（退出码 {getattr(cp, 'returncode', '?')}）"
                f"：{_clip_stderr(cp)}")


def make_clipboard(session: str | None = None, *, which=None,
                   runner=None) -> Clipboard | None:
    """按会话选择剪贴板工具；一个都没有返回 None（调用方降级并提示）。"""
    session = detect_session() if session is None else session
    which = shutil.which if which is None else which

    def xclip():
        return CommandClipboard(
            "xclip",
            read_argv=["xclip", "-selection", "clipboard", "-o"],
            write_argv=["xclip", "-selection", "clipboard", "-i"],
            targets_argv=["xclip", "-selection", "clipboard", "-o",
                          "-t", "TARGETS"],
            runner=runner)

    def xsel():
        return CommandClipboard(
            "xsel",
            read_argv=["xsel", "--clipboard", "--output"],
            write_argv=["xsel", "--clipboard", "--input"],
            runner=runner)

    if session == "wayland" and which("wl-copy") and which("wl-paste"):
        return CommandClipboard(
            "wl-copy",
            read_argv=["wl-paste", "--no-newline"],
            write_argv=["wl-copy"],
            targets_argv=["wl-paste", "--list-types"],
            runner=runner)
    if which("xclip"):
        return xclip()
    if which("xsel"):
        return xsel()
    return None


def is_text_targets(targets: list[str] | None) -> bool:
    if targets is None:
        return True                    # xsel 不支持列 targets：只能读了再看
    for target in targets:
        low = target.strip().lower()
        if low in TEXT_TARGETS or low.startswith("text/plain"):
            return True
    return False


def default_paste_wait(nbytes: int) -> float:
    """目标应用异步取走剪贴板内容所需的时间（按体积估算）。"""
    if nbytes <= 200_000:
        return 0.5
    return min(3.0, 0.5 + nbytes / 1_000_000)


def paste_key_for(info: dict | None, override: str | None = None) -> str:
    if override:
        return override
    if is_terminal(info):
        cls = (info.get("class") or info.get("name") or "").lower()
        return "shift+insert" if cls in PASTE_SHIFT_INSERT else "ctrl+shift+v"
    return "ctrl+v"


def paste_text(typer: "Typer", text: str, *, clip: Clipboard,
               paste_key: str = "ctrl+v", restore: bool = True,
               wait: float | None = None, sleep=time.sleep) -> None:
    """把文本放进剪贴板并按粘贴键；备份失败时 fail-closed。

    备份失败/非文本/超 4 MiB → ``ClipboardUnsafe``（退出码 2），
    除非调用方显式 ``restore=False``（--no-clipboard-backup）。
    """
    data = text.encode("utf-8")
    backup = None
    if restore:
        if not is_text_targets(clip.targets()):
            raise ClipboardUnsafe(
                "剪贴板当前内容不是文本（可能是图片），无法安全备份。\n"
                "为避免覆盖你的剪贴板，已停止。确认可以覆盖时加 "
                "--no-clipboard-backup。")
        try:
            backup = clip.read()
        except ClipboardError as e:
            raise ClipboardUnsafe(
                f"无法备份当前剪贴板（{e}）。\n"
                f"为避免覆盖你的剪贴板，已停止。确认可以覆盖时加 "
                f"--no-clipboard-backup。")
        if len(backup) > MAX_CLIP_BACKUP:
            raise ClipboardUnsafe(
                f"剪贴板内容超过 {MAX_CLIP_BACKUP} 字节，无法安全备份。\n"
                f"确认可以覆盖时加 --no-clipboard-backup。")
    if wait is None:
        wait = default_paste_wait(len(data))
    try:
        clip.write(data)
        sleep(CLIP_SETTLE)
        typer.press(paste_key)
        sleep(wait)
    finally:
        if backup is not None:
            try:
                clip.write(backup)
            except Exception as e:
                LOG.warn(f"剪贴板恢复失败，原内容已丢失：{e}")


def send_text(typer: "Typer", text: str, *, args, target_info: dict | None,
              clip_factory=None) -> bool:
    """paste-mode 的唯一实现：返回 True 表示已走剪贴板，False 表示降级逐字符。"""
    clip_factory = make_clipboard if clip_factory is None else clip_factory
    clip = clip_factory()
    if clip is None:
        LOG.warn("未找到 xclip/xsel/wl-copy，--paste-mode 降级为逐字符输入。"
                 "安装：sudo apt install xclip（或 wl-clipboard）")
        est = len(text) * max(args.delay, 0.0)
        if est >= 5:
            LOG.warn(f"预计耗时约 {est:.0f} 秒（Ctrl-C 可中断）")
        typer.type_text(text)
        return False
    info = target_info
    if info is None:
        try:
            info = typer.focus_info()
        except Exception:
            info = None
    key = paste_key_for(info, args.paste_key)
    try:
        paste_text(typer, text, clip=clip, paste_key=key,
                   restore=not args.no_clipboard_backup,
                   wait=args.paste_wait)
    except ClipboardError as e:
        LOG.warn(f"剪贴板操作失败（{e}），降级为逐字符输入。")
        typer.type_text(text)
        return False
    return True


# ------------------------------------------------------------- 会话层外观
class Typer:
    """对外统一入口：选择后端并把所有调用转发过去。"""

    def __init__(self, backend: str = "auto", delay: float = 0.008,
                 remap_wait: float = 0.02, *, which=None, env=None,
                 runner=None):
        self.backend = make_backend(backend, delay=delay,
                                    remap_wait=remap_wait, which=which,
                                    env=env, runner=runner)
        self._delay = delay
        self._closed = False
        atexit.register(self.close)

    @property
    def delay(self) -> float:
        return self._delay

    @delay.setter
    def delay(self, value: float) -> None:
        self._delay = value
        self.backend.delay = value

    def type_text(self, text: str) -> None:
        return self.backend.type_text(text)

    def type_char(self, ch: str) -> None:
        return self.backend.type_char(ch)

    def press(self, combo: str) -> None:
        return self.backend.press(combo)

    def focus_info(self) -> dict | None:
        return self.backend.focus_info()

    def capabilities(self) -> dict:
        return self.backend.capabilities()

    def list_windows(self) -> list[WindowInfo]:
        return self.backend.list_windows()

    def activate_window(self, info: WindowInfo, *,
                        timeout: float = WINDOW_ACTIVATE_TIMEOUT) -> bool:
        return self.backend.activate_window(info, timeout=timeout)

    def focused_window_id(self) -> int | None:
        return self.backend.focused_window_id()

    def is_window_focused(self, xid: int) -> bool:
        return self.backend.is_window_focused(xid)

    def resolvable(self, text: str) -> list[str]:
        return self.backend.resolvable(text)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.backend.close()
        except Exception:
            pass


def target_label(typer: Typer) -> str:
    """倒计时/提示里"会敲到哪里"的一句话。"""
    caps = typer.capabilities()
    if not caps.get("can_focus_info"):
        return f"Wayland 会话（{caps.get('name')}：看不到焦点窗口，请自己确认）"
    try:
        return describe_window(typer.focus_info())
    except Exception:
        return "未知窗口"


def multiline_risk(typer: Typer, text: str,
                   info: dict | None = None) -> str | None:
    """多行文本打进终端（或目标不可知）时的风险说明；无风险返回 None。

    ``info`` 是 `--window` 定向后的目标（dict 形式）；给定时按它判断，
    不再看"当前焦点"——两者在激活后应一致，但目标信息更确定。
    """
    body = text.rstrip("\n")
    if "\n" not in body:
        return None
    if info is not None:
        if is_terminal(info):
            n = len([line for line in body.splitlines() if line.strip()])
            return (f"目标终端 {describe_window(info)}：多行文本会被逐行执行"
                    f"（共 {n} 行）。")
        return None
    caps = typer.capabilities()
    if not caps.get("can_focus_info"):
        return "无法读取焦点窗口（Wayland）：多行文本若打进终端会被逐行执行。"
    info = typer.focus_info()
    if is_terminal(info):
        n = len([line for line in body.splitlines() if line.strip()])
        return (f"目标是终端 {describe_window(info)}：多行文本会被逐行执行"
                f"（共 {n} 行）。")
    return None


def parse_combo(name: str) -> tuple[list[str], str]:
    """解析 "ctrl+alt+t" -> (["ctrl","alt"], "t")。"""
    tokens = name.split("+")
    mods = []
    for tok in tokens[:-1]:
        tok = tok.lower().strip()
        if tok not in MODIFIERS:
            raise Abort(f"未知修饰键: {tok}")
        mods.append(tok)
    return mods, tokens[-1].strip()


# ------------------------------------------------------------------ 历史记录
def _entry_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def default_history_path(env=None) -> str | None:
    """$XDG_STATE_HOME/autopaste/history；环境不完整时返回 None（仅内存）。"""
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or ""
    if base:
        if not os.path.isabs(base):
            return None             # 相对路径会落进 cwd：fail-closed
    else:
        home = env.get("HOME")
        if not home or not os.path.isabs(home):
            return None
        base = os.path.join(home, ".local", "state")
    return os.path.join(base, "autopaste", "history")


class History:
    """REPL 历史；``path=None`` 表示仅内存。

    任何权限/属主/符号链接/IO 问题都只降级为"仅内存历史"并说明原因，
    绝不"尽力而为"地写一个不安全的明文文件。
    """

    def __init__(self, path: str | None = None, *, euid=None):
        self.path = path
        self.entries: list[str] = []
        self.skipped = 0
        self.disabled_reason: str | None = None
        self._euid = os.geteuid() if euid is None else euid
        if path:
            try:
                self._load()
            except OSError as e:
                self._disable(f"历史不可用，已退化为仅内存：{e}")

    # ---- 写入
    def add(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if len(text.encode("utf-8")) > HIST_MAX_ENTRY_BYTES:
            return False
        if _entry_lines(text) > HIST_MAX_ENTRY_LINES:
            return False
        if self.entries and self.entries[-1] == text:
            return False
        self.entries.append(text)
        del self.entries[:-HIST_MAX_ENTRIES]
        if self.path is not None and self.disabled_reason is None:
            try:
                self._append(text)
            except OSError as e:
                self._disable(f"历史写入失败，已退化为仅内存：{e}")
        return True

    # ---- 安全闸门
    def _disable(self, why: str) -> None:
        self.disabled_reason = why
        LOG.warn(why)

    def _dir_problem(self, d: str) -> str | None:
        try:
            st = os.lstat(d)
        except OSError as e:
            return f"历史目录不可用（{e}）"
        if not stat.S_ISDIR(st.st_mode):
            return "历史目录不是目录（可能是符号链接）"
        if st.st_uid != self._euid:
            return "历史目录不属于当前用户"
        if stat.S_IMODE(st.st_mode) & 0o022:
            try:
                os.chmod(d, 0o700)
            except OSError:
                return "历史目录对组/其他用户可写且无法收紧"
        return None

    @staticmethod
    def _read_fd(fd: int, limit: int) -> bytes:
        chunks = []
        left = limit
        while left > 0:
            chunk = os.read(fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    # ---- 读写实现
    def _load(self) -> None:
        d = os.path.dirname(self.path)
        if not os.path.isdir(d):
            return                       # 首次 add() 才创建目录
        problem = self._dir_problem(d)
        if problem:
            raise OSError(problem)
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return
        except OSError as e:
            raise OSError(f"无法读取历史文件（{e}）")
        compact = False
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError("历史文件不是普通文件")
            if st.st_uid != self._euid:
                raise OSError("历史文件不属于当前用户")
            if stat.S_IMODE(st.st_mode) & 0o077:
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    raise OSError("历史文件权限过宽且无法收紧")
            if st.st_size > HIST_MAX_FILE_BYTES:
                compact = True
                os.lseek(fd, -HIST_MAX_FILE_BYTES, os.SEEK_END)
                raw = self._read_fd(fd, HIST_MAX_FILE_BYTES)
                # 丢弃被截断的首行
                nl = raw.find(b"\n")
                raw = raw[nl + 1:] if nl >= 0 else b""
            else:
                raw = self._read_fd(fd, HIST_MAX_FILE_BYTES)
        finally:
            os.close(fd)
        for line in raw.splitlines():
            try:
                obj = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.skipped += 1
                continue
            text = obj.get("t") if isinstance(obj, dict) else None
            if (not isinstance(obj, dict) or obj.get("v") != 1
                    or not isinstance(text, str)
                    or len(text.encode("utf-8")) > HIST_MAX_ENTRY_BYTES
                    or _entry_lines(text) > HIST_MAX_ENTRY_LINES):
                self.skipped += 1
                continue
            self.entries.append(text)
        del self.entries[:-HIST_MAX_ENTRIES]
        if compact:
            try:
                self._compact()
            except OSError as e:
                self._disable(f"历史压缩失败，已退化为仅内存：{e}")

    def _append(self, text: str) -> None:
        d = os.path.dirname(self.path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        problem = self._dir_problem(d)
        if problem:
            raise OSError(problem)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                     | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError("历史文件不是普通文件")
            if st.st_uid != self._euid:
                raise OSError("历史文件不属于当前用户")
            if stat.S_IMODE(st.st_mode) & 0o077:
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    raise OSError("历史文件权限过宽且无法收紧")
            data = (json.dumps({"v": 1, "t": text}, ensure_ascii=False)
                    + "\n").encode("utf-8")
            while data:
                n = os.write(fd, data)
                if n <= 0:
                    raise OSError("历史写入未完成")
                data = data[n:]
        finally:
            os.close(fd)

    def _compact(self) -> None:
        d = os.path.dirname(self.path)
        tmp = os.path.join(
            d, f".{os.path.basename(self.path)}.autopaste-{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_NOFOLLOW, 0o600)
        try:
            for text in self.entries[-HIST_MAX_ENTRIES:]:
                data = (json.dumps({"v": 1, "t": text}, ensure_ascii=False)
                        + "\n").encode("utf-8")
                while data:
                    n = os.write(fd, data)
                    if n <= 0:
                        raise OSError("历史压缩写入未完成")
                    data = data[n:]
        except OSError:
            os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.close(fd)
        try:
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ------------------------------------------------------------------ 行编辑器
class LineEditor:
    """cbreak 模式下的最小行编辑器：方向键 / Home / End / 退格 / 括号粘贴 / 历史"""

    def __init__(self, fd: int, history=None):
        self.fd = fd
        self.history = history if history is not None else []
        self.buf: list[str] = []
        self.pos = 0
        self.paste: str | None = None
        self.paste_origin = "paste"      # "paste" | "history"
        self.hist_i = len(self.history)
        self.hist_draft: str | None = None
        self.hist_prefix: str | None = None

    def _w(self, s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _read_byte(self) -> int | None:
        b = os.read(self.fd, 1)
        return b[0] if b else None

    def _read_esc(self) -> str:
        c = self._read_byte()
        if c is None:
            return ""
        seq = chr(c)
        if seq in "[O":
            while True:
                c = self._read_byte()
                if c is None:
                    return seq
                seq += chr(c)
                if 0x40 <= c <= 0x7E:
                    return seq
        return seq

    def _read_paste(self) -> str:
        """读取括号粘贴。契约（超限粘贴的两种可能行为，这里选"截断 + 排空"）：

        * 终止序列 ``\\x1b[201~`` 一定被消费掉，绝不留给后续按键循环当普通
          输入重新读入（否则命令行会凭空多出 ``[201~`` 之类的字符）；
        * 载荷最多保留 ``MAX_PAYLOAD`` 字节（恰好等于上限不算超限）；
        * 超过上限时截断到上限，并**继续读到终止序列为止**丢弃剩余部分
          （最多再读 ``MAX_PAYLOAD`` 字节，避免无终止序列的管道卡死 REPL），
          同时在 stderr 明确提示"已截断并丢弃"，不静默吞输入。
        """
        data = bytearray()
        overflow = False
        while True:
            c = self._read_byte()
            if c is None:
                break
            data.append(c)
            if data.endswith(b"\x1b[201~"):
                del data[-6:]
                break
            # 保留窗口里出现完整终止序列的最晚起点是 len-6；再多读 5 字节
            # 仍没等到终止序列，就说明剩余内容只能丢弃。
            if len(data) > MAX_PAYLOAD + 4:
                overflow = True
                break
        if overflow:
            LOG.warn(f"粘贴内容超过上限 {MAX_PAYLOAD} 字节，"
                     f"已截断并丢弃剩余部分。")
            self._discard_paste_tail(data[-5:])
            del data[MAX_PAYLOAD:]
        return (data.decode("utf-8", "replace")
                .replace("\r\n", "\n").replace("\r", "\n"))

    def _discard_paste_tail(self, window: bytes) -> None:
        """丢弃超限粘贴的剩余字节，直到终止序列或 EOF（有上限）。"""
        buf = bytearray(window)
        for _ in range(MAX_PAYLOAD):
            c = self._read_byte()
            if c is None:
                return
            buf.append(c)
            if bytes(buf).endswith(b"\x1b[201~"):
                return
            if len(buf) > 6:
                del buf[0]

    @staticmethod
    def _width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else
                   (0 if unicodedata.combining(c) else 1) for c in s)

    def _render(self, prompt: str) -> None:
        if self.paste is not None:
            nlines = self.paste.count("\n") + 1
            if self.paste.endswith("\n"):
                nlines -= 1
            if self.paste_origin == "history":
                label, hint = "历史", "回车重发，退格清除"
            else:
                label, hint = "已粘贴", "退格清除"
            display = (f"[{label} {len(self.paste)} 字符 / {nlines} 行"
                       f"（{hint}）]")
            tail = 0
        else:
            display = "".join(self.buf)
            tail = self._width(display[self.pos:])
        self._w("\r\x1b[K" + prompt + display)
        if tail:
            self._w(f"\x1b[{tail}D")

    # ---- 历史导航
    def _load_text(self, text: str, origin: str) -> None:
        if "\n" in text:
            # 多行条目复用"折叠粘贴"显示：_render 的宽度模型不支持内嵌换行
            self.paste, self.paste_origin = text, origin
            self.buf, self.pos = [], 0
        else:
            self.buf, self.pos = list(text), len(text)
            self.paste, self.paste_origin = None, "paste"

    def _history_visible(self) -> bool:
        return self.paste is None or self.paste_origin == "history"

    def _hist_up(self) -> None:
        if not self._history_visible():
            return
        if self.hist_prefix is None:
            self.hist_prefix = "".join(self.buf)
            self.hist_draft = self.hist_prefix
        j = self.hist_i - 1
        while j >= 0:
            if self.history[j].startswith(self.hist_prefix):
                self.hist_i = j
                self._load_text(self.history[j], "history")
                return
            j -= 1

    def _hist_down(self) -> None:
        if not self._history_visible() or self.hist_prefix is None:
            return
        j = self.hist_i + 1
        while j < len(self.history):
            if self.history[j].startswith(self.hist_prefix):
                self.hist_i = j
                self._load_text(self.history[j], "history")
                return
            j += 1
        draft = self.hist_draft or ""
        self.hist_i = len(self.history)
        self.hist_prefix = None
        self.hist_draft = None
        self._load_text(draft, "paste")

    def read(self, prompt: str) -> tuple[str, str | None]:
        self.buf, self.pos, self.paste = [], 0, None
        self.paste_origin = "paste"
        self.hist_i = len(self.history)
        self.hist_draft = None
        self.hist_prefix = None
        self._render(prompt)
        while True:
            c = self._read_byte()
            if c is None:
                self._w("\r\n")
                return "eof", None
            if c == 0x1B:
                seq = self._read_esc()
                if seq == "[200~":
                    self.paste = self._read_paste()
                    self.paste_origin = "paste"
                    self.buf, self.pos = [], 0
                elif seq in ("[A", "OA"):
                    self._hist_up()
                elif seq in ("[B", "OB"):
                    self._hist_down()
                elif seq in ("[D", "OD"):
                    self.pos = max(0, self.pos - 1)
                elif seq in ("[C", "OC"):
                    self.pos = min(len(self.buf), self.pos + 1)
                elif seq in ("[H", "OH", "[1~"):
                    self.pos = 0
                elif seq in ("[F", "OF", "[4~"):
                    self.pos = len(self.buf)
                elif seq == "[3~":
                    if self.pos < len(self.buf):
                        del self.buf[self.pos]
                    elif self.paste is not None:
                        self.paste = None
                self._render(prompt)
                continue
            if c in (10, 13):
                self._w("\r\n")
                return "text", (self.paste if self.paste is not None
                                else "".join(self.buf))
            if c in (0x7F, 0x08):
                if self.paste is not None and not self.buf:
                    self.paste = None
                elif self.pos > 0:
                    del self.buf[self.pos - 1]
                    self.pos -= 1
                self._render(prompt)
                continue
            if c == 0x03:
                self._w("^C\r\n")
                return "interrupt", None
            if c == 0x04:
                if not self.buf and self.paste is None:
                    self._w("\r\n")
                    return "eof", None
                continue
            if c == 0x01:
                self.pos = 0
                self._render(prompt)
                continue
            if c == 0x05:
                self.pos = len(self.buf)
                self._render(prompt)
                continue
            if c == 0x15:
                self.buf, self.pos = [], 0
                self.paste = None
                self._render(prompt)
                continue
            if c == 0x17:
                if self.paste is not None and not self.buf:
                    self.paste = None
                    self._render(prompt)
                    continue
                while self.pos > 0 and self.buf[self.pos - 1] == " ":
                    del self.buf[self.pos - 1]
                    self.pos -= 1
                while self.pos > 0 and self.buf[self.pos - 1] != " ":
                    del self.buf[self.pos - 1]
                    self.pos -= 1
                self._render(prompt)
                continue
            if c == 0x10:                    # Ctrl-P = ↑（必须早于 c<0x20）
                self._hist_up()
                self._render(prompt)
                continue
            if c == 0x0E:                    # Ctrl-N = ↓
                self._hist_down()
                self._render(prompt)
                continue
            if c < 0x20:
                continue
            if self.paste is not None:
                # 回忆/粘贴态下打字会被忽略（否则看起来输入了、提交的却是旧内容）
                continue
            if c < 0x80:
                ch = chr(c)
            else:
                need = 1 if c < 0xE0 else (2 if c < 0xF0 else 3)
                raw = bytes([c])
                for _ in range(need):
                    nb = self._read_byte()
                    if nb is None:
                        break
                    raw += bytes([nb])
                ch = raw.decode("utf-8", "replace")
            self.buf.insert(self.pos, ch)
            self.pos += 1
            self._render(prompt)


# ------------------------------------------------------------------ 交互界面
class Settings:
    def __init__(self, sleep: float = 2.0, delay: float = 0.008,
                 auto_enter: bool = False, window: str | None = None):
        self.sleep = sleep
        self.delay = delay
        self.auto_enter = auto_enter
        self.window = window


def countdown(seconds: float, typer: Typer, cancelable: bool = True,
              label: str | None = None) -> bool:
    """倒计时，顺手显示"会敲到哪个窗口"。返回 False 表示被取消。

    ``label`` 用于 `--window` 定向：显示的是**目标窗口**而不是当前焦点。
    """
    if label is None:
        try:
            target = target_label(typer)
        except Exception:
            target = "未知窗口"
    else:
        target = label
    if seconds <= 0:
        return True
    stream = LOG._stream()
    quiet = LOG.level == Log.QUIET
    if quiet:
        # --quiet 只压成一行，但目标窗口与"可取消"必须保留（安全可视化）
        tail = f"｜{int(seconds)}s 后可取消（任意键取消）" if cancelable \
            else f"｜{int(seconds)}s 后开始"
        stream.write(f"目标 {target}{tail}\n")
        stream.flush()
    end = time.time() + seconds
    try:
        while True:
            left = end - time.time()
            if left <= 0:
                break
            if not quiet:
                tail = "（按任意键取消）" if cancelable else ""
                stream.write(f"\r  目标 {target}  {left:4.1f}s {tail}   ")
                stream.flush()
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if r and cancelable:
                try:
                    data = os.read(sys.stdin.fileno(), 1024)
                except OSError:
                    data = b""
                if data:
                    stream.write("\r" + " " * 70 + "\r  已取消\r\n"
                                 if not quiet else "  已取消\n")
                    return False
                # EOF（如 `cat note.txt | autopaste -s 3 -`）：stdin 再也
                # 变不出按键，停止监听，但倒计时必须照常走完。
                cancelable = False
    except KeyboardInterrupt:
        stream.write("\r" + " " * 70 + "\r  已取消\r\n" if not quiet
                     else "  已取消\n")
        return False
    if not quiet:
        stream.write("\r" + " " * 70 + "\r")
        stream.flush()
    return True


def warn_multiline_target(typer: Typer, text: str) -> None:
    msg = multiline_risk(typer, text)
    if msg:
        LOG.warn(msg)


def _cmd_preview(typer: Typer, cfg: Settings, last: str | None) -> None:
    """`:p`：只预览上一段内容，绝不发送任何按键。"""
    if last is None:
        sys.stdout.write("  还没有可预览的内容（先输入一段或 :f <文件>）\n")
        return
    nlines = _entry_lines(last)
    est = len(last) * max(cfg.delay, 0.0)
    sys.stdout.write(f"  预览（{len(last)} 字符 / {nlines} 行，"
                     f"预计 {est:.1f}s，未发送）:\n")
    sys.stdout.write(f"  {_esc_preview(last)}\n")
    sys.stdout.write(f"  目标: {target_label(typer)}\n")
    if est > 60:
        sys.stdout.write("  提示: 预计耗时较长，可考虑 --paste-mode 或 :k ctrl+v\n")
    risk = multiline_risk(typer, last)
    if risk:
        sys.stdout.write(f"  ⚠ {risk}\n")


def _save_target(path: str) -> None:
    """`:w` 写入前的 fail-closed 检查：只接受普通文件，拒绝符号链接。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return                       # 新文件：由 O_EXCL 临时文件保证不写穿
    except OSError as e:
        raise Abort(f"无法检查 {path}: {e}")
    if stat.S_ISLNK(st.st_mode):
        raise Abort(f"拒绝写入符号链接: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise Abort(f"拒绝写入非普通文件: {path}")


def _cmd_save(arg: str, last: str | None) -> str:
    """`:w <文件>`：原子写入上一段内容（0600），返回回执文本。"""
    if not arg:
        raise Abort("用法: :w <文件>")
    if last is None:
        raise Abort("还没有可保存的内容")
    path = os.path.expanduser(arg)
    if not os.path.basename(path) or path.endswith(os.sep):
        raise Abort(f"无效的文件路径: {arg}")
    _save_target(path)
    directory = os.path.dirname(path) or "."
    tmp = os.path.join(
        directory,
        f".{os.path.basename(path)}.autopaste-{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise Abort(f"无法写入 {path}: {e}")
    try:
        data = last.encode("utf-8")
        while data:
            n = os.write(fd, data)
            if n <= 0:
                raise OSError("写入未完成")
            data = data[n:]
        try:
            os.fsync(fd)
        except OSError:
            pass
    except OSError as e:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise Abort(f"无法写入 {path}: {e}")
    os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise Abort(f"无法写入 {path}: {e}")
    return (f"  已保存 {path}（{len(last)} 字符 / {_entry_lines(last)} 行，"
            f"0600）")


BANNER = """\
autopaste REPL ── 回车后倒计时再输入；粘贴多行文本没有任何引号/转义问题
  :s 3 倒计时 | :d 0.02 每字间隔 | :nl 自动回车 | :k 组合键 | :f 文件
  :r 重复上次 | :t 状态/目标窗口 | :p 预览 | :w 保存 | :win 定向窗口
  ↑/↓ 历史（行内前缀搜索，↓ 回到草稿） | :h 帮助 | :q 退出
"""

HELP = """\
命令:
  :s <秒>         每次回车后的倒计时 (0 表示不等)
  :d <秒>         每个字符之间的间隔
  :nl             切换"输完自动按回车"
  :k <组合键>     发送按键, 如 :k ctrl+v  :k enter  :k ctrl+alt+t
  :f <文件>       输入文件内容
  :r              重复上一次提交的内容
  :t              查看当前设置与目标窗口
  :win <pattern>  设置定向窗口（class:/title:/pid:/re:），:win - 清除
  :p              预览上一次提交的内容（转义显示，不发送任何按键）
  :w <文件>       把上一次提交的内容保存到文件（0600，拒绝符号链接）
  ↑ / ↓           上一条/下一条历史（先输入前缀可过滤；↓ 回到草稿）
  :h              显示本帮助
  :q              退出

其它: 直接粘贴(含多行) -> 回车; 途中 Ctrl-C 取消当前行/倒计时, Ctrl-D 退出。
"""


def repl(typer: Typer, cfg: Settings,
         history: "History | None" = None) -> int:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    ed = LineEditor(fd, history=None if history is None else history.entries)
    last: str | None = None
    #: 本轮提交实际激活的目标窗口（None = 不定向，敲当前焦点）
    focus_target: WindowInfo | None = None
    LOG.info(BANNER)

    def note_sent(text: str, started: float) -> None:
        dt = time.time() - started
        rate = (len(text) / dt) if dt > 0 else float("inf")
        LOG.detail(f"已发送 {len(text)} 字符 / {_entry_lines(text)} 行，"
                   f"用时 {dt:.2f}s（{rate:.1f} 字/秒）")

    def activate() -> bool:
        """REPL 里 --window 是粘性目标：每次提交前重新解析并激活。"""
        nonlocal focus_target
        focus_target = None
        if not cfg.window:
            return True
        try:
            target = resolve_window(typer, cfg.window, tty=True)
            if not typer.activate_window(target):
                raise WindowUnavailable(
                    f"无法激活 {describe_window_info(target)}")
            focus_target = target
            return True
        except Abort as e:
            LOG.warn(f"已拦截: {e}")
            return False

    def focus_ok() -> bool:
        """倒计时后的焦点复核：焦点离开就跳过本次注入（与 CLI 一致）。

        绝不"盲打"到当前窗口；REPL 不退出，用户可以直接重试。
        """
        if focus_target is None:
            return True
        try:
            ok = typer.is_window_focused(focus_target.xid)
        except Exception:
            ok = False
        if not ok:
            LOG.warn(f"已拦截: 焦点已离开定向窗口 "
                     f"{describe_window_info(focus_target)}，已取消本次输入"
                     f"（避免敲到别处）。")
        return ok

    def cleanup():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?2004h")     # 打开终端的括号粘贴
        sys.stdout.flush()
        while True:
            try:
                target = target_label(typer)
            except Exception:
                target = "?"
            short = target if len(target) <= 28 else target[:27] + "…"
            prompt = f"[{short}] autopaste> "
            kind, payload = ed.read(prompt)
            if kind == "eof":
                break
            if kind == "interrupt":
                continue
            text = payload
            if not text.strip():
                continue

            if text.startswith(":"):
                cmd = text[1:].strip()
                name, _, arg = cmd.partition(" ")
                name, arg = name.lower(), arg.strip()
                if history is not None:
                    history.add(text)
                try:
                    if name in ("q", "quit", "exit"):
                        break
                    elif name in ("h", "help", "?"):
                        sys.stdout.write(HELP)
                    elif name == "t":
                        sys.stdout.write(
                            f"  倒计时={cfg.sleep}s  字符间隔={cfg.delay}s  "
                            f"自动回车={'开' if cfg.auto_enter else '关'}\n"
                            f"  定向窗口={cfg.window or '（当前焦点）'}\n"
                            f"  当前目标={target_label(typer)}\n")
                    elif name == "win":
                        if arg in ("-", ""):
                            cfg.window = None
                            LOG.info("  已清除定向窗口")
                        else:
                            cfg.window = arg
                            LOG.info(f"  定向窗口={arg}")
                    elif name == "p":
                        _cmd_preview(typer, cfg, last)
                    elif name == "w":
                        LOG.info(_cmd_save(arg, last))
                    elif name == "s":
                        cfg.sleep = _num(arg, 0.0, MAX_COUNTDOWN, "倒计时")
                    elif name == "d":
                        cfg.delay = _num(arg, 0.0, MAX_DELAY, "字符间隔")
                        typer.delay = cfg.delay
                    elif name == "nl":
                        cfg.auto_enter = not cfg.auto_enter
                        LOG.info("  自动回车: %s"
                                 % ("开" if cfg.auto_enter else "关"))
                    elif name == "k":
                        validate_payload(arg)
                        if (activate() and countdown(cfg.sleep, typer)
                                and focus_ok()):
                            typer.press(arg)
                    elif name == "f":
                        check_regular_file(arg)
                        with open(arg, "r", encoding="utf-8",
                                  errors="replace") as f:
                            body = f.read()
                        validate_payload(body)
                        warn_multiline_target(typer, body)
                        if (activate() and countdown(cfg.sleep, typer)
                                and focus_ok()):
                            started = time.time()
                            typer.type_text(body)
                            note_sent(body, started)
                        last = body
                    elif name == "r":
                        if last is None:
                            LOG.info("  还没有可重复的内容")
                        elif (activate() and countdown(cfg.sleep, typer)
                              and focus_ok()):
                            started = time.time()
                            typer.type_text(last)
                            note_sent(last, started)
                    elif name == "":
                        pass
                    else:
                        LOG.info(f"  未知命令 :{name}，输入 :h 查看帮助")
                except (Abort, ValueError, OSError) as e:
                    LOG.warn(f"已拦截: {e}")
                sys.stdout.flush()
                continue

            try:
                validate_payload(text)
            except Abort as e:
                LOG.warn(f"已拦截: {e}")
                continue
            if history is not None:
                history.add(text)
            last = text
            warn_multiline_target(typer, text)
            if (activate() and countdown(cfg.sleep, typer)
                    and focus_ok()):
                started = time.time()
                typer.type_text(text)
                if cfg.auto_enter:
                    typer.press("enter")
                note_sent(text, started)
            sys.stdout.flush()
    finally:
        try:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()
        finally:
            cleanup()
    LOG.info("bye")
    return 0


def _num(s: str, lo: float, hi: float, what: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise Abort(f"{what}需要是数字: {s!r}")
    if not lo <= v <= hi:
        raise Abort(f"{what}超出范围 [{lo}, {hi}]: {v}")
    return v


# ------------------------------------------------------------------ 命令行
def resolve_window(typer: Typer, pattern: str, *, tty: bool) -> WindowInfo:
    """把 --window pattern 解析成唯一目标；不支持定向的后端直接报错。"""
    caps = typer.capabilities()
    if not caps.get("window_control"):
        raise WindowUnavailable(
            f"当前后端 {caps.get('name')} 不支持窗口定向（Wayland 看不到 X11 窗口）。\n"
            f"请改用 --backend x11（XWayland 窗口）或去掉 --window，"
            f"用 -s 3 手动切换。")
    wins = typer.list_windows()
    matches = match_windows(wins, pattern)
    return pick_window(matches, active_xid=typer.focused_window_id(),
                       tty=tty)


def _info_dict(info: WindowInfo | None) -> dict | None:
    if info is None:
        return None
    return {"class": info.cls or None, "name": info.instance or None,
            "title": info.title or None}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="autopaste",
        description="把文本敲进当前聚焦的窗口（X11 用 XTEST，Wayland 用 "
                    "wtype/ydotool，零第三方依赖）。"
                    "不带参数运行进入交互 REPL。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  autopaste                          进入 REPL（粘贴文本零转义）
  autopaste -s 3 'hello'             倒数 3 秒后输入
  autopaste -f note.txt -s 2         输入文件
  autopaste --dry-run 'x < y'        只预览，不敲键
  autopaste -k ctrl+shift+v          发送组合键
  autopaste --backend wtype 'hi'     指定后端（Wayland）
  autopaste --list-backends          查看后端可用性
  autopaste --list-windows           列出可定向窗口
  autopaste --window class:firefox 'hi'  定向窗口（X11，校验焦点）
  autopaste --paste-mode -f big.txt  剪贴板直贴大段文本

安全:
  * 默认拒绝 root 运行、拒绝控制字符（ESC 等）、拒绝 FIFO/设备文件；
  * 多行文本打进终端会二次确认（Wayland 下所有多行都确认）；
  * --window 会主动激活目标窗口；--paste-mode 会改写系统剪贴板（默认备份/恢复）；
  * 详见 SECURITY.md。

zsh 提示: 文本里的 ! 会被历史展开，请用单引号 '...' 包住，或直接用 REPL 粘贴。
""")
    ap.add_argument("text", nargs="*", help="要输入的文本；'-' 表示从 stdin 读取")
    ap.add_argument("-d", "--delay", type=float, default=0.008,
                    help="每个字符之间的延时秒数，默认 0.008，上限 5")
    ap.add_argument("-s", "--sleep", type=float, default=None,
                    help="开始前倒计时秒数（REPL 中为每次提交的默认值）")
    ap.add_argument("-f", "--file", help="从文件读取内容（仅普通文件）")
    ap.add_argument("-k", "--key", action="append", default=[],
                    help="按键或组合键，可重复: -k ctrl+v -k enter")
    ap.add_argument("-n", "--no-newline", action="store_true",
                    help="文件/stdin 输入时去掉结尾换行")
    ap.add_argument("--repl", action="store_true", help="强制进入交互 REPL")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要输入的内容与目标窗口，不发送任何按键")
    ap.add_argument("--safe", action="store_true",
                    help="X11 逐字符保守发送（每字符 flush+sleep），不用批量事件")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="跳过确认提示（终端目标 + 多行文本时使用）")
    ap.add_argument("--allow-control", action="store_true",
                    help="允许输入控制字符（ESC 等），确认安全后再用")
    ap.add_argument("--backend", choices=("auto", "x11", "wtype", "ydotool"),
                    default="auto",
                    help="键盘注入后端：auto|x11|wtype|ydotool，默认 auto")
    ap.add_argument("--list-backends", action="store_true",
                    help="列出各后端可用性与探测依据后退出")
    ap.add_argument("--window", metavar="PATTERN",
                    help="定向到匹配的窗口：默认大小写不敏感子串（class/标题），"
                         "可用 class: title: pid: id: re: 前缀")
    ap.add_argument("--list-windows", action="store_true",
                    help="列出可定向的窗口（TSV）后退出；可与 --window 过滤合用")
    ap.add_argument("--json", action="store_true",
                    help="配合 --list-windows 输出 JSON")
    ap.add_argument("--paste-mode", action="store_true",
                    help="把内容写入剪贴板并发送一次粘贴键（大段文本首选）")
    ap.add_argument("--paste-key", metavar="COMBO",
                    help="覆盖粘贴快捷键，如 ctrl+v / ctrl+shift+v / shift+insert")
    ap.add_argument("--paste-wait", type=float, default=None, metavar="秒",
                    help="发送粘贴键后等目标取走内容的秒数（默认按体积，0-10）")
    ap.add_argument("--no-clipboard-backup", "--no-restore-clipboard",
                    dest="no_clipboard_backup", action="store_true",
                    help="不备份/不恢复原剪贴板（默认 fail-closed：备份失败即停止）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("-v", "--verbose", action="store_true",
                   help="输出诊断信息（配置来源/后端/capabilities/耗时）")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="静默模式：只保留倒计时目标行、风险提示与错误")
    ap.add_argument("--no-history", action="store_true",
                    help="不读写 REPL 历史文件（默认保存到 XDG state 目录）")
    ap.add_argument("--allow-root", action="store_true",
                    help="允许以 root 运行（不推荐）")
    ap.add_argument("-V", "--version", action="version",
                    version=f"autopaste {__version__}")
    return ap


def _esc_preview(text: str, limit: int = 400) -> str:
    out = []
    for ch in text[:limit]:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    s = "".join(out)
    if len(text) > limit:
        s += f"…（共 {len(text)} 字符）"
    return s


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(question + " [y/N] ")
    sys.stderr.flush()
    try:
        ans = sys.stdin.readline().strip().lower()
    except KeyboardInterrupt:
        return False
    return ans in ("y", "yes")


def _tool_version(name: str, which=None, runner=None) -> str | None:
    """返回工具版本行；未安装返回 None，探测失败返回空串。"""
    which = shutil.which if which is None else which
    if not which(name):
        return None
    run = runner or _run
    try:
        cp = run([name, "--version"], capture_stdout=True)
        if getattr(cp, "returncode", 1) == 0:
            out = (getattr(cp, "stdout", "") or "").strip().splitlines()
            if out:
                return out[0][:60]
    except Exception:
        pass
    return ""


def format_backends(env=None, which=None, runner=None) -> str:
    """`--list-backends` 的诊断输出：可用性 + 探测依据。"""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    session = detect_session(env)
    rows = []

    libs_note = ""
    try:
        _load_x11()
        libs_ok = True
    except Exception as e:
        libs_ok = False
        libs_note = str(e).splitlines()[0]
    if not env.get("DISPLAY"):
        x11_ok, x11_why = False, f"DISPLAY 未设置；{libs_note or 'libX11/libXtst 可加载'}"
    elif not libs_ok:
        x11_ok, x11_why = False, libs_note
    else:
        x11_ok = True
        x11_why = f"DISPLAY={env.get('DISPLAY')}，libX11/libXtst 可加载"
    rows.append(("x11", x11_ok, x11_why))

    wver = _tool_version("wtype", which=which, runner=runner)
    if wver is None:
        rows.append(("wtype", False, "未在 PATH 找到"))
    else:
        rows.append(("wtype", True,
                     "PATH=" + (which("wtype") or "") +
                     (f"，版本 {wver}" if wver else "")))

    yver = _tool_version("ydotool", which=which, runner=runner)
    if yver is None:
        rows.append(("ydotool", False, "未在 PATH 找到"))
    else:
        sock = _ydotool_socket(env)
        if sock:
            esc = _ydotool_escapes(runner)
            esc_note = ("，支持 -e（关闭转义）" if esc else
                        "，不支持 -e（旧版 < 1.0.4：含反斜杠的文本会被拒绝；"
                        "建议升级或改用 wtype）")
            rows.append(("ydotool", True,
                         "PATH=" + (which("ydotool") or "") +
                         f"，socket={sock}" +
                         (f"，版本 {yver}" if yver else "") + esc_note))
        else:
            rows.append(("ydotool", False,
                         "已安装但找不到 socket（ydotoold 未运行？）"))

    out = ["后端      可用  探测依据"]
    for name, ok, why in rows:
        out.append(f"{name:<9} {'是' if ok else '否'}    {why}")
    out.append(f"当前会话: {session}")
    try:
        out.append("auto 将选择: "
                   + choose_backend("auto", env=env, which=which,
                                    socket_ok=_ydotool_socket_ok))
    except BackendUnavailable:
        out.append("auto 将选择: （无可用后端）")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    set_log(Log.VERBOSE if args.verbose else
            Log.QUIET if args.quiet else Log.NORMAL)

    if args.list_backends:
        sys.stdout.write(format_backends())
        return 0

    # ---- 安全闸门 1: 不以 root 身份注入按键
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not args.allow_root:
        LOG.error("拒绝以 root 运行：按键注入等同于代替你敲键盘，"
                  "以 root 执行会把任何窗口都暴露给它。\n"
                  "如确有需要，请显式加 --allow-root。")
        return 2

    # ---- --list-windows：只读枚举，不激活、不注入
    if args.list_windows:
        try:
            typer = Typer(backend=args.backend)
            try:
                caps = typer.capabilities()
                if not caps.get("window_control"):
                    LOG.error(
                        f"当前后端 {caps.get('name')} 不支持窗口枚举"
                        f"（Wayland 看不到 X11 窗口）。请用 --backend x11。")
                    return 2
                wins = typer.list_windows()
                if args.window:
                    wins = match_windows(wins, args.window)
                active = typer.focused_window_id()
                if args.json:
                    sys.stdout.write(windows_as_json(wins, active) + "\n")
                else:
                    if sys.stdout.isatty():
                        sys.stdout.write(WINDOW_HEADER + "\n")
                    for line in format_window_lines(wins, active):
                        sys.stdout.write(line + "\n")
                return 0
            finally:
                typer.close()
        except BackendUnavailable as e:
            LOG.error(f"{e}")
            return 2
        except Abort as e:
            LOG.error(f"已拦截: {e}")
            return e.code

    try:
        delay = _num(str(args.delay), 0.0, MAX_DELAY, "字符间隔")
        sleep = 0.0 if args.sleep is None else _num(
            str(args.sleep), 0.0, MAX_COUNTDOWN, "倒计时")
    except Abort as e:
        LOG.error(f"{e}")
        return 1

    interactive = args.repl or (not args.text and not args.file
                                and not args.key and sys.stdin.isatty())

    try:
        if interactive:
            if args.paste_mode:
                LOG.error("--paste-mode 目前只支持命令行模式"
                          "（REPL 内请用 :k 或直接粘贴）。")
                return 1
            cfg = Settings(sleep=2.0 if args.sleep is None else sleep,
                           delay=delay, window=args.window)
            history = None
            if not args.no_history:
                history = History(default_history_path())
                if history.path is not None:
                    LOG.detail(
                        f"历史: {history.path}（载入 {len(history.entries)} 条，"
                        f"跳过 {history.skipped} 条损坏）")
            typer = Typer(backend=args.backend, delay=cfg.delay)
            return repl(typer, cfg, history=history)

        # ---- 组装输入
        if args.file:
            check_regular_file(args.file)
            with open(args.file, "r", encoding="utf-8",
                      errors="replace") as f:
                payload = f.read()
        elif args.text == ["-"]:
            data = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
            if len(data) > MAX_PAYLOAD:
                raise Abort(f"stdin 内容超过上限 {MAX_PAYLOAD} 字节")
            payload = data.decode("utf-8", "replace")
        else:
            payload = " ".join(args.text)
        if args.no_newline:
            payload = payload.rstrip("\n")

        user_keys = list(args.key)
        if payload:
            args.key.insert(0, payload)
        if not args.key:
            ap.print_help()
            return 1

        # ---- paste-mode 的参数契约（在任何注入/剪贴板操作之前）
        if args.paste_key and not args.paste_mode:
            raise Abort("--paste-key 只能在 --paste-mode 下使用。")
        if args.paste_mode:
            if user_keys:
                raise Abort("--paste-mode 不能与 -k 组合键混用。")
            if len(args.key) != 1:
                raise Abort("--paste-mode 只支持单个文本 payload。")
            if args.paste_key:
                if not is_combo(args.paste_key):
                    raise Abort(
                        f"--paste-key 需要组合键（如 ctrl+v）：{args.paste_key!r}")
                parse_combo(args.paste_key)        # 校验修饰键名
            if args.paste_wait is not None:
                _num(str(args.paste_wait), 0.0, MAX_PASTE_WAIT, "paste-wait")
        joined = "".join(args.key)

        for item in args.key:
            validate_payload(item, args.allow_control)

        typer = Typer(backend=args.backend, delay=delay)
        caps = typer.capabilities()
        LOG.detail(f"后端: {caps.get('name')} capabilities: {caps}")
        LOG.detail(f"会话: {detect_session()}")
        if args.safe:
            typer.backend.batch_enabled = False
        if args.paste_mode and not caps.get("combos"):
            raise BackendUnavailable(
                f"当前后端 {args.backend} 不支持组合键，无法使用 --paste-mode。")

        # ---- --window：先解析目标（只读），激活留到确认之后
        target = None
        if args.window:
            target = resolve_window(typer, args.window,
                                    tty=sys.stdin.isatty())
        target_info = _info_dict(target)
        target_text = (f"[定向] {describe_window_info(target)}"
                       if target else None)

        # ---- dry-run：只预览
        if args.dry_run:
            caps = typer.capabilities()
            sys.stdout.write(f"后端     : {caps.get('name')}\n")
            sys.stdout.write(f"目标窗口 : "
                             f"{target_text or target_label(typer)}\n")
            if target:
                sys.stdout.write(
                    "激活     : 将发送 _NET_ACTIVE_WINDOW"
                    "（dry-run 未执行）\n")
            total = sum(len(k) for k in args.key)
            sys.stdout.write(f"字符数   : {total}\n")
            for i, item in enumerate(args.key, 1):
                kind = "按键" if is_combo(item) or len(item) == 1 else "文本"
                if kind == "按键" and len(item) > 1 and is_combo(item):
                    sys.stdout.write(f"  [{i}] 按键 {item}\n")
                    continue
                lines = item.count("\n")
                sys.stdout.write(f"  [{i}] {kind} {lines + 1} 行: "
                                 f"{_esc_preview(item)}\n")
            if caps.get("needs_remap"):
                missing = typer.resolvable("".join(args.key))
                if missing:
                    sys.stdout.write(
                        f"无法映射的字符: {''.join(missing)!r}\n")
            else:
                sys.stdout.write(
                    "（该后端自带键盘映射，跳过字符可映射性检查）\n")
            if args.paste_mode:
                info = target_info
                if info is None:
                    try:
                        info = typer.focus_info()
                    except Exception:
                        info = None
                key = paste_key_for(info, args.paste_key)
                sys.stdout.write(
                    f"粘贴模式 : 将把 {len(joined)} 字符写入剪贴板并发送 "
                    f"{key}（dry-run 未执行）\n")
            sys.stdout.write("dry-run：未发送任何按键。\n")
            return 0

        # ---- 安全闸门 2: 终端里的多行文本（Wayland 下所有多行都要确认）
        if target_info is not None:
            risk = multiline_risk(typer, joined, info=target_info)
        else:
            risk = multiline_risk(typer, joined)
        if risk:
            LOG.warn(risk)
            if not args.yes and not _confirm("继续？"):
                LOG.warn("已取消。")
                return 3

        # ---- 激活定向窗口（改焦点是副作用，放在确认之后）
        if target is not None:
            if not typer.activate_window(target):
                raise WindowUnavailable(
                    f"无法激活 {describe_window_info(target)}；"
                    f"请确认窗口仍然存在。")

        if target_text is not None:
            ok = countdown(sleep, typer, label=target_text)
        else:
            ok = countdown(sleep, typer)
        if not ok:
            return 130

        # ---- 敲键前复核焦点：倒计时里用户可能点走了，绝不抢回
        if target is not None and not typer.is_window_focused(target.xid):
            LOG.warn("焦点已离开定向窗口，已取消（避免敲到别处）。")
            return 3

        # ---- paste-mode：唯一一次剪贴板写入发生在倒计时与焦点复核之后
        if args.paste_mode:
            if args.no_clipboard_backup:
                LOG.warn("--no-clipboard-backup：不备份/不恢复剪贴板，"
                         "原内容会被覆盖。")
            started = time.time()
            send_text(typer, joined, args=args, target_info=target_info)
            LOG.detail(f"已发送 {len(joined)} 字符 / {_entry_lines(joined)} 行，"
                       f"用时 {time.time() - started:.2f}s（paste-mode）")
            return 0

        started = time.time()
        for item in args.key:
            if len(item) == 1 or not is_combo(item):
                if item.lower() in SPECIAL or item.lower() in MODIFIERS:
                    typer.press(item)
                else:
                    typer.type_text(item)
            else:
                typer.press(item)
        dt = time.time() - started
        rate = (len(joined) / dt) if dt > 0 else float("inf")
        LOG.detail(f"已发送 {len(joined)} 字符 / {_entry_lines(joined)} 行，"
                   f"用时 {dt:.2f}s（{rate:.1f} 字/秒）")
        return 0
    except (WindowUnavailable, WindowCancelled) as e:
        LOG.error(f"{e}")
        return e.code
    except BackendUnavailable as e:
        LOG.error(f"{e}")
        return 2
    except BackendError as e:
        LOG.error(f"已拦截: {e}")
        return 1
    except Abort as e:
        LOG.error(f"已拦截: {e}")
        return e.code
    except KeyboardInterrupt:
        LOG.error("\n已中断。")
        return 130


def is_combo(item: str) -> bool:
    toks = item.split("+")
    return len(toks) > 1 and all(t.lower() in MODIFIERS for t in toks[:-1])


def _install_signals() -> None:
    def handler(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def console_main(argv: list[str] | None = None) -> int:
    """进程入口：先装信号处理，再跑 main()。

    ``python -m autopaste``、直接执行脚本、pip/pipx 生成的 console script
    必须走同一个入口。SIGTERM/SIGHUP 默认会立刻终止进程，atexit 不执行，
    ``Typer.close()`` 就不会还原临时键位映射/释放修饰键——所以信号必须在
    进入 main() 之前转成 KeyboardInterrupt（SECURITY.md #9）。
    """
    _install_signals()
    try:
        return main(argv)
    except KeyboardInterrupt:
        # main() 内部已把中断转成 130；这里兜住 argparse 等更早的位置。
        LOG.error("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(console_main())
