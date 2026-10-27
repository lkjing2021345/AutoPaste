# -*- coding: utf-8 -*-
"""进程入口 console_main：信号安装 + 中断走清理路径（不连接 X11）。

pip/pipx 生成的 console script 指向 ``autopaste:console_main``，
``python -m autopaste`` 与直接执行脚本也走同一个函数；本文件守护这条契约：
SIGINT/SIGTERM/SIGHUP 都必须被注册，且中断最终返回 130、Typer 仍被 close。
"""

import os
import signal
import unittest
from unittest import mock

import autopaste
from support import redirect_std, temp_home


def _registered_handlers(signal_mock):
    """从被打桩的 signal.signal 调用里取出 {signum: handler}。"""
    return {call.args[0]: call.args[1] for call in signal_mock.call_args_list}


class EntryPointTests(unittest.TestCase):
    def test_installs_sigint_sigterm_sighup(self):
        with mock.patch.object(autopaste.signal, "signal") as sig, \
                mock.patch.object(autopaste, "main", return_value=0):
            code = autopaste.console_main()
        self.assertEqual(code, 0)
        handlers = _registered_handlers(sig)
        self.assertEqual(set(handlers),
                         {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
        # 三个信号必须共用同一个 handler，行为不能因信号不同而分叉
        self.assertEqual(len({id(h) for h in handlers.values()}), 1)

    def test_handler_raises_keyboard_interrupt(self):
        with mock.patch.object(autopaste.signal, "signal") as sig, \
                mock.patch.object(autopaste, "main", return_value=0):
            autopaste.console_main()
        handler = _registered_handlers(sig)[signal.SIGTERM]
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.assertRaises(KeyboardInterrupt):
                handler(signum, None)

    def test_interrupt_returns_130(self):
        with mock.patch.object(autopaste.signal, "signal"), \
                mock.patch.object(autopaste, "main",
                                  side_effect=KeyboardInterrupt), \
                redirect_std() as (_, err):
            code = autopaste.console_main()
        self.assertEqual(code, 130)
        self.assertIn("已中断", err.getvalue())

    def test_signal_during_main_returns_130_and_cleanup_runs(self):
        """handler 在 main() 体内触发 -> 130，且 Typer.close 已注册到 atexit。"""
        closed = []

        class FakeTyper:
            def __init__(self, *args, **kwargs):
                autopaste.atexit.register(self.close)

            def close(self):
                closed.append("closed")

            def capabilities(self):
                return {"name": "x11", "can_focus_info": True,
                        "can_paste_mode": False, "unicode": True,
                        "needs_remap": True, "combos": True,
                        "per_char_delay": True, "daemon": False}

            def focus_info(self):
                return None

            def type_text(self, text):
                raise AssertionError("中断前的代码不应真的注入")

            def press(self, combo):
                raise AssertionError("中断前的代码不应真的注入")

        def fake_countdown(seconds, typer):
            # 模拟外部 SIGTERM 到达：走真实注册的 handler
            _registered_handlers(sig)[signal.SIGTERM](signal.SIGTERM, None)

        with temp_home(), \
                mock.patch.object(autopaste.signal, "signal") as sig, \
                mock.patch.object(autopaste, "Typer", FakeTyper), \
                mock.patch.object(autopaste, "atexit") as atexit_mock, \
                mock.patch.object(autopaste, "countdown", fake_countdown), \
                mock.patch.object(os, "geteuid", return_value=(os.geteuid() or 1),
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                redirect_std() as (_, err):
            code = autopaste.console_main(["-y", "hi"])
        self.assertEqual(code, 130)
        self.assertIn("已中断", err.getvalue())
        # 清理是 atexit 兜底：注册必须存在，回调确实关闭 Typer
        callbacks = [c.args[0] for c in atexit_mock.register.call_args_list]
        self.assertTrue(callbacks)
        for cb in callbacks:
            cb()
        self.assertEqual(closed, ["closed"])


if __name__ == "__main__":
    unittest.main()
