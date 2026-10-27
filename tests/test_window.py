# -*- coding: utf-8 -*-
"""03.1 窗口定向：枚举、匹配、歧义、激活与焦点校验（假 X11，不需要 DISPLAY）。"""

import io
import os
import unittest
from unittest import mock

import autopaste
from support import EventLog, FakeX11, FakeXtst, redirect_std, temp_home


class WindowBase(unittest.TestCase):
    def setUp(self):
        self.log = EventLog()
        self.x11 = FakeX11(lo=8, hi=64, per=2, log=self.log)
        self.xtst = FakeXtst(log=self.log)
        self.x11.add_key(ord("a"), 38, level0=ord("a"), level1=ord("A"))
        self.x11.add_key(autopaste.KEYSYM_SHIFT, 50)
        self.x11.add_named("Shift_L", autopaste.KEYSYM_SHIFT)
        self._patches = [
            mock.patch.object(autopaste, "x11", self.x11),
            mock.patch.object(autopaste, "xtst", self.xtst),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.backend = autopaste.X11Backend(delay=0.0, remap_wait=0.0)
        self.addCleanup(self.backend.close)

    def add_window(self, xid, **kwargs):
        self.x11.add_window(xid, **kwargs)
        self.x11.add_child(self.x11.root, xid)
        return xid

    def info(self, xid):
        return autopaste._window_info_raw(self.x11.dpy, xid)


class ListWindowsTests(WindowBase):
    def test_prefers_net_client_list(self):
        self.add_window(0x200, cls="kitty", title="one")
        self.add_window(0x201, cls="firefox", title="two")
        self.x11.set_prop(self.x11.root, "_NET_CLIENT_LIST", [0x200], "WINDOW")
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual([w.xid for w in wins], [0x200])

    def test_falls_back_to_query_tree_without_ewmh(self):
        self.add_window(0x200, cls="kitty", title="one")
        self.add_window(0x201, cls="firefox", title="two")
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual({w.xid for w in wins}, {0x200, 0x201})

    def test_query_tree_skips_override_and_unmapped(self):
        self.add_window(0x200, cls="kitty", title="visible")
        self.add_window(0x201, cls="menu", title="popup",
                        override_redirect=True)
        self.add_window(0x202, cls="hidden", title="iconified", map_state=0)
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual([w.xid for w in wins], [0x200])

    def test_query_tree_finds_client_inside_frame(self):
        self.add_window(0x300, cls="", title="")       # WM frame
        self.add_window(0x301, cls="kitty", title="inside")
        self.x11.tree[self.x11.root].remove(0x301)
        self.x11.add_child(0x300, 0x301)
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual([w.xid for w in wins], [0x301])

    def test_skips_menu_window_types(self):
        self.add_window(0x200, cls="kitty", title="ok")
        self.add_window(0x201, cls="x", title="popup",
                        window_type=("_NET_WM_WINDOW_TYPE_POPUP_MENU",))
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual([w.xid for w in wins], [0x200])

    def test_title_prefers_net_wm_name_utf8(self):
        self.add_window(0x200, cls="kitty", title="中文标题")
        info = self.info(0x200)
        self.assertEqual(info.title, "中文标题")

    def test_title_falls_back_to_wm_name_latin1(self):
        self.add_window(0x200, cls="old", title="",
                        title_bytes=b"caf\xe9", net_wm_name=False)
        self.assertEqual(self.info(0x200).title, "café")

    def test_class_pid_desktop_parsed(self):
        self.add_window(0x200, cls="kitty", instance="kitty-inst",
                        title="t", pid=1234, desktop=2)
        info = self.info(0x200)
        self.assertEqual((info.cls, info.instance, info.pid, info.desktop),
                         ("kitty", "kitty-inst", 1234, 2))

    def test_all_desktops_maps_to_none(self):
        self.add_window(0x200, cls="kitty", title="t", desktop=0xFFFFFFFF)
        self.assertIsNone(self.info(0x200).desktop)

    def test_sorted_by_stacking_order(self):
        self.add_window(0x200, cls="a", title="a")
        self.add_window(0x201, cls="b", title="b")
        self.x11.set_prop(self.x11.root, "_NET_CLIENT_LIST",
                          [0x200, 0x201], "WINDOW")
        self.x11.set_prop(self.x11.root, "_NET_CLIENT_LIST_STACKING",
                          [0x201, 0x200], "WINDOW")
        wins = autopaste.list_windows_x11(self.x11.dpy)
        self.assertEqual([w.xid for w in wins], [0x201, 0x200])

    def test_row_is_tab_separated_and_sanitized(self):
        self.add_window(0x200, cls="kitty", title="a\tb\nc\x1b[31m", pid=1,
                        desktop=0)
        line = autopaste.format_window_row(self.info(0x200), 0x200)
        cols = line.split("\t")
        self.assertEqual(len(cols), 6)
        self.assertEqual(cols[0], "0x00000200")
        self.assertEqual(cols[1], "kitty")
        self.assertEqual(cols[2], "1")
        self.assertEqual(cols[4], "*")
        self.assertNotIn("\x1b", cols[5])
        self.assertNotIn("\n", cols[5])

    def test_header_constant(self):
        self.assertEqual(autopaste.WINDOW_HEADER,
                         "ID\tCLASS\tPID\tDESKTOP\tACTIVE\tTITLE")


class MatchTests(WindowBase):
    def setUp(self):
        super().setUp()
        self.kitty = autopaste.WindowInfo(0x200, "kitty", "kitty", "~/src — nvim",
                                          100, 1)
        self.firefox = autopaste.WindowInfo(0x201, "Firefox", "Navigator",
                                            "GitHub", 200, 2)
        self.wins = [self.kitty, self.firefox]

    def test_default_is_case_insensitive_substring(self):
        self.assertEqual(autopaste.match_windows(self.wins, "KIT"),
                         [self.kitty])
        self.assertEqual(autopaste.match_windows(self.wins, "github"),
                         [self.firefox])

    def test_default_searches_title_too(self):
        self.assertEqual(autopaste.match_windows(self.wins, "nvim"),
                         [self.kitty])

    def test_class_prefix_only_checks_class_and_instance(self):
        self.assertEqual(autopaste.match_windows(self.wins, "class:kitty"),
                         [self.kitty])
        # 只出现在标题里的词不能匹配 class:
        self.assertEqual(autopaste.match_windows(self.wins, "class:nvim"), [])

    def test_title_prefix(self):
        self.assertEqual(autopaste.match_windows(self.wins, "title:github"),
                         [self.firefox])
        self.assertEqual(autopaste.match_windows(self.wins, "title:kitty"), [])

    def test_id_hex_and_decimal(self):
        self.assertEqual(autopaste.match_windows(self.wins, "id:0x200"),
                         [self.kitty])
        self.assertEqual(autopaste.match_windows(self.wins, "id:512"),
                         [self.kitty])
        with self.assertRaises(autopaste.Abort):
            autopaste.match_windows(self.wins, "id:zzz")

    def test_pid_prefix(self):
        self.assertEqual(autopaste.match_windows(self.wins, "pid:200"),
                         [self.firefox])
        with self.assertRaises(autopaste.Abort):
            autopaste.match_windows(self.wins, "pid:abc")

    def test_regex_is_opt_in(self):
        # 默认不是正则：点号按字面量处理
        self.assertEqual(autopaste.match_windows(self.wins, "kit.y"), [])
        self.assertEqual(autopaste.match_windows(self.wins, "re:kit.y"),
                         [self.kitty])

    def test_invalid_regex_aborts(self):
        with self.assertRaises(autopaste.Abort) as ctx:
            autopaste.match_windows(self.wins, "re:(")
        self.assertIn("re:(", str(ctx.exception))

    def test_no_match_returns_empty(self):
        self.assertEqual(autopaste.match_windows(self.wins, "nope"), [])


class PickTests(unittest.TestCase):
    def make(self, n=2):
        return [autopaste.WindowInfo(0x200 + i, "kitty", "kitty",
                                     f"win {i}", 100 + i, 1)
                for i in range(n)]

    def test_single_match(self):
        wins = self.make(1)
        self.assertIs(autopaste.pick_window(wins, active_xid=0x999,
                                            tty=False), wins[0])

    def test_zero_match_exits_2(self):
        with self.assertRaises(autopaste.WindowUnavailable) as ctx:
            autopaste.pick_window([], tty=False)
        self.assertEqual(ctx.exception.code, 2)

    def test_prefers_current_active_match(self):
        wins = self.make(3)
        with redirect_std() as (_, err):
            picked = autopaste.pick_window(wins, active_xid=0x201, tty=False)
        self.assertEqual(picked.xid, 0x201)
        self.assertIn("当前激活", err.getvalue())

    def test_ambiguous_non_tty_exits_2_with_candidates(self):
        wins = self.make(2)
        with self.assertRaises(autopaste.WindowUnavailable) as ctx:
            autopaste.pick_window(wins, active_xid=None, tty=False)
        self.assertEqual(ctx.exception.code, 2)
        msg = str(ctx.exception)
        self.assertIn("0x00000200", msg)
        self.assertIn("0x00000201", msg)

    def test_ambiguous_tty_prompts_and_enter_picks_top(self):
        wins = self.make(2)
        with mock.patch("sys.stdin", io.StringIO("\n")), \
                redirect_std() as (out, _):
            picked = autopaste.pick_window(wins, active_xid=None, tty=True)
        self.assertEqual(picked.xid, 0x201)       # 最上层（列表末尾）在前
        self.assertIn("选择 [1-2", out.getvalue())

    def test_ambiguous_tty_cancel_exits_3(self):
        wins = self.make(2)
        with mock.patch("sys.stdin", io.StringIO("q\n")), redirect_std():
            with self.assertRaises(autopaste.WindowCancelled) as ctx:
                autopaste.pick_window(wins, active_xid=None, tty=True)
        self.assertEqual(ctx.exception.code, 3)

    def test_ambiguous_tty_eof_cancels(self):
        wins = self.make(2)
        with mock.patch("sys.stdin", io.StringIO("")), redirect_std():
            with self.assertRaises(autopaste.WindowCancelled):
                autopaste.pick_window(wins, active_xid=None, tty=True)


class ActivateTests(WindowBase):
    def setUp(self):
        super().setUp()
        self.add_window(0x200, cls="kitty", instance="kitty", title="t")
        self.x11.set_prop(self.x11.root, "_NET_ACTIVE_WINDOW", [0x200],
                          "WINDOW")
        self.x11.focus = 0x999
        self.target = self.info(0x200)

    def test_already_focused_skips_activation(self):
        self.x11.focus = 0x200
        self.assertTrue(self.backend.activate_window(self.target,
                                                     timeout=0.01))
        self.assertEqual(self.x11.client_messages, [])
        self.assertEqual(self.x11.raised, [])

    def test_sends_net_active_window_client_message(self):
        self.assertTrue(self.backend.activate_window(self.target,
                                                     timeout=0.01))
        self.assertEqual(len(self.x11.client_messages), 1)
        msg = self.x11.client_messages[0]
        self.assertEqual(msg["window"], 0x200)
        self.assertEqual(msg["type"],
                         self.x11.atoms["_NET_ACTIVE_WINDOW"])
        self.assertEqual(msg["format"], 32)
        self.assertEqual(msg["l"][0], 2)          # source=pager/tool
        self.assertEqual(msg["mask"], autopaste.SUBSTRUCT_MASK)

    def test_focus_descendant_counts_as_focused(self):
        child = 0x201
        self.x11.add_child(0x200, child)
        self.x11.focus = child
        self.assertTrue(autopaste._is_focused(self.x11.dpy, 0x200))

    def test_falls_back_to_raise_and_setinputfocus(self):
        # 没有 _NET_ACTIVE_WINDOW：直接 raise + XSetInputFocus
        self.x11.atoms.pop("_NET_ACTIVE_WINDOW", None)
        self.assertTrue(self.backend.activate_window(self.target,
                                                     timeout=0.01))
        self.assertIn(0x200, self.x11.raised)
        self.assertEqual(self.x11.input_focus_sets[0][0], 0x200)
        self.assertEqual(self.x11.input_focus_sets[0][1], 2)

    def test_timeout_raises_focus_stealing_hint(self):
        self.x11.ignore_set_input_focus = True
        with self.assertRaises(autopaste.WindowUnavailable) as ctx:
            autopaste.activate_window_x11(
                self.x11.dpy, self.target, timeout=0.02, interval=0.005)
        msg = str(ctx.exception)
        self.assertIn("焦点抢占防护", msg)
        self.assertIn("--window id:", msg)

    def test_closed_window_exits_2(self):
        gone = autopaste.WindowInfo(0x2FF, "kitty", "kitty", "t")
        with self.assertRaises(autopaste.WindowUnavailable) as ctx:
            autopaste.activate_window_x11(self.x11.dpy, gone, timeout=0.01)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("已关闭", str(ctx.exception))

    def test_reused_xid_detected(self):
        reused = autopaste.WindowInfo(0x200, "firefox", "Navigator", "t")
        with self.assertRaises(autopaste.WindowUnavailable) as ctx:
            autopaste.activate_window_x11(self.x11.dpy, reused, timeout=0.01)
        self.assertIn("复用", str(ctx.exception))

    def test_error_handler_installed_and_restored(self):
        self.backend.activate_window(self.target, timeout=0.01)
        self.assertGreaterEqual(len(self.x11.error_handlers), 2)
        last = self.x11.error_handlers[-1]
        value = getattr(last, "value", last)
        self.assertIn(value, (0, None))


# ------------------------------------------------------------------ CLI 接线
class MainWindowTests(unittest.TestCase):
    def setUp(self):
        self.kitty = autopaste.WindowInfo(0x200, "kitty", "kitty", "~/src",
                                          100, 1)
        self.other = autopaste.WindowInfo(0x201, "firefox", "Navigator",
                                          "GitHub", 200, 1)

    def _run(self, argv, *, caps=None, windows=None, active=0x200,
             activate=True, focused=True, confirm=False, countdown=True,
             list_impl=None):
        caps = caps or {"name": "x11", "can_focus_info": True,
                        "can_paste_mode": False, "unicode": True,
                        "needs_remap": True, "combos": True,
                        "per_char_delay": True, "daemon": False,
                        "window_control": True}
        windows = [self.kitty, self.other] if windows is None else windows
        with temp_home(), \
                mock.patch.object(autopaste, "Typer") as typer_cls, \
                mock.patch.object(os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(autopaste, "_confirm",
                                  return_value=confirm), \
                mock.patch.object(autopaste, "countdown",
                                  return_value=countdown), \
                redirect_std() as (out, err):
            typer = typer_cls.return_value
            typer.capabilities.return_value = caps
            typer.list_windows.return_value = windows
            typer.list_windows.side_effect = list_impl
            typer.focused_window_id.return_value = active
            typer.activate_window.return_value = activate
            typer.is_window_focused.return_value = focused
            typer.is_window_focused.side_effect = None
            typer.focus_info.return_value = None
            typer.resolvable.return_value = []
            code = autopaste.main(argv)
            return code, out.getvalue(), err.getvalue(), typer

    def test_list_windows_tsv_output(self):
        code, out, _, typer = self._run(["--list-windows"])
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(len(line.split("\t")), 6)
        self.assertIn("0x00000200", out)
        self.assertIn("*", out)
        typer.activate_window.assert_not_called()
        typer.type_text.assert_not_called()

    def test_list_windows_json(self):
        code, out, _, _ = self._run(["--list-windows", "--json"])
        self.assertEqual(code, 0)
        import json
        rows = json.loads(out)
        self.assertEqual(rows[0]["id"], "0x00000200")
        self.assertTrue(rows[0]["active"])

    def test_list_windows_filtered_by_pattern(self):
        code, out, _, _ = self._run(["--list-windows", "--window",
                                     "title:github"])
        self.assertEqual(code, 0)
        self.assertNotIn("0x00000200", out)
        self.assertIn("0x00000201", out)

    def test_list_windows_on_wayland_exits_2(self):
        caps = {"name": "wtype", "can_focus_info": False,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": False,
                "window_control": False}
        code, _, err, _ = self._run(["--list-windows"], caps=caps)
        self.assertEqual(code, 2)
        self.assertIn("不支持窗口枚举", err)

    def test_window_option_requires_window_control(self):
        caps = {"name": "wtype", "can_focus_info": False,
                "can_paste_mode": False, "unicode": True,
                "needs_remap": False, "combos": True,
                "per_char_delay": True, "daemon": False,
                "window_control": False}
        code, _, err, _ = self._run(["--window", "kitty", "hi"], caps=caps)
        self.assertEqual(code, 2)
        self.assertIn("不支持窗口定向", err)

    def test_no_match_exits_2(self):
        code, _, err, typer = self._run(["--window", "nosuch", "hi"])
        self.assertEqual(code, 2)
        self.assertIn("没有匹配", err)
        typer.type_text.assert_not_called()

    def test_ambiguous_non_tty_exits_2(self):
        # active 不在候选里（active=0x999）且两个窗口都匹配 kitty/firefox 的任意子串
        code, _, err, typer = self._run(["--window", "i", "hi"], active=0x999)
        self.assertEqual(code, 2)
        self.assertIn("匹配到 2 个窗口", err)
        typer.type_text.assert_not_called()

    def test_active_match_is_used(self):
        code, _, _, typer = self._run(["--window", "i", "hi"], active=0x201,
                                      countdown=True)
        self.assertEqual(code, 0)
        self.assertEqual(typer.activate_window.call_args.args[0].xid, 0x201)

    def test_activate_false_exits_2(self):
        code, _, err, typer = self._run(["--window", "kitty", "hi"],
                                        activate=False)
        self.assertEqual(code, 2)
        self.assertIn("无法激活", err)
        typer.type_text.assert_not_called()

    def test_focus_lost_during_countdown_cancels(self):
        code, _, err, typer = self._run(["--window", "kitty", "hi"],
                                        focused=False)
        self.assertEqual(code, 3)
        self.assertIn("焦点已离开", err)
        typer.type_text.assert_not_called()

    def test_dry_run_with_window_does_not_activate(self):
        code, out, _, typer = self._run(["--dry-run", "--window", "kitty",
                                         "hi"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run 未执行", out)
        typer.activate_window.assert_not_called()
        typer.type_text.assert_not_called()

    def test_window_terminal_multiline_requires_confirm(self):
        code, _, _, typer = self._run(["--window", "kitty", "a\nb"],
                                      confirm=False)
        self.assertEqual(code, 3)
        typer.type_text.assert_not_called()
        typer.activate_window.assert_not_called()

    def test_window_terminal_multiline_with_yes_proceeds(self):
        code, _, _, typer = self._run(["-y", "--window", "kitty", "a\nb"],
                                      confirm=False)
        self.assertEqual(code, 0)
        typer.activate_window.assert_called_once()
        typer.type_text.assert_called_once_with("a\nb")


if __name__ == "__main__":
    unittest.main()
