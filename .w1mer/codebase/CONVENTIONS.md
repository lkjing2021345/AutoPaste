# CONVENTIONS

- **语言**：代码注释、文档、提交信息一律中文（`README.en.md` 除外）。
- **依赖红线**：不引入第三方包；新增能力优先用标准库 + ctypes。
- **安全默认**：默认拒绝 root、拒绝控制字符、拒绝 FIFO/设备、终端多行需确认；
  任何新特性不得放宽这些默认值，只能通过显式 flag 打开。
- **失败路径**：所有异常都要保证修饰键释放 + 键盘映射还原（`try/finally`）；
  键位映射仅 X11 后端存在，Wayland 后端保证修饰键释放与子进程返回。
- **错误处理**：用户输入类错误抛 `Abort` 子类，在 `main()` 统一转成中文提示 + 退出码；
  退出码唯一来源是 `Abort.code`：0 成功，1 内容/数值校验失败，2 用法/配置/环境/安全拒绝
  （含 argparse），3 用户取消/焦点复核失败，130 中断。
- **文档**：`docs/` 一个事实一个 owner；`AUTO:flags`/`AUTO:matrix` 只由
  `tools/gen_cli_docs.py` 生成；新 flag 必须跑生成器 + `tests/test_docs.py`。
- **提交**：一个任务一个 commit，`type(scope): 中文摘要`，正文列要点；不 push、
  implementer 不打 tag（编排者统一执行）。
- **测试**：不依赖真实 X11（假后端/依赖注入）；
  `env -u DISPLAY python3 -m unittest discover -s tests -v < /dev/null`。
