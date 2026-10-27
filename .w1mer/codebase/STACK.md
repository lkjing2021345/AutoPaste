# STACK

- **语言/运行时**：Python ≥ 3.8（当前开发环境 3.x，CI 覆盖 3.9–3.13）
- **依赖**：**零第三方依赖**。只用标准库 + `ctypes` 直接 dlopen `libX11.so.6` / `libXtst.so.6`
- **平台**：Linux + X11 会话（XTEST）。Wayland 原生不可用（规划中：wtype/ydotool 后端）
- **安装形态**：单文件脚本 `autopaste.py`；`install.sh` 软链到 `~/.local/bin/autopaste`
- **测试**：目前只有手工冒烟（`/tmp` 下的临时脚本），**尚无自动化测试**（任务 01 解决）
- **CI**：GitHub Actions（`.github/workflows/ci.yml`），py_compile + `--version/--help` + 文档完整性
- **发布**：`main` 分支直推；无版本 tag（1.0.0）
