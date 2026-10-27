#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可复现的事件发送基准（不需要真实焦点窗口、不注入任何按键）。

用 ``tests/support.py`` 的假 X11/XTest 后端替换真实库，只测本程序的
Python 侧开销与 flush 次数：

  * ``before`` = 批量改造前的逐字符路径（每字符 flush + 客户端 sleep）；
  * ``after``  = 批量路径（分块 flush + XTest 服务端 delay）。

``--delay`` > 0 时用"假服务器在 press 事件上 sleep"模拟 XTEST 的服务端延迟，
因此两种模式的下限都约为 1/delay 字/秒；差异来自每字符的 flush/syscall。
真实吞吐还受 X 服务器、目标应用与键盘布局影响，请以实机为准。

    python3 tools/bench.py
    python3 tools/bench.py --count 20000 --delay 0.008
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import autopaste                                    # noqa: E402
from support import FakeX11, FakeXtst              # noqa: E402


class ServerDelayXtst(FakeXtst):
    """模拟 XTest 的 delay 是"服务器睡眠"：press 事件让服务器睡 delay ms。"""

    def XTestFakeKeyEvent(self, dpy, keycode, is_press, delay):
        rc = super().XTestFakeKeyEvent(dpy, keycode, is_press, delay)
        if delay:
            time.sleep(delay / 1000.0)
        return rc


def make_backend(delay):
    x11 = FakeX11(lo=8, hi=64, per=2)
    for i, ch in enumerate("abcdefghij"):
        x11.add_key(ord(ch), 10 + i, level0=ord(ch))
    xtst = ServerDelayXtst()
    autopaste.x11 = x11
    autopaste.xtst = xtst
    backend = autopaste.X11Backend(delay=delay, remap_wait=0.0)
    backend.close = lambda: None
    return backend, x11


def run(sample, delay, batch):
    backend, x11 = make_backend(delay)
    backend.batch_enabled = batch
    start = time.perf_counter()
    backend.type_text(sample)
    elapsed = time.perf_counter() - start
    return elapsed, x11.flush_count


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=20000,
                    help="delay=0 时的字符数（默认 20000）")
    ap.add_argument("--count-delay", type=int, default=500,
                    help="delay>0 时的字符数（默认 500，避免基准太慢）")
    ap.add_argument("--delay", type=float, default=0.008,
                    help="第二个场景的每字符间隔（默认 0.008）")
    args = ap.parse_args(argv)

    scenarios = [
        ("0", 0.0, args.count),
        (str(args.delay), args.delay, args.count_delay),
    ]
    print(f"{'模式':<8}{'delay':>8}{'字符':>8}{'秒':>10}{'字符/秒':>12}"
          f"{'flush':>8}")
    for label, delay, count in scenarios:
        sample = ("abcdefghij" * (count // 10 + 1))[:count]
        for mode in ("before", "after"):
            elapsed, flushes = run(sample, delay, batch=(mode == "after"))
            rate = count / elapsed if elapsed else float("inf")
            print(f"{mode:<8}{label:>8}{count:>8}{elapsed:>10.3f}"
                  f"{rate:>12.0f}{flushes:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
