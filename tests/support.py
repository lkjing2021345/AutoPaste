# -*- coding: utf-8 -*-
"""测试用的假 X11 / XTest 后端（纯标准库，不需要 DISPLAY）。

autopaste.py 把它们当模块级全局 `x11` / `xtst` 使用，测试里用
`mock.patch.object(autopaste, "x11", fake)` 替换即可，无需改生产代码。
"""

import contextlib
import ctypes
import io
import os
import tempfile
from unittest import mock

import autopaste

KEYSYM_SHIFT = 0xFFE1


def _set_out(ptr, value):
    """把 ctypes.byref() 包出来的输出参数写回 value。"""
    obj = getattr(ptr, "_obj", None)
    if obj is not None:
        obj.value = value
        return
    ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int))[0] = value


def _null_ptr_out(ptr, ptr_type):
    ctypes.cast(ptr, ctypes.POINTER(ptr_type))[0] = None


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
        # ---- 窗口枚举/激活用（03.1）
        self.root = 0x100
        self.properties = {}      # (xid, atom) -> (type_atom, format, payload)
        self.atoms = {"CARDINAL": 6, "WINDOW": 33, "ATOM": 4,
                      "UTF8_STRING": 271}
        self.atom_names = {v: k for k, v in self.atoms.items()}
        self.window_attrs = {}    # xid -> dict
        self.class_hints = {}     # xid -> (instance, cls)
        self.names = {}           # xid -> bytes
        self.tree = {}            # xid -> [children]
        self.parent = {}          # xid -> parent
        self.focus = 0
        self.focus_script = []    # 每次 XGetInputFocus 弹出一个值
        self.client_messages = []
        self.error_handlers = []
        self.prev_error_handler = 0
        self.raised = []
        self.input_focus_sets = []
        self.ignore_set_input_focus = False
        self._buffers = []

    @property
    def mapping_history(self):
        """[(keycode, [keysym...]), ...]：remap 与 restore 按时间顺序。"""
        return [(e[1], e[2]) for e in self.log.entries
                if e[0] in ("remap", "restore")]

    # ---- window helpers
    def atom_id(self, name, only_if_exists=False):
        if name in self.atoms:
            return self.atoms[name]
        if only_if_exists:
            return 0
        aid = max(self.atoms.values(), default=0) + 1
        self.atoms[name] = aid
        self.atom_names[aid] = name
        return aid

    def set_prop(self, xid, name, payload, type_name=None, fmt=None):
        if fmt is None:
            fmt = 8 if isinstance(payload, (bytes, bytearray)) else 32
        default_type = "UTF8_STRING" if fmt == 8 else "CARDINAL"
        self.properties[(xid, self.atom_id(name))] = (
            self.atom_id(type_name or default_type), fmt, payload)

    def add_window(self, xid, *, cls="", instance="", title="",
                   title_bytes=None, pid=None, desktop=None,
                   override_redirect=False, map_state=2,
                   net_wm_name=True, window_type=()):
        self.window_attrs[xid] = {
            "override_redirect": int(bool(override_redirect)),
            "map_state": map_state,
        }
        self.class_hints[xid] = (instance, cls)
        if title_bytes is None:
            title_bytes = (title or "").encode("utf-8")
        self.names[xid] = title_bytes
        if net_wm_name and title_bytes:
            self.set_prop(xid, "_NET_WM_NAME", title_bytes,
                          "UTF8_STRING")
        if pid is not None:
            self.set_prop(xid, "_NET_WM_PID", [pid])
        if desktop is not None:
            self.set_prop(xid, "_NET_WM_DESKTOP", [desktop])
        if window_type:
            self.set_prop(xid, "_NET_WM_WINDOW_TYPE",
                          [self.atom_id(t) for t in window_type],
                          "ATOM")
        self.tree.setdefault(xid, [])

    def add_child(self, parent, child):
        self.tree.setdefault(parent, []).append(child)
        self.parent[child] = parent
        self.tree.setdefault(child, [])

    def set_focus_script(self, values):
        self.focus_script = list(values)

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

    # ---- 窗口 / 属性 / 焦点替身（03.1）
    def XDefaultRootWindow(self, dpy):
        return self.root

    def XInternAtom(self, dpy, name, only_if_exists):
        if isinstance(name, bytes):
            name = name.decode("ascii")
        return self.atom_id(name, bool(only_if_exists))

    def XGetWindowProperties(self):
        return self.properties

    def XGetWindowProperty(self, dpy, win, prop, offset, length, delete,
                           req_type, actual_type, actual_format, nitems,
                           bytes_after, data_ptr):
        entry = self.properties.get((win, prop))
        if entry is None:
            _set_out(actual_type, 0)
            _set_out(actual_format, 0)
            _set_out(nitems, 0)
            _set_out(bytes_after, 0)
            _null_ptr_out(data_ptr, ctypes.POINTER(ctypes.c_ubyte))
            return 0
        ptype, fmt, payload = entry
        _set_out(actual_type, ptype)
        if req_type and req_type != ptype:
            # 类型不符：Xlib 仍返回 Success，但没有数据
            _set_out(actual_format, 0)
            _set_out(nitems, 0)
            _set_out(bytes_after, 0)
            _null_ptr_out(data_ptr, ctypes.POINTER(ctypes.c_ubyte))
            return 0
        _set_out(actual_format, fmt)
        _set_out(nitems, len(payload))
        _set_out(bytes_after, 0)
        if fmt == 32:
            buf = (ctypes.c_ulong * max(len(payload), 1))(*payload)
        else:
            buf = ctypes.create_string_buffer(bytes(payload),
                                              max(len(payload), 1))
        self._buffers.append(buf)
        ctypes.cast(data_ptr,
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = \
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        return 0

    def XQueryTree(self, dpy, win, root_ret, parent_ret, children_ret,
                   nchildren_ret):
        children = self.tree.get(win, [])
        _set_out(root_ret, self.root)
        _set_out(parent_ret, self.parent.get(win, self.root))
        buf = (ctypes.c_ulong * max(len(children), 1))(*children)
        self._buffers.append(buf)
        ctypes.cast(children_ret,
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)))[0] = \
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))
        _set_out(nchildren_ret, len(children))
        return 1

    def XGetWindowAttributes(self, dpy, win, attrs):
        if win not in self.window_attrs:
            return 0
        target = getattr(attrs, "_obj", attrs)
        for key, value in self.window_attrs[win].items():
            setattr(target, key, value)
        return 1

    def XFetchName(self, dpy, win, name_ptr):
        raw = self.names.get(win)
        if raw is None:
            return 0
        buf = ctypes.create_string_buffer(raw, len(raw) + 1)
        self._buffers.append(buf)
        name_ptr._obj.value = ctypes.cast(buf, ctypes.c_char_p).value
        return 1

    def XGetClassHint(self, dpy, win, hint_ptr):
        hint = self.class_hints.get(win)
        if hint is None:
            return 0
        instance, cls = hint
        target = getattr(hint_ptr, "_obj", hint_ptr)
        for field, value in (("res_name", instance), ("res_class", cls)):
            if value:
                buf = ctypes.create_string_buffer(value.encode("utf-8"),
                                                  len(value) + 1)
                self._buffers.append(buf)
                setattr(target, field,
                        ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return 1

    def XGetInputFocus(self, dpy, win, revert):
        value = self.focus_script.pop(0) if self.focus_script else self.focus
        _set_out(win, value)
        _set_out(revert, 1)
        return 1

    def XRaiseWindow(self, dpy, win):
        self.raised.append(win)

    def XSetInputFocus(self, dpy, win, revert, timestamp):
        self.input_focus_sets.append((win, revert, timestamp))
        if not self.ignore_set_input_focus:
            self.focus = win

    def XSendEvent(self, dpy, win, propagate, mask, event_ptr):
        obj = getattr(event_ptr, "_obj", None)
        if obj is None:
            target = event_ptr
        else:
            target = ctypes.byref(obj)
        ev = ctypes.cast(target,
                         ctypes.POINTER(autopaste.XClientMessageEvent)).contents
        self.client_messages.append({
            "window": ev.window, "type": ev.message_type,
            "format": ev.format, "l": [ev.data.l[i] for i in range(5)],
            "mask": mask,
        })
        return 1

    def XSetErrorHandler(self, handler):
        self.error_handlers.append(handler)
        old = self.prev_error_handler
        self.prev_error_handler = handler
        return old


class FakeXtst:
    def __init__(self, log=None):
        self.log = EventLog() if log is None else log
        self.raise_on = set()       # {(keycode, is_press)}
        self.delays = []            # 每次 XTestFakeKeyEvent 的 delay(ms)

    @property
    def events(self):
        """[(keycode, is_press), ...]：只取 tap，按时间顺序。"""
        return self.log.select("tap")

    def XTestFakeKeyEvent(self, dpy, keycode, is_press, delay):
        if (keycode, bool(is_press)) in self.raise_on:
            raise RuntimeError("fake XTest 故障")
        self.log.record("tap", keycode, bool(is_press))
        self.delays.append(delay)
        return 1


@contextlib.contextmanager
def temp_home():
    """临时 HOME/XDG 目录：保证测试不读真实 ~/.config/autopaste/config.toml。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"HOME": tmp,
               "XDG_CONFIG_HOME": os.path.join(tmp, "config"),
               "XDG_STATE_HOME": os.path.join(tmp, "state")}
        with mock.patch.dict(os.environ, env):
            yield tmp


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
