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
import ctypes
import ctypes.util
import os
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


class Abort(Exception):
    """用户取消 / 安全检查拦截"""


class BackendError(Abort):
    """键盘后端运行期失败（外部工具报错等）"""


class BackendUnavailable(BackendError):
    """环境缺失：没装工具、没有会话、权限不足、socket 不存在等"""


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
        win = ctypes.c_ulong()
        rev = ctypes.c_int()
        x11.XGetInputFocus(dpy, ctypes.byref(win), ctypes.byref(rev))
        if win.value in (0, 1):          # None / PointerRoot
            return None
        title = None
        name = ctypes.c_char_p()
        if x11.XFetchName(dpy, win.value, ctypes.byref(name)):
            title = _decode(name.value)
            x11.XFree(ctypes.cast(name, ctypes.c_void_p))
        cls = res_name = None
        hint = XClassHint()
        if x11.XGetClassHint(dpy, win.value, ctypes.byref(hint)):
            if hint.res_name:
                res_name = ctypes.string_at(hint.res_name).decode("utf-8", "replace")
            if hint.res_class:
                cls = ctypes.string_at(hint.res_class).decode("utf-8", "replace")
            for f in (hint.res_name, hint.res_class):
                if f:
                    x11.XFree(ctypes.cast(f, ctypes.c_void_p))
        return {"title": title, "name": res_name, "class": cls}
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
                "per_char_delay": False, "daemon": False}

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
        for ch in text:
            self.type_char(ch)

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

    def capabilities(self) -> dict:
        return {"name": self.name, "can_focus_info": True,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": True, "combos": True,
                "per_char_delay": True, "daemon": False}

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
                "per_char_delay": True, "daemon": False}


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


class YdotoolBackend(Backend):
    """通过 ydotool（/dev/uinput）注入按键；仅支持 ASCII 字面量。

    ``ydotool type`` 默认开启反斜杠转义（``\\n``/``\\t``/``\\xNN`` 会被
    解码成真实控制字符），因此每次调用都显式传 ``-e 0``，把文本按字面量
    交给工具——否则会绕过 ``validate_payload`` 的控制字符拦截与多行确认。
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
        for chunk in _chunks(text, self.MAX_ARG):
            # -e 0：关闭 ydotool 的反斜杠转义，文本必须按字面量发送。
            self._exec(["type", *self._delay_args(), "-e", "0", "--", chunk])

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
                "per_char_delay": True, "daemon": True}


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


def multiline_risk(typer: Typer, text: str) -> str | None:
    """多行文本打进终端（或目标不可知）时的风险说明；无风险返回 None。"""
    body = text.rstrip("\n")
    if "\n" not in body:
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


# ------------------------------------------------------------------ 行编辑器
class LineEditor:
    """cbreak 模式下的最小行编辑器：方向键 / Home / End / 退格 / 括号粘贴"""

    def __init__(self, fd: int):
        self.fd = fd
        self.buf: list[str] = []
        self.pos = 0
        self.paste: str | None = None

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
        data = bytearray()
        while len(data) <= MAX_PAYLOAD:
            c = self._read_byte()
            if c is None:
                break
            data.append(c)
            if data.endswith(b"\x1b[201~"):
                del data[-6:]
                break
        return (data.decode("utf-8", "replace")
                .replace("\r\n", "\n").replace("\r", "\n"))

    @staticmethod
    def _width(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else
                   (0 if unicodedata.combining(c) else 1) for c in s)

    def _render(self, prompt: str) -> None:
        if self.paste is not None:
            nlines = self.paste.count("\n") + 1
            if self.paste.endswith("\n"):
                nlines -= 1
            display = f"[已粘贴 {len(self.paste)} 字符 / {nlines} 行]"
            tail = 0
        else:
            display = "".join(self.buf)
            tail = self._width(display[self.pos:])
        self._w("\r\x1b[K" + prompt + display)
        if tail:
            self._w(f"\x1b[{tail}D")

    def read(self, prompt: str) -> tuple[str, str | None]:
        self.buf, self.pos, self.paste = [], 0, None
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
                    self.buf, self.pos = [], 0
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
                self._render(prompt)
                continue
            if c == 0x17:
                while self.pos > 0 and self.buf[self.pos - 1] == " ":
                    del self.buf[self.pos - 1]
                    self.pos -= 1
                while self.pos > 0 and self.buf[self.pos - 1] != " ":
                    del self.buf[self.pos - 1]
                    self.pos -= 1
                self._render(prompt)
                continue
            if c < 0x20:
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
                 auto_enter: bool = False):
        self.sleep = sleep
        self.delay = delay
        self.auto_enter = auto_enter


def countdown(seconds: float, typer: Typer, cancelable: bool = True) -> bool:
    """倒计时，顺手显示"会敲到哪个窗口"。返回 False 表示被取消。"""
    try:
        target = target_label(typer)
    except Exception:
        target = "未知窗口"
    if seconds <= 0:
        return True
    end = time.time() + seconds
    try:
        while True:
            left = end - time.time()
            if left <= 0:
                break
            tail = "（按任意键取消）" if cancelable else ""
            sys.stdout.write(f"\r  目标 {target}  {left:4.1f}s {tail}   ")
            sys.stdout.flush()
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if r and cancelable:
                os.read(sys.stdin.fileno(), 1024)
                sys.stdout.write("\r" + " " * 70 + "\r  已取消\r\n")
                return False
    except KeyboardInterrupt:
        sys.stdout.write("\r" + " " * 70 + "\r  已取消\r\n")
        return False
    sys.stdout.write("\r" + " " * 70 + "\r")
    return True


def warn_multiline_target(typer: Typer, text: str) -> None:
    msg = multiline_risk(typer, text)
    if msg:
        sys.stderr.write(f"⚠  {msg}\n")


BANNER = """\
autopaste REPL ── 回车后倒计时再输入；粘贴多行文本没有任何引号/转义问题
  :s 3 倒计时 | :d 0.02 每字间隔 | :nl 自动回车 | :k 组合键 | :f 文件
  :r 重复上次 | :t 状态/目标窗口 | :h 帮助 | :q 退出
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
  :h              显示本帮助
  :q              退出

