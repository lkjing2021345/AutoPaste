# 文档索引

[← README](../README.md)

面向使用者与维护者的文档。根目录只留入口 `README.md`、威胁模型
`SECURITY.md` 与版本历史 `CHANGELOG.md`。

## 读者路线

| 你是 | 从这里开始 |
|---|---|
| 第一次用 | [README](../README.md) → [`usage.md`](usage.md) |
| 要找某个参数/退出码 | [`cli.md`](cli.md)（参数表由 `--help` 生成） |
| Wayland 没反应 / 选后端 | [`backends.md`](backends.md) |
| 想改代码 / 评审安全设计 | [`design.md`](design.md) → [SECURITY.md](../SECURITY.md) |
| 参与开发 | [CONTRIBUTING.md](../CONTRIBUTING.md) |

## 文件职责

| 文件 | 拥有（唯一事实源） |
|---|---|
| [`usage.md`](usage.md) | CLI/REPL 场景流程、输入来源、配置、故障排查 |
| [`cli.md`](cli.md) | 全部参数、键名、退出码、示例 |
| [`backends.md`](backends.md) | 能力矩阵、auto 规则、安装与权限 |
| [`design.md`](design.md) | 架构、数据流、Backend 契约、安全闸门清单 |
| [`../SECURITY.md`](../SECURITY.md) | 威胁模型、内建措施、防不了什么 |

## 维护规则

1. **一个事实只有一个 owner**：不属于自己的内容改成链接 + 一句上下文，禁止多份
   互相复述的默认值/退出码表。
2. **AUTO 区块禁止手改**：`cli.md` 的 `AUTO:flags` 与 `backends.md` 的 `AUTO:matrix`
   只由 `python3 tools/gen_cli_docs.py` 生成；CI 跑 `--check`。
3. **不写未实现的 flag**：`tests/test_docs.py` 扫描所有文档里以 `--` 开头的参数，
   不在 `build_parser()` 里的直接失败。新功能先落代码与 `--help`，再生成文档。
