#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autopaste - 把文本"敲"进任意 X11 窗口（XTEST 键盘注入，零依赖）

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
    * 多行文本打进终端 = 逐行执行命令，因此 CLI 模式下会二次确认。
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
import signal
import stat
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
x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")

P = ctypes.POINTER


class XClassHint(ctypes.Structure):
    # 注意: 字段用 POINTER(c_char) 而不是 c_char_p —— c_char_p 字段在被读取时
    # 会被 ctypes 自动转成 Python bytes, 那样就无法再拿到原始指针去 XFree,
    # 强行 cast 会 free 掉 Python 自己的内存 (glibc: invalid size)。
    _fields_ = [("res_name", P(ctypes.c_char)),
                ("res_class", P(ctypes.c_char))]


x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XFree.argtypes = [ctypes.c_void_p]
x11.XStringToKeysym.restype = ctypes.c_ulong
x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
x11.XKeysymToKeycode.restype = ctypes.c_ubyte
x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XDisplayKeycodes.argtypes = [ctypes.c_void_p, P(ctypes.c_int),
                                 P(ctypes.c_int)]
x11.XGetKeyboardMapping.restype = P(ctypes.c_ulong)
x11.XGetKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                    ctypes.c_int, P(ctypes.c_int)]
x11.XChangeKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_int, P(ctypes.c_ulong),
                                       ctypes.c_int]
x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
x11.XkbKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                   ctypes.c_int, ctypes.c_int]
x11.XGetInputFocus.argtypes = [ctypes.c_void_p, P(ctypes.c_ulong),
                               P(ctypes.c_int)]
x11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                           P(ctypes.c_char_p)]
x11.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                              P(XClassHint)]

xtst.XTestFakeKeyEvent.restype = ctypes.c_int
xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_ulong]

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

KEYSYM_SHIFT = 0xFFE1  # Shift_L


class Abort(Exception):
    """用户取消 / 安全检查拦截"""


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


# ------------------------------------------------------------------ 键盘注入
class Typer:
    def __init__(self, delay: float = 0.008, remap_wait: float = 0.02):
        self.dpy = x11.XOpenDisplay(None)
        if not self.dpy:
            raise Abort("无法连接 X 服务器（DISPLAY=%s）"
                        % os.environ.get("DISPLAY", "(未设置)"))
        self.delay = delay
        self.remap_wait = remap_wait
        self.shift_kc = x11.XKeysymToKeycode(self.dpy, KEYSYM_SHIFT)
        self.per = self._keysyms_per_keycode()
        self.spare_kc = self._find_spare_keycode()
        self._remapped = False
        self._closed = False
        atexit.register(self.close)

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
        target = describe_window(focused_window(typer.dpy))
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


def warn_terminal_target(typer: Typer, text: str) -> None:
    info = focused_window(typer.dpy)
    if is_terminal(info) and "\n" in text.rstrip("\n"):
        lines = len([l for l in text.splitlines() if l.strip()])
        sys.stderr.write(
            f"⚠  目标是终端 {describe_window(info)}：多行文本会被逐行执行"
            f"（共 {lines} 行）。确认内容安全后继续。\n")


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
                target = describe_window(focused_window(typer.dpy))
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
                            f"  当前目标={describe_window(focused_window(typer.dpy))}\n")
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
                        warn_terminal_target(typer, body)
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
            warn_terminal_target(typer, text)
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
        description="把文本敲进当前聚焦的 X11 窗口（XTEST，零依赖）。"
                    "不带参数运行进入交互 REPL。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  autopaste                          进入 REPL（粘贴文本零转义）
  autopaste -s 3 'hello'             倒数 3 秒后输入
  autopaste -f note.txt -s 2         输入文件
  autopaste --dry-run 'x < y'        只预览，不敲键
  autopaste -k ctrl+shift+v          发送组合键

安全:
  * 默认拒绝 root 运行、拒绝控制字符（ESC 等）、拒绝 FIFO/设备文件；
  * 多行文本打进终端会二次确认；
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


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # ---- 安全闸门 1: 不以 root 身份注入按键
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not args.allow_root:
        sys.stderr.write(
            "拒绝以 root 运行：按键注入等同于代替你敲键盘，"
            "以 root 执行会把任何窗口都暴露给它。\n"
            "如确有需要，请显式加 --allow-root。\n")
        return 2

    if not os.environ.get("DISPLAY"):
        sys.stderr.write("未设置 DISPLAY，autopaste 只支持 X11 会话"
                         "（Wayland 可考虑 ydotool）。\n")
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
            typer = Typer(delay=cfg.delay)
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

        typer = Typer(delay=delay)

        # ---- dry-run：只预览
        if args.dry_run:
            target = describe_window(focused_window(typer.dpy))
            sys.stdout.write(f"目标窗口 : {target}\n")
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
            missing = typer.resolvable("".join(args.key))
            if missing:
                sys.stdout.write(f"无法映射的字符: {''.join(missing)!r}\n")
            sys.stdout.write("dry-run：未发送任何按键。\n")
            return 0

        # ---- 安全闸门 2: 终端里的多行文本
        joined = "".join(args.key)
        info = focused_window(typer.dpy)
        if is_terminal(info) and "\n" in joined.rstrip("\n"):
            sys.stderr.write(
                f"⚠  目标是终端 {describe_window(info)}：即将逐行输入 "
                f"{len([l for l in joined.splitlines() if l.strip()])} 行内容，"
                f"多行文本会被逐行执行。\n")
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
