# STACK

- **语言/运行时**：Python ≥ 3.8（CI 覆盖 3.8/jammy、3.9、3.13 全量测试；check job 覆盖 3.9–3.13）
- **依赖**：**零第三方运行时依赖**。标准库 + `ctypes` 惰性 dlopen `libX11.so.6` / `libXtst.so.6`；
  Wayland 以固定 argv（不经 shell）调用外部 `wtype` / `ydotool`
- **平台**：Linux；X11 与 Wayland（wtype/ydotool）。无 X 库时 `--help`/`--version`/`--dry-run` 仍可用
- **安装形态**：单文件 `autopaste.py`（约 3780 行）+ `pyproject.toml`（PEP 621）；`install.sh`
  软链到 `~/.local/bin/autopaste`；console script 入口是 `autopaste:console_main`
- **测试**：标准库 `unittest`，`tests/` 下 17 个文件、347 条用例（2026-10-27），不需要 DISPLAY；
  用 `tests/support.py` 的假 X11/XTest 替换模块级 `autopaste.x11` / `autopaste.xtst`
- **CI**：`.github/workflows/ci.yml` 三个 job——`check`（py_compile、`--help`、文件存在性、
  `gen_cli_docs.py --check` + `test_docs.py`）、`tests`（3.8/3.9/3.13 全量 unittest）、
  `package`（`pip install .` + `autopaste --version`）
- **文档**：`README.md`（入口）、`docs/{README,usage,cli,backends,design}.md`、根目录
  `SECURITY.md` / `CHANGELOG.md` / `CONTRIBUTING.md`；参数表与能力矩阵由
  `tools/gen_cli_docs.py` 从源码生成
- **发布**：`dynamic = ["version"]` 从 `autopaste.__version__` 取版本（当前 1.1.0）；
  尚无 git tag，`v1.1.0` 由编排者在最后一个 commit 上打 annotated tag（implementer 不 push/不打 tag）
