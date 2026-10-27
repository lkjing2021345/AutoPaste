# 功能实施验证（FEATURE）

记录已完成功能的实测结果。字段：roadmap id、commit、日期、实测数据、结论。

## 04.1 REPL 历史（2026-10-27 / 894ab14）

- 新增 21 条历史用例（`tests/test_history.py`）+ REPL 接线 2 条。
- 持久化：`History(path)` 写入后新实例可读回 `["first","second"]`；
  文件 `stat` = 0600、目录 = 0700。
- 上限：单条 8193 字节被拒；200 行接受、201 行拒绝；文件 1.2 MiB 启动时
  截读尾部并压缩，压缩后 ≤1 MiB 且最后一条为第 400 条。
- fail-closed：符号链接文件/目录、属主不匹配（注入 `euid=0`）→
  `disabled_reason` 非空、条目为空、文件字节数不变；相对 `XDG_STATE_HOME`
  → `default_history_path()` 返回 None（仅内存）。
- 结论：significant（安全边界全部为禁用而非降级写入）。

## 04.2 `:p` / `:w`（2026-10-27 / 151fc48）

- 15 条用例：`:p` 输出与 `--dry-run` 同一 `_esc_preview`（`a\tb` → `a\tb` 转义），
  注入调用为 0；`:w` 内容 UTF-8、模式 0600、覆盖普通文件成功。
- `:w` 拒绝符号链接（目标字节不变）、目录、FIFO；`os.replace` 失败时原文件
  字节不变且无 `.tmp` 残留。
- 窗口指令改名为 `:win`，`:w` 与保存互不抢占（同一 REPL 会话内验证）。
- 结论：significant。

## 04.3 日志与 `--quiet/--verbose`（2026-10-27 / 4531c6d）

- 6 条用例：`--quiet` 下 banner/bye 不出现，但 `:w` 错误、多行风险、确认
  取消仍输出；quiet 倒计时恰好一行（`目标 …｜Ns 后可取消`，无 `\r`）且按键
  仍能取消；`--verbose --dry-run secret-token-123` 的 stderr 不含 payload。
- 结论：significant（安全可视化未因静默丢失）。

## 04.4 配置与退出码（2026-10-27 / 739985f）

- 配置：合法扁平 TOML 解析 5 个键（含 REPL 实际应用 `auto_enter=true` 与
  `sleep=1.5`，`:t` 显示"配置"来源）；`allow_root`/`allow_control`/`yes`/
  `no_restore_clipboard` → 退出 2；`allow-root` → "非法键名" 退出 2；
  未知键警告后忽略；`nan/inf/数组/日期`、重复键、非 UTF-8 均报错并带
  `路径:行号`。
- 文件安全：0666/0642、符号链接、属主不匹配 → 退出 2；默认路径缺失 →
  使用默认值；显式 `--config` 缺失 → 2。
- 优先级：`-d 0.008` 覆盖配置 `0.02`（verbose 显示"命令行"）；无 CLI 时
  使用配置并显示"配置"；`-q -v` 同给 → argparse 退出 2。
- 退出码矩阵：0（dry-run）、1（`--delay 99` / 文件读失败）、2（root /
  缺后端 / 配置 / `--repl` 非 tty）、3（多行确认取消）、130（倒计时取消）
  全部命中；`BackendUnavailable.code == 2` 未被父类 `BackendError(1)` 吞掉。
- 收口：`--repl </dev/null` 退出 2 无 traceback；`-f` 的 `PermissionError`
  → 退出 1 且提示"无法读取文件"；`console_main` 的 `BrokenPipeError` → 0。
- 结论：significant。

## 04 整体

- 测试规模：241（基线）→ **330**，全绿（`env -u DISPLAY python3 -m unittest
  discover -s tests`，3.3s，无 tty/无 X 依赖）。
- 结论：significant。
