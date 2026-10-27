# 参与开发

## 环境

- Linux + Python ≥ 3.8；只允许标准库（不引入第三方运行时依赖）
- 不需要 X11：测试用假后端替换 `autopaste.x11` / `autopaste.xtst`
- 本地命令：

```bash
python3 -m py_compile autopaste.py
env -u DISPLAY python3 -m unittest discover -s tests -v < /dev/null
python3 tools/gen_cli_docs.py          # 改了 build_parser()/capabilities() 后
python3 tools/gen_cli_docs.py --check  # CI 同款校验
```

`< /dev/null` 不能省：部分用例依赖真实 `sys.stdin.isatty()`，在交互终端里会挂住。

## 提交约定

- 中文提交信息，`type(scope): 摘要`，一个逻辑变更一个 commit；
- **不 push**，不给自己打 tag（发布由维护者统一执行）；
- 代码、注释、文档一律中文（`README.en.md` 除外）；
- 新增 flag 必须同时：改 `build_parser()`、跑生成器更新 `docs/cli.md`、补测试。

## 文档规则

- 一个事实只有一个 owner，详见 [`docs/README.md`](docs/README.md)；
- `docs/cli.md` 的 `AUTO:flags` 与 `docs/backends.md` 的 `AUTO:matrix` **禁止手改**；
- `tests/test_docs.py` 会扫描未实现的参数、坏链接、退出码与版本漂移。

## 安全

- 任何新特性不得放宽默认闸门（root/控制字符/FIFO/多行确认），只能显式 flag 打开；
- 失败路径必须保证修饰键释放与键位映射还原（`try/finally`）；
- 安全问题请走私密渠道，见 [`SECURITY.md`](SECURITY.md)。
