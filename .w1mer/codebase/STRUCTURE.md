# STRUCTURE

单文件程序 `autopaste.py`（约 3780 行，3783 行），自上而下 13 段：

```
安全/窗口/剪贴板常量            (86-152)
  → X11 ctypes 绑定 + 惰性加载   (153-329)
  → 异常层级 + Log              (330-417)
  → 窗口查询 / 枚举 / 匹配 / 激活 (418-985)
  → 输入校验                     (986-1011)
  → 后端抽象 Backend            (1012-1339)
      X11Backend / WtypeBackend / YdotoolBackend
  → 外部命令接缝 + 后端选择      (1340-1725)
  → 剪贴板（xclip/xsel/wl-copy） (1726-1946)
  → 会话外观 Typer / 多行风险    (1947-2060)
  → History（JSONL，fail-closed） (2061-2332)
  → LineEditor（cbreak 行编辑）  (2333-2595)
  → 配置文件解析 + resolve      (2596-2826)
  → 交互界面 countdown/REPL      (2827-3226)
  → argparse CLI + main/console_main (3227-3783)
```

数据流（CLI）：
`argv/--file/stdin` → `validate_payload()` → `load_config()`+`resolve_settings()` →
`choose_backend()`/`Typer` → `resolve_window()` →（`--dry-run` 在此结束）→
`multiline_risk()`/`_confirm()` → `countdown()` → 焦点复核 → `type_text()`/`press()`
或 `paste_text()`/`send_text()`。

数据流（REPL）：
`LineEditor.read()` → 命令分发（`:s :d :nl :k :f :r :t :p :w :win :h :q`）/文本 →
`activate()` → `countdown()` → `focus_ok()` → `type_text()`/`press()`。

关键约束：
- `Typer` 是唯一会话对象（选后端 + delay + close），`close()` 幂等；X11 Display、
  临时键位映射都在 `X11Backend` 内，`atexit` + `SIGINT/SIGTERM/SIGHUP` 兜底。
- `Backend` 契约：`capabilities()/type_text()/type_char()/press()/focus_info()/
  list_windows()/activate_window()/focused_window_id()/is_window_focused()/close()`。
- `abort.code` 是退出码唯一来源（0/1/2/3/130）；`main()` 只 `except Abort`。
- `Log` 是唯一诊断出口：stdout 只放用户请求的数据，stderr 放诊断/警告/倒计时。
- 所有注入路径必须经过 `validate_payload()` + 焦点门；没有第二个旁路。
- 版本号唯一来源 `__version__`；文档 AUTO 区块只由 `tools/gen_cli_docs.py` 生成。
