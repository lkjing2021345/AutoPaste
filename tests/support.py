# -*- coding: utf-8 -*-
"""测试用的假 X11 / XTest 后端（纯标准库，不需要 DISPLAY）。

autopaste.py 把它们当模块级全局 `x11` / `xtst` 使用，测试里用
`mock.patch.object(autopaste, "x11", fake)` 替换即可，无需改生产代码。
"""

import contextlib
import ctypes
import io
import os

KEYSYM_SHIFT = 0xFFE1


def _set_out(ptr, value):
    """把 ctypes.byref() 包出来的输出参数写回 value。"""
    obj = getattr(ptr, "_obj", None)
    if obj is not None:
        obj.value = value
        return
    ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int))[0] = value


class EventLog:
    """FakeX11 / FakeXtst 共享的事件流：按真实发生顺序记录。

    两个假后端如果各记各的历史，就无法验证"先 remap、再 tap、后 restore"
    这类跨后端的时序；共享一条日志后 ``kinds()`` 直接给出发生顺序。

    条目格式：``("remap"|"restore", keycode, [keysym...])`` 或
    ``("tap", keycode, is_press)``。
    """

    def __init__(self):
        self.entries = []

    def record(self, kind, *data):
        self.entries.append((kind,) + data)

    def kinds(self):
        return [e[0] for e in self.entries]

    def select(self, kind):
        return [e[1:] for e in self.entries if e[0] == kind]


class FakeX11:
    def __init__(self, lo=8, hi=64, per=2, log=None):
        self.lo = lo
        self.hi = hi
        self.per = per
        self.log = EventLog() if log is None else log
        self.dpy = 0x1234
        self.keysym_to_keycode = {}
        self.keycode_to_keysym = {}
        self.name_to_keysym = {}
        self.flush_count = 0
        self.close_count = 0
        self.freed = 0

    @property
    def mapping_history(self):
        """[(keycode, [keysym...]), ...]：remap 与 restore 按时间顺序。"""
        return [(e[1], e[2]) for e in self.log.entries
                if e[0] in ("remap", "restore")]

    # ---- setup helpers
    def add_key(self, keysym, keycode, level0=None, level1=None):
        self.keysym_to_keycode[keysym] = keycode
        self.keycode_to_keysym[(keycode, 0, 0)] = (
            keysym if level0 is None else level0)
        if level1 is not None:
            self.keycode_to_keysym[(keycode, 0, 1)] = level1
        return keycode

    def add_named(self, name, keysym):
        self.name_to_keysym[name.encode()] = keysym

    # ---- Xlib 函数替身
    def XOpenDisplay(self, name):
        return self.dpy

    def XCloseDisplay(self, dpy):
        self.close_count += 1

    def XFlush(self, dpy):
        self.flush_count += 1

    def XSync(self, dpy, discard):
        self.flush_count += 1

    def XFree(self, ptr):
        self.freed += 1

    def XStringToKeysym(self, name):
        return self.name_to_keysym.get(name, 0)

    def XKeysymToKeycode(self, dpy, keysym):
        return self.keysym_to_keycode.get(keysym, 0)

    def XDisplayKeycodes(self, dpy, lo, hi):
        _set_out(lo, self.lo)
        _set_out(hi, self.hi)

    def XGetKeyboardMapping(self, dpy, first, count, per):
        _set_out(per, self.per)
        return (ctypes.c_ulong * max(self.per, 1))()

    def XChangeKeyboardMapping(self, dpy, keycode, per, keysyms, num):
        syms = list(keysyms)[:per]
        # 全 0 的映射就是 X11Backend._restore_keymap()，其余是临时 remap
        kind = "restore" if not any(syms) else "remap"
        self.log.record(kind, keycode, syms)

    def XkbKeycodeToKeysym(self, dpy, keycode, group, level):
        return self.keycode_to_keysym.get((keycode, group, level), 0)


class FakeXtst:
    def __init__(self, log=None):
        self.log = EventLog() if log is None else log
        self.raise_on = set()       # {(keycode, is_press)}

    @property
    def events(self):
        """[(keycode, is_press), ...]：只取 tap，按时间顺序。"""
        return self.log.select("tap")

    def XTestFakeKeyEvent(self, dpy, keycode, is_press, delay):
        if (keycode, bool(is_press)) in self.raise_on:
            raise RuntimeError("fake XTest 故障")
        self.log.record("tap", keycode, bool(is_press))
        return 1


@contextlib.contextmanager
def redirect_std(monkey_targets=None):
    """同时吞掉 stdout/stderr 的上下文。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def editor_input(data: bytes):
    """返回 (read_fd, close)：read_fd 里预先写入 data 并关闭写端。"""
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, data)
    finally:
        os.close(write_fd)
    return read_fd
