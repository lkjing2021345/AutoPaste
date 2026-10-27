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


class FakeX11:
    def __init__(self, lo=8, hi=64, per=2):
        self.lo = lo
        self.hi = hi
        self.per = per
        self.dpy = 0x1234
        self.keysym_to_keycode = {}
        self.keycode_to_keysym = {}
        self.name_to_keysym = {}
        self.mapping_history = []      # [(keycode, [keysym...]), ...]
        self.flush_count = 0
        self.close_count = 0
        self.freed = 0

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
        self.mapping_history.append((keycode, list(keysyms)[:per]))

    def XkbKeycodeToKeysym(self, dpy, keycode, group, level):
        return self.keycode_to_keysym.get((keycode, group, level), 0)


class FakeXtst:
    def __init__(self):
        self.events = []            # [(keycode, is_press), ...]
        self.raise_on = set()       # {(keycode, is_press)}

    def XTestFakeKeyEvent(self, dpy, keycode, is_press, delay):
        if (keycode, bool(is_press)) in self.raise_on:
            raise RuntimeError("fake XTest 故障")
        self.events.append((keycode, bool(is_press)))
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