其它: 直接粘贴(含多行) -> 回车; 途中 Ctrl-C 取消当前行/倒计时, Ctrl-D 退出。
"""


def repl(typer: Typer, cfg: Settings) -> int:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    ed = LineEditor(fd)
    last: str | None = None
    sys.stdout.write(BANNER)
    sys.stdout.flush()

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
                try:
                    if name in ("q", "quit", "exit"):
                        break
                    elif name in ("h", "help", "?"):
                        sys.stdout.write(HELP)
                    elif name == "t":
                        sys.stdout.write(
                            f"  倒计时={cfg.sleep}s  字符间隔={cfg.delay}s  "
                            f"自动回车={'开' if cfg.auto_enter else '关'}\n"
                            f"  当前目标={target_label(typer)}\n")
                    elif name == "s":
                        cfg.sleep = _num(arg, 0.0, MAX_COUNTDOWN, "倒计时")
                    elif name == "d":
                        cfg.delay = _num(arg, 0.0, MAX_DELAY, "字符间隔")
                        typer.delay = cfg.delay
                    elif name == "nl":
                        cfg.auto_enter = not cfg.auto_enter
                        sys.stdout.write("  自动回车: %s\n"
                                         % ("开" if cfg.auto_enter else "关"))
                    elif name == "k":
                        validate_payload(arg)
                        if countdown(cfg.sleep, typer):
                            typer.press(arg)
                    elif name == "f":
                        check_regular_file(arg)
                        with open(arg, "r", encoding="utf-8",
                                  errors="replace") as f:
                            body = f.read()
                        validate_payload(body)
                        warn_multiline_target(typer, body)
                        if countdown(cfg.sleep, typer):
                            typer.type_text(body)
                        last = body
                    elif name == "r":
                        if last is None:
                            sys.stdout.write("  还没有可重复的内容\n")
                        elif countdown(cfg.sleep, typer):
                            typer.type_text(last)
                    elif name == "":
                        pass
                    else:
                        sys.stdout.write(f"  未知命令 :{name}，输入 :h 查看帮助\n")
                except (Abort, ValueError, OSError) as e:
                    sys.stdout.write(f"  已拦截: {e}\n")
                sys.stdout.flush()
                continue

            try:
                validate_payload(text)
            except Abort as e:
                sys.stdout.write(f"  已拦截: {e}\n")
                continue
            last = text
            warn_multiline_target(typer, text)
            if countdown(cfg.sleep, typer):
                typer.type_text(text)
                if cfg.auto_enter:
                    typer.press("enter")
            sys.stdout.flush()
    finally:
        try:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()
        finally:
            cleanup()
    sys.stdout.write("bye\n")
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

安全:
  * 默认拒绝 root 运行、拒绝控制字符（ESC 等）、拒绝 FIFO/设备文件；
  * 多行文本打进终端会二次确认（Wayland 下所有多行都确认）；
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
    ap.add_argument("-y", "--yes", action="store_true",
                    help="跳过确认提示（终端目标 + 多行文本时使用）")
    ap.add_argument("--allow-control", action="store_true",
                    help="允许输入控制字符（ESC 等），确认安全后再用")
    ap.add_argument("--backend", choices=("auto", "x11", "wtype", "ydotool"),
                    default="auto",
                    help="键盘注入后端：auto|x11|wtype|ydotool，默认 auto")
    ap.add_argument("--list-backends", action="store_true",
                    help="列出各后端可用性与探测依据后退出")
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
            rows.append(("ydotool", True,
                         "PATH=" + (which("ydotool") or "") +
                         f"，socket={sock}" +
                         (f"，版本 {yver}" if yver else "")))
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

    if args.list_backends:
        sys.stdout.write(format_backends())
        return 0

    # ---- 安全闸门 1: 不以 root 身份注入按键
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not args.allow_root:
        sys.stderr.write(
            "拒绝以 root 运行：按键注入等同于代替你敲键盘，"
            "以 root 执行会把任何窗口都暴露给它。\n"
            "如确有需要，请显式加 --allow-root。\n")
        return 2

    try:
        delay = _num(str(args.delay), 0.0, MAX_DELAY, "字符间隔")
        sleep = 0.0 if args.sleep is None else _num(
            str(args.sleep), 0.0, MAX_COUNTDOWN, "倒计时")
    except Abort as e:
        sys.stderr.write(f"{e}\n")
        return 1

    interactive = args.repl or (not args.text and not args.file
                                and not args.key and sys.stdin.isatty())

    try:
        if interactive:
            cfg = Settings(sleep=2.0 if args.sleep is None else sleep,
                           delay=delay)
            typer = Typer(backend=args.backend, delay=cfg.delay)
            return repl(typer, cfg)

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

        if payload:
            args.key.insert(0, payload)
        if not args.key:
            ap.print_help()
            return 1

        for item in args.key:
            validate_payload(item, args.allow_control)

        typer = Typer(backend=args.backend, delay=delay)

        # ---- dry-run：只预览
        if args.dry_run:
            caps = typer.capabilities()
            sys.stdout.write(f"后端     : {caps.get('name')}\n")
            sys.stdout.write(f"目标窗口 : {target_label(typer)}\n")
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
            sys.stdout.write("dry-run：未发送任何按键。\n")
            return 0

        # ---- 安全闸门 2: 终端里的多行文本（Wayland 下所有多行都要确认）
        joined = "".join(args.key)
        risk = multiline_risk(typer, joined)
        if risk:
            sys.stderr.write(f"⚠  {risk}\n")
            if not args.yes and not _confirm("继续？"):
                sys.stderr.write("已取消。\n")
                return 3

        if not countdown(sleep, typer):
            return 130

        for item in args.key:
            if len(item) == 1 or not is_combo(item):
                if item.lower() in SPECIAL or item.lower() in MODIFIERS:
                    typer.press(item)
                else:
                    typer.type_text(item)
            else:
                typer.press(item)
        return 0
    except BackendUnavailable as e:
        sys.stderr.write(f"{e}\n")
        return 2
    except BackendError as e:
        sys.stderr.write(f"已拦截: {e}\n")
        return 1
    except Abort as e:
        sys.stderr.write(f"已拦截: {e}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\n已中断。\n")
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


if __name__ == "__main__":
    _install_signals()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
