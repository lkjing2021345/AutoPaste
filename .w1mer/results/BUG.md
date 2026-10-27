# Bug 修复验证（BUG）

## 03.1 REPL 定向窗口倒计时后不复核焦点（2026-10-27 / c96ee1a）

- 修复：`repl()` 的 `focus_ok()` 在 `countdown()` 之后、`type_text/press`
  之前复核 `is_window_focused(target.xid)`；失败仅提示并跳过本次注入。
- 证据（`tests/test_repl.py`）：`focused=False` 时提交 `hi`，`typed == []`
  且 stderr 含"焦点已离开"；`focused=True` 时正常注入一次；无定向目标时
  不做多余复核。
- 结论：significant（消除"盲打当前窗口"的 Major 风险）。

## 03.2 countdown 把 stdin EOF 当按键取消（2026-10-27 / 214d6e8）

- 修复：`select` 可读后 `os.read` 返回 `b""` 不再视为取消，改为
  `cancelable = False` 并让倒计时走完；真正的按键仍立即取消。
- 证据（`tests/test_repl.py`）：已关闭写端的 pipe + `countdown(0.15)` →
  返回 True 且输出无"已取消"；写入 `x` 的 pipe + `countdown(5.0)` →
  返回 False 且输出"已取消"（立即返回，未等 5s）。
- 结论：significant（`cat note.txt | autopaste -s 3 -` 不再误退 130）。
