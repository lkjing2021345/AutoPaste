---
id: "02"
title: 后端抽象 + Wayland 支持（wtype/ydotool）— 实施计划
state: active
---

# 02 后端抽象 + Wayland 支持 — 实施计划（explorer, batch base 8b22d3e）

> 读者：任务 02 的 implementer。所有行号基于 commit `8b22d3e` 的 `autopaste.py`（951 行）。
> 目标一句话：**把"产生键盘事件"从 `Typer` 里剥出来变成可插拔 `Backend`，X11 路径逐字节保持行为不变，新增 wtype / ydotool 两条 Wayland 路径。**

---

## 0. 结论摘要（先看这 6 条）

1. `Typer` 保留为**会话外观（façade）**，只做：后端构造、`delay` 转发、`close` 幂等/atexit、`resolvable` 转发、`focus_info` 转发。真正的 XTEST 代码整体搬进 `X11Backend`（**代码原样搬，不重写一个字符**），这是"X11 行为零变化"的唯一可靠保证。
2. 新代码只增不改：`Backend` 抽象基类 + `WtypeBackend` + `YdotoolBackend`；`Typer.__init__` 变成"选择后端"，其余 12 处 `typer.xxx()` 调用点不动。
3. 必须同时修掉一个**阻塞 Wayland 的结构问题**：`x11 = ctypes.CDLL(...)` 在模块级执行（70–72 行），纯 Wayland 机器上没装 `libX11` 时连 `--help/--version` 都会崩。开箱改成 lazy load，由 `X11Backend.__init__` 触发。
4. `main()` 里"没有 DISPLAY 就退出 2"（829–832 行）必须删掉，改为**后端解析**；解析失败才退 2。判断会话类型**不能只看 DISPLAY**（Wayland 下 XWayland 也有 DISPLAY，但 XTEST 打不到原生窗口），必须以 `XDG_SESSION_TYPE` 为主。
5. auto 顺序：**x11（真 X11 会话）→ wtype → ydotool**；但 `XDG_CURRENT_DESKTOP` 含 GNOME 时反转为 **ydotool → wtype**（mutter 不实现 virtual-keyboard 协议）。运行期再兜一层：auto 模式下 wtype 因协议失败 → 自动降级 ydotool 并打印提示。
6. 最大风险是 **argv 长度上限**（Linux 单参数 128 KiB，`E2BIG`）——1 MiB payload 绝对不能一次塞进 `wtype` 的 argv，必须分片；第二风险是 **ydotool 不支持 Unicode**，GNOME + 中文场景没有可用后端，必须**显式报错**而不是静默打错字。

---

## 1. 现状事实（改造前的准确坐标系）

| 位置 | 内容 | 处置 |
|---|---|---|
| 40–52 | imports | `+ shutil, + subprocess`（`subprocess` 是安全文档承诺的反面，见 §7） |
| 70–72 | `x11 = ctypes.CDLL(...)` / `xtst = ctypes.CDLL(...)` | **移到 `X11Backend` 的 `_load()`（lazy）** |
| 74 | `P = ctypes.POINTER` | 不动 |
| 77–115 | `XClassHint`、`argtypes/restype` 声明 | 不动（全局一次性声明，与 dlopen 解耦，可以在 import 期继续跑） |
| 117–134 | `NoSymbol/SPECIAL/MODIFIERS/KEYSYM_SHIFT` | 不动（是共享词表，Wayland 后端复用） |
| 141–144 | `_char_keysym()` | 不动（纯函数，X11 专用） |
| 153–178 | `focused_window(dpy)` | **签名不动**，但内部改用模块级 lazy `x11`；只有 `X11Backend.focus_info()` 调它 |
| 181–199 | `describe_window` / `is_terminal` | 不动（纯函数，跨后端复用） |
| 229–402 | `class Typer`（XTEST 实现） | **改名 `class X11Backend(Backend)`，方法体原样保留**，补 `name/focus_info/capabilities` |
| 565–591 | `countdown(seconds, typer)`，568 行 `focused_window(typer.dpy)` | 改 `target_label(typer)` |
| 594–600 | `warn_terminal_target`，595 行同上 | 改 `multiline_risk(typer, text)`（见 §5） |
| 645 / 672 | REPL prompt 与 `:t` 里的 `focused_window(typer.dpy)` | 改 `target_label(typer)` |
| 743–785 | `build_parser()` | `+ --backend`、`+ --list-backends`；description 去掉"X11" |
| 829–832 | `if not os.environ.get("DISPLAY"): ... return 2` | **删除**，换成后端解析 |
| 849 / 877 | `Typer(delay=...)` | `Typer(backend=args.backend, delay=...)` |
| 881–896 | dry-run（`focused_window(typer.dpy)` + `typer.resolvable`） | 用 `focus_info()/capabilities()`；无重映射需求的后端跳过 `resolvable` |
| 899–909 | 终端多行闸门（`focused_window(typer.dpy)`） | 改用 `multiline_risk()` |
| 946–951 | `__main__` | 不动（`atexit` 仍由 `Typer` 注册） |

`.dpy` 的全部引用只有 6 处（568/595/645/672/881/901），全部在 `Typer` 外部——这说明**外观层方案可行且改动面很小**。

---

## 2. 接口设计

### 2.1 `Backend` 契约

方法刻意压到 5 个 + 2 个可选，避免抽象层变成"第二个程序"：

```python
class Backend:
    """键盘注入后端契约。所有方法不得依赖具体会话环境之外的全局状态。"""
    name = "base"
    #: 是否会计时/重映射等由 delay 控制；subprocess 后端把 delay 交给外部工具

    def type_text(self, text: str) -> None: ...
    def type_char(self, ch: str) -> None: ...      # 默认实现: type_text(ch)
    def press(self, combo: str) -> None: ...       # "ctrl+alt+t" / "enter" / "a"
    def focus_info(self) -> dict | None:           # {"title","name","class"} 或 None
        return None
    def capabilities(self) -> dict:                # 见 2.3
        return {"name": self.name, "focus_info": False, "unicode": False,
                "needs_remap": False, "combos": False,
                "per_char_delay": False, "daemon": False}
    def resolvable(self, text: str) -> list[str]:  # dry-run 用；不需要重映射的后端返回 []
        return []
    def close(self) -> None: ...
```

约束（写进 docstring，reviewer 会查）：

- `close()` **必须幂等**，且不抛异常。
- `type_*` / `press` 失败抛 `BackendError(Abort)`（§2.4 退出码）。
- `focus_info()` **不得**为拿信息而阻塞 > 100ms；拿不到就返回 `None`，不要抛。

### 2.2 `Typer` 与 `Backend` 的分工

```
Typer            = 会话层：选哪个后端、delay 策略、atexit、能力查询门面、REPL 改 :d 时的转发
Backend (X11/Wtype/Ydotool) = 设备层：真正让事件出现在屏幕上、焦点查询、资源释放
```

```python
class Typer:
    """对外统一入口；内部委托给 Backend。保留旧方法名，调用点零改动。"""
    def __init__(self, backend: str = "auto", delay: float = 0.008,
                 remap_wait: float = 0.02):
        self.backend = make_backend(backend, delay=delay, remap_wait=remap_wait)
        self._delay = delay
        self._closed = False
        atexit.register(self.close)

    # delay 是唯一有状态的参数：REPL 的 :d 会改它（原 677 行 `typer.delay = cfg.delay`）
    @property
    def delay(self) -> float:
        return self._delay
    @delay.setter
    def delay(self, v: float) -> None:
        self._delay = v
        self.backend.delay = v          # 子进程后端在下一次调用时把它翻成 -d ms

    def type_text(self, t): return self.backend.type_text(t)
    def type_char(self, c): return self.backend.type_char(c)
    def press(self, c):     return self.backend.press(c)
    def resolvable(self, t):return self.backend.resolvable(t)
    def focus_info(self):   return self.backend.focus_info()
    def capabilities(self): return self.backend.capabilities()
    def close(self):
        if self._closed: return
        self._closed = True
        try: self.backend.close()
        except Exception: pass
```

要点：

- `Typer` 不再有 `.dpy`。6 个调用点改成 `typer.focus_info()` / `target_label(typer)`。**不要**为了省改动留一个 `dpy` 属性转发 `getattr(self.backend,"dpy",None)`——那会在 Wayland 下把 `None` 传给 `focused_window()` 变成段错误级别的隐患。
- `atexit` 注册从 `Typer`（新外观）做，`X11Backend` 内部**再注册一次也没关系**（`close` 幂等），但建议只在 `X11Backend.__init__` 保留原注册、`Typer` 也注册；两者都幂等，重复注册可接受。**最简做法：只保留 `Typer` 的注册，删掉 242 行**（因为 `X11Backend` 只会经由 `Typer` 构造；但测试可能直接构造 `X11Backend`，所以更稳的是两处都留）。

### 2.3 `capabilities()` 字典（自描述，UI 只读它）

| key | x11 | wtype | ydotool | 用途 |
|---|---|---|---|---|
| `name` | `"x11"` | `"wtype"` | `"ydotool"` | 提示/日志/dry-run |
| `focus_info` | True | False | False | 决定 UI 能否显示目标窗口、多行闸门策略 |
| `unicode` | True | True | **False**（CJK/emoji 不可靠） | 提前拦截，避免静默错字 |
| `needs_remap` | True | False | False | dry-run 是否跑 `resolvable()` |
| `combos` | True | True | True（需 keycode 表） | `-k ctrl+v` 是否支持 |
| `per_char_delay` | True | True（`-d` ms） | True（`--key-delay` ms） | `:d` 是否生效 |
| `daemon` | False | False | True | 缺 socket 时给 daemon 提示 |

### 2.4 异常与退出码

```python
class BackendError(Abort):        # 后端运行期失败（工具还没起来）
    """..."""
class BackendUnavailable(BackendError):   # 环境缺失（没装/没会话/权限/socket）
    pass
```

`main()` 末尾的 `except Abort as e: return 1` 之前加：

```python
except BackendUnavailable as e:      # 环境/安全拒绝 → 2（与现有 root/DISPLAY 拒绝一致）
    sys.stderr.write(f"{e}\n"); return 2
except BackendError as e:            # 工具跑挂了 → 1
    sys.stderr.write(f"已拦截: {e}\n"); return 1
```

（`BackendUnavailable` 是 `Abort` 子类，顺序必须在 `Abort` 之前。）

---

## 3. 后端选择（auto 语义）

### 3.1 会话探测

```python
def detect_session(env=os.environ) -> str:      # "x11" | "wayland" | "unknown"
    st = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if st == "wayland":  return "wayland"       # 即便有 DISPLAY（XWayland）也算 wayland
    if st == "x11":      return "x11"
    if env.get("WAYLAND_DISPLAY"): return "wayland"
    if env.get("DISPLAY"):         return "x11"   # 旧行为：只看 DISPLAY
    return "unknown"
```

**为什么 `XDG_SESSION_TYPE=wayland` 优先于 DISPLAY**：XWayland 环境下 `XOpenDisplay()` 会成功，XTEST 也"能发"，但事件只进 X 客户端，原生 Wayland 窗口（默认浏览器/终端）收不到。宁可报"Wayland 无后端"也不要假装成功。

### 3.2 选择顺序与理由

```python
def choose_backend(requested="auto", env=os.environ, which=shutil.which):
    if requested != "auto":
        return requested                       # 显式指定：不做任何偷偷回退
    sess = detect_session(env)
    if sess == "x11":
        return "x11"                           # 真 X11：保持现状，零风险
    desk = (env.get("XDG_CURRENT_DESKTOP") or "").upper()
    prefer = ("ydotool", "wtype") if "GNOME" in desk else ("wtype", "ydotool")
    for name in prefer:
        if which(name):
            return name
    raise BackendUnavailable(_missing_hint(sess, env))
```

- **真 X11 会话 → 永远 x11**：这是"X11 行为零变化"的字面含义，也让现有用户 100% 无感。
- **Wayland 默认 wtype**：无守护进程、无 `/dev/uinput` 权限、不需要 root 级配置，单次 exec，权限面最小（符合 CONVENTIONS "安全默认"）。缺点：只对实现了 `zwp_virtual_keyboard_manager_v1` 的合成器有效 → wlroots 系（sway/Hyprland/river/Wayfire）、KDE Plasma 可用；**GNOME/mutter 不支持**。
- **GNOME 反转为 ydotool 优先**：靠 `XDG_CURRENT_DESKTOP`（含 `GNOME`/`ubuntu:GNOME`）。这是启发式，命中即避免"必然失败的第一次尝试"。
- **运行期回退**：仅在 `auto` 且当前是 wtype 时，捕获 `BackendError` 且 `stderr` 命中协议特征（`virtual keyboard` / `protocol` / `not supported` / 退出码 1 且无输出）→ 若有 ydotool 则打印一行 `wtype 不可用（<合成器> 不支持 virtual-keyboard），改用 ydotool` 并重建后端。**显式 `--backend wtype` 绝不回退**（用户要的就是可预测）。

### 3.3 缺依赖提示（`_missing_hint`）

必须给"可直接复制"的命令，且按发行版分行（用 `shutil.which("apt")`/`"pacman"`/`"dnf"` 粗分流或全部列出）：

```
Wayland 会话（<GNOME|KDE|sway…>）需要 wtype 或 ydotool 之一，当前都没找到。

  Debian/Ubuntu : sudo apt install wtype        （ydotool 同样在 universe）
  Fedora        : sudo dnf install wtype ydotool
  Arch          : sudo pacman -S wtype ydotool
  Alpine        : sudo apk add wtype

推荐先装 wtype（无需守护进程、无需额外权限）。
若用 GNOME（mutter 不支持 virtual-keyboard 协议）请装 ydotool，然后：
  systemctl --user enable --now ydotoold
  sudo usermod -aG input $USER    # 或写 udev 规则给 /dev/uinput 0660
（本程序拒绝以 root 运行，权限必须授予你自己的用户。）
```

---

## 4. 两个 Wayland 后端的实现细节

### 4.1 `WtypeBackend`（约 90 行）

前置：`wtype` 二进制；合成器实现 `virtual-keyboard-unstable-v1`（+ `wl_seat`）。

```python
class WtypeBackend(Backend):
    name = "wtype"
    MAX_ARG = 32 * 1024          # 远低于 Linux 单参数 128 KiB 上限（UTF-8 字节计）

    def __init__(self, delay=0.008, runner=None, which=shutil.which, env=None):
        self.delay = delay
        self._run = runner or subprocess.run
        self._env = env if env is not None else os.environ
        if not which("wtype"):
            raise BackendUnavailable("未找到 wtype。\n" + _missing_hint("wayland", self._env))
```

- 整串一次调用（**不要逐字符 fork**，1 MiB 会把机器拖死）：`wtype -d <ms> -- <chunk>`。
- `-d` 是**毫秒整数**：`int(round(min(delay, 5.0) * 1000))`，`delay=0.008 → 8`。`delay=0` 时**不要**传 `-d 0`（部分版本等价于不给，行为不同）；统一传显式值即可，但需 `--d 0` 的兼容性实测（见 §8 验证项）。
- 组合键：`ctrl+shift+v` → `-M ctrl -M shift -k v -m shift -m ctrl`（释放顺序反过来，符合 `press()` 现有的 `reversed(pressed)` 语义）。
- 特殊键名走 `SPECIAL` 表的 keysym 名（`Return/Tab/Escape/BackSpace/Delete/Prior/Next/Up…`），`-k` 接受 xkb keysym 名。
- **分片**：按 UTF-8 字节累加到 ≤ `MAX_ARG`，且不在多字节字符中间切断（按字符累加，判断 `len(ch.encode())`）；每片之间 `time.sleep(delay)` 可选（`-d` 已覆盖字符间隔，不需要额外 sleep）。
- 以 `-` 开头的文本：**必须**用 `--` 分隔符（`wtype -d 8 -- "-rf"`），避免被当成选项。
- 退出码非 0 → 抓 `stderr` 前 200 字符抛 `BackendError`；`FileNotFoundError` → `BackendUnavailable`。
- 进程管理：用 `subprocess.run(..., stdout=DEVNULL, stderr=PIPE, text=True)`；若要支持中断时"停手"，用 `Popen` + `wait` + `finally: terminate()`（**建议直接用 `run`，Ctrl-C 会同时打到前台进程组里的 wtype**；但 SIGTERM 只打父进程时会留下子进程 → 用 `Popen` 更稳，见 §6 风险 6）。

### 4.2 `YdotoolBackend`（约 180 行，其中 ~70 行是键码表）

前置三件套：`ydotool` 二进制 + `ydotoold` 在跑 + 当前用户的 socket 可写 + 用户在 `input` 组（`/dev/uinput` 0660）。

- 文本：`ydotool type --key-delay <ms> -- <chunk>`（v1.0+；同样 `--` 防选项注入、同样分片）。
- 组合键：`ydotool key <code>:1 <code>:1 <code>:0 …` ← 需要**evdev `KEY_*` 码表**：`ctrl 29 / shift 42 / alt 56 / super 125 / enter 28 / tab 15 / esc 1 / backspace 14 / delete 111 / home 102 / end 107 / up 103 / down 108 / left 105 / right 106 / space 57 / a-z 30.. / 1-9 2..10 / 0 11 / F1..F12 59..68,87,88`。表里没有的键 → `Abort("ydotool 后端不支持按键 X，请改用 --backend wtype 或 x11")`，**不要**猜码。
- socket 探测（先探测再报错，报错要准）：
  ```python
  cand = [env.get("YDOTOOL_SOCKET"),
          os.path.join(env.get("XDG_RUNTIME_DIR", ""), ".ydotool_socket"),
          "/tmp/.ydotool_socket"]     # v1.0.x 默认是 /tmp；新版本用 XDG_RUNTIME_DIR
  ```
  都不存在 → `BackendUnavailable("ydotoold 未运行（找不到 socket）。先 systemctl --user enable --now ydotoold …")`。
- 权限错误特征（stderr 含 `uinput` / `Permission denied` / `No such file`）→ 提示 `/dev/uinput` 0660 + `input` 组，而不是让用户 `sudo`。
- **Unicode 硬限制**：`capabilities()["unicode"]=False`；`type_text` 里对 `any(ord(c) > 0x7E for c in text)` 直接抛
  `Abort("ydotool 无法可靠输入非 ASCII 字符（当前文本含 '中'）。GNOME Wayland + 中文请用 X11 会话或等 --paste-mode。")`
  ——**这是安全决策，不是功能缺失**：静默丢字会让用户把半截命令敲进终端。

### 4.3 两者对比速查

| | wtype | ydotool |
|---|---|---|
| 传输 | Wayland 协议 `zwp_virtual_keyboard` | Unix socket → `ydotoold` → `/dev/uinput` |
| 需要守护进程 | 否 | 是（`ydotoold`） |
| 权限 | 无 | `/dev/uinput` + socket + `input` 组 |
| 合成器支持 | wlroots、KWin；**mutter 不行** | 内核层，全部支持 |
| Unicode/CJK | 支持 | 不支持 |
| 布局依赖 | 自带 keymap，基本无关 | 依赖自身 qwerty 表 |
| 典型失败 | `failed to create virtual keyboard` | `failed to connect socket` / `permission denied` |

---

## 5. 降级与安全闸门复用

**闸门本身全部保留、不改判据**（CONVENTIONS：新特性不得放宽默认值）：

| 现有闸门 | Wayland 下怎么办 |
|---|---|
| 拒绝 root（821–827） | 不动。注意 ydotool 权限必须由 udev/组解决，不能靠 sudo 绕过 |
| `validate_payload`（203） | 不动，对所有后端一视同仁（控制字符拦截在协议层之前） |
| 1 MiB 上限 | 不动；由后端分片落实 |
| `check_regular_file` | 不动 |
| 倒计时目标可视化（565） | 见下：`target_label()` 降级文案 |
| 终端多行二次确认（899–909 / 594） | 见下：**Wayland 改为"所有多行都确认"**（更严，不放宽） |
| `--dry-run` | 显示后端名 + "无法读取焦点"，并跳过 `resolvable()` |

### 5.1 目标可视化降级

```python
def target_label(typer: Typer) -> str:
    caps = typer.capabilities()
    if not caps.get("focus_info"):
        return f"Wayland 会话（{caps['name']}：看不到焦点窗口，请自己确认）"
    return describe_window(typer.focus_info())
```

倒计时期间这句就显示在 `\r  目标 …` 那行——**语义不变（仍是"你即将敲到哪里"的唯一提示），只是诚实地说"不知道"**。绝不能用"未知窗口"糊过去，那和"X11 读失败"无法区分。

### 5.2 多行闸门（关键安全决策）

```python
def multiline_risk(typer: Typer, text: str) -> str | None:
    body = text.rstrip("\n")
    if "\n" not in body:
        return None
    caps = typer.capabilities()
    if not caps.get("focus_info"):
        return ("无法读取焦点窗口（Wayland）：多行文本若打进终端会被逐行执行。")
    info = typer.focus_info()
    if is_terminal(info):
        n = len([l for l in body.splitlines() if l.strip()])
        return f"目标是终端 {describe_window(info)}：多行文本会被逐行执行（共 {n} 行）。"
    return None
```

调用点：

- **CLI（899–909）**：`msg = multiline_risk(...)`；非 `None` 且 `not args.yes` → `_confirm("继续？")`，失败 `return 3`。非 tty 时 `_confirm` 返回 False → 天然拒绝（与现状一致）。**这条在 Wayland 上从"仅终端"扩大到"任何多行"，是收紧，不是放宽。**
- **REPL（594/716）**：保持现状语义——只打印警告不阻塞（REPL 每次提交本来就有可取消倒计时）。把 `warn_terminal_target` 改名为 `warn_multiline_target(typer, text)`，内部用 `multiline_risk`，打印一行 `⚠ …`。

理由：失去 `is_terminal()` 就等于失去"逐行执行"的最后一道自动兜底。代价只是 Wayland 下 CLI 敲多行要按一次 `y`（可加 `-y`），换来的是一致的风险姿态。

### 5.3 dry-run 输出（881–896）

```
后端      : wtype（Wayland，焦点不可读）
目标窗口  : Wayland 会话（wtype：看不到焦点窗口，请自己确认）
字符数    : 1234
  [1] 文本 17 行: hello\nworld…
（wtype 自带 keymap，无需字符重映射；已跳过 resolvable 检查）
dry-run：未发送任何按键。
```

`resolvable()` 只在 `caps["needs_remap"]` 为真时调用，否则一句说明替代（避免"无法映射的字符"这种 X11 专属结论误导 Wayland 用户）。

---

## 6. 测试策略（CI 无 X11 / 无 Wayland）

原则：**后端全部依赖注入**——`runner`（假 `subprocess.run` 记录 argv）、`which`（假 PATH 查表）、`env`（假环境字典）。import 侧把 `_load()` 之后能 patch 到的东西做成模块函数，方便 `unittest.mock.patch`。

```python
class FakeRunner:
    def __init__(self, rc=0, stderr=""):
        self.calls = []          # [argv, ...]
        self.kwargs = []
    def __call__(self, argv, **kw):
        self.calls.append(list(argv)); self.kwargs.append(kw)
        return subprocess.CompletedProcess(argv, self.rc, "", self.stderr)
```

文件名：若任务 01 已落地 `tests/`，新建 `tests/test_backends.py`（后端/选择/探测）与 `tests/test_safety.py`（闸门），不要塞进现有文件；若 01 未落地，仍按此路径创建，冲突最小。

必测用例（**名字即可直接照抄**）：

选择/探测
1. `test_detect_session_x11_from_xdg_session_type`
2. `test_detect_session_wayland_wins_over_display`（`XDG_SESSION_TYPE=wayland` + `DISPLAY=:0` → wayland）
3. `test_detect_session_falls_back_to_wayland_display`
4. `test_detect_session_falls_back_to_display`
5. `test_auto_on_x11_always_chooses_x11`
6. `test_auto_wayland_prefers_wtype_on_kde`（`XDG_CURRENT_DESKTOP=KDE`）
7. `test_auto_wayland_prefers_ydotool_on_gnome`
8. `test_auto_without_tools_raises_unavailable_with_install_hint`（断言消息含 `apt install wtype` 与 `pacman -S wtype`）
9. `test_explicit_backend_not_installed_returns_exit_2`
10. `test_explicit_wtype_never_falls_back_to_ydotool`

wtype argv
11. `test_wtype_type_text_argv`（`["wtype","-d","8","--","hello"]`）
12. `test_wtype_delay_converted_to_ms_and_clamped`（`delay=6.0` 被 `_num` 挡住前，后端仍 `min(...,5000)`）
13. `test_wtype_chunks_long_text_below_arg_limit`（1 MiB 输入 → 每片 ≤ 32 KiB，拼接等于原文，字符不被切半）
14. `test_wtype_text_starting_with_dash_uses_separator`
15. `test_wtype_combo_argv_ctrl_shift_v`（`-M ctrl -M shift -k v -m shift -m ctrl`）
16. `test_wtype_special_key_maps_to_keysym_name`（`enter → -k Return`）
17. `test_wtype_nonzero_exit_raises_backend_error_with_stderr`
18. `test_wtype_missing_binary_raises_unavailable`
19. `test_wtype_protocol_failure_falls_back_to_ydotool_in_auto_only`

ydotool argv
20. `test_ydotool_type_argv`（`["ydotool","type","--key-delay","8","--","hello"]`）
21. `test_ydotool_socket_from_env_var`
22. `test_ydotool_socket_probed_from_xdg_runtime_dir_and_tmp`
23. `test_ydotool_missing_socket_message_mentions_ydotoold`
24. `test_ydotool_rejects_non_ascii_text`（`"中文"` → `Abort`，消息含 `ydotool` 与 `非 ASCII`）
25. `test_ydotool_combo_uses_evdev_codes`（`ctrl+v` → `29:1 47:1 47:0 29:0`）
26. `test_ydotool_unknown_key_aborts_instead_of_guessing`
27. `test_ydotool_permission_error_hint_mentions_uinput`

X11 回归（**最重要的一组**，用假 `ctypes` 记录 `XTestFakeKeyEvent` 调用序列）
28. `test_x11_tap_sequence_unchanged`（`a` → (kc,1),(kc,0) 顺序与原来一致）
29. `test_x11_shift_wrapped_for_uppercase`
30. `test_x11_unicode_remaps_spare_keycode_then_restores`（`_remap`→`_tap`→`_restore` 顺序 + 最终 keymap 清零）
31. `test_x11_combo_releases_modifiers_on_exception`（`finally` 反向释放）
32. `test_x11_close_is_idempotent`
33. `test_x11_focus_info_delegates_to_focused_window`

外观层/集成
34. `test_typer_delay_setter_forwards_to_backend`（REPL `:d`）
35. `test_typer_close_idempotent_and_registered_once`（patch atexit）
36. `test_countdown_shows_wayland_target_label`
37. `test_dry_run_on_wayland_prints_backend_and_skips_resolvable`
38. `test_wayland_multiline_requires_confirm`（`_confirm` patch 成 False → 退出码 3）
39. `test_wayland_multiline_with_yes_flag_proceeds`
40. `test_help_works_without_libx11`（patch `ctypes.util.find_library` 返回 None 且 `CDLL` 抛 `OSError`；断言 `build_parser().format_help()` 仍成功 → 证明 lazy load）
41. `test_backend_capabilities_table`（三后端字段与 §2.3 一致，防止 UI 契约漂移）

CI 补充（可选，低成本）：`ubuntu-latest` 上 `apt-get install -y wtype` 后跑 `wtype -h` 只能验证"二进制存在"，**无法验证协议**——所以 42 号用例不做，实机验证放 §8。

---

## 7. 需要同步修改的文档（同一 commit）

- `SECURITY.md` §1 "不执行 shell：没有 `os.system`/`subprocess`/`eval`" —— **这句会变成假话**，必须改成：
  "wayland 后端会 exec 外部工具（`wtype`/`ydotool`），参数以 argv 数组直传、**从不经过 shell**，不存在注入；X11 后端仍不 exec 任何进程。"
  同时 §2 表格新增三行：后端选择可预测性（`--backend` 显式覆盖 / auto 不静默跨会话）、Wayland 多行闸门收紧、ydotool 非 ASCII 拦截。
- `README.md` 34 行 + 109 行 + 安装段：平台矩阵（X11 / wlroots / KWin / GNOME+ydotool）+ 依赖安装三行命令。README.en.md 同步一句。
- `CHANGELOG.md`：`### Added` 后端抽象 + `--backend` + `--list-backends`；`### Changed` lazy X11、Wayland 多行确认。
- `autopaste.py` 模块 docstring（3 行 "X11 窗口"）与 `build_parser.description`：改成"当前聚焦窗口"。

---

## 8. 风险表（按"翻车概率 × 后果"排序）

| # | 风险 | 触发条件 | 后果 | 缓解/验证 |
|---|---|---|---|---|
| 1 | **argv 长度上限 `MAX_ARG_STRLEN`=128 KiB** | 用户 `-f` 大文件（上限 1 MiB） | `E2BIG`，整个输入失败（X11 无此问题，属于回归性能力缺失） | 后端内分片（32 KiB/片，UTF-8 安全切割）；单测 13；实机 `head -c 200000 /dev/urandom \| base64` 冒烟 |
| 2 | **wtype 对 CJK 的按需 keymap 在部分合成器不可靠** | KDE Plasma 某些版本、高延迟合成器 | 丢字/乱字，且**用户可能看不见**（已进终端） | 默认 `-d 8` 不设 0；实机验收清单（§9）；失败时 stderr 透传 |
| 3 | **ydotool 不支持 Unicode** | GNOME + 中文 | 静默错误输入（最坏：半截命令被执行） | `capabilities()["unicode"]=False` + 提前 `Abort`；单测 24 |
| 4 | **mutter 不支持 virtual-keyboard** | GNOME 默认 | auto 首选 wtype 必然失败 | `XDG_CURRENT_DESKTOP` 反序 + 运行期回退；单测 7/19 |
| 5 | ydotool socket/权限 | daemon 没起、`/tmp` 与 `$XDG_RUNTIME_DIR` 版本差异、`input` 组 | 一直报"连接失败"，用户不知道怎么办 | 三候选路径探测 + 精准文案；单测 21/22/23/27 |
| 6 | 中断后子进程继续打字 | `SIGTERM` 只打父进程（不是终端 Ctrl-C） | 用户在"已取消"后仍被输入 | `Popen` + `try/finally: terminate(); wait(timeout=1)`；signals 已在 936 装 |
| 7 | 延时语义变化（`-d` 是整数 ms） | `-d 0.0005` → 0ms | 与原逐字符 0.5ms 不等价，快速输入丢字 | `max(1, round(delay*1000))` 当 delay>0；文档写明"Wayland 后端精度 1ms" |
| 8 | X11 行为回归 | 搬代码时手滑 | 老用户直接受影响（最高优先级） | 方法体整块搬迁 + 单测 28–33 golden 序列；diff review 只看"是否只改了 `self.` 前缀" |
| 9 | `focused_window()` 在 lazy 未加载时被调用 | 有人保留 `.dpy` 转发或直接调全局函数 | `AttributeError`/段错误 | 全局 `x11 = None` 时函数内 `raise BackendUnavailable`；`Typer` 无 `.dpy` 属性 |
| 10 | 退出码语义漂移 | `BackendUnavailable` 排在 `Abort` 后 | 环境错误退 1（文档期望 2） | except 顺序 + 单测 9 |
| 11 | 1 MiB × 8ms 的耗时（≈2.3h）被当成 bug | 用户喂大文件 | 抱怨"卡住" | 文案/dry-run 打印预计耗时（可选，一行 `len*delay`）；不是本任务必须 |

**明确不在本任务范围**（防止任务 03/04 撞车）：剪贴板/`--paste-mode`、`--window` 定向、批量 XTEST flush 提速、配置文件、`kdotool`/GNOME Shell 扩展拿焦点窗口。

---

## 9. 实施顺序（建议拆成 3 个 commit，对应 02.1 / 02.2 / 02.3）

**02.1 抽象层（必须单独 commit，且此时不新增任何 Wayland 代码）**
1. `Backend` 基类 + `BackendError/BackendUnavailable`。
2. `class Typer` → `class X11Backend(Backend)`：**只改类名 + 补 3 个方法**，方法体不动；`_load()` 把 70–72 行的 dlopen 搬进来（`focused_window` 内加 `_require_x11()`）。
3. 新增 `Typer` 外观 + `make_backend()`（本期只实现 `x11`，`auto` 在非 x11 会话抛"本版本仅 X11"——**便于单独合并/回滚**）。
4. 6 处 `.dpy` → `focus_info()`；`--backend {auto,x11}`、`--list-backends`。
5. 测试 28–41 全绿 + 手工回归一次 X11（`--dry-run`、`-k ctrl+v`、中文、REPL `:d`）。
   **验收：`git diff` 里 XTEST 相关行只有缩进/`self.` 变化；所有既有功能肉眼一致。**

**02.2 wtype 后端**：`WtypeBackend` + auto 选择/回退 + 缺依赖提示 + 测试 1–19 + README/SECURITY 更新。
   验收（需要真机，写进 PR 描述）：sway/Hyprland 下 ASCII、中文、emoji、`-k ctrl+shift+v`、8000 字符分片、`-d 20`。

**02.3 ydotool 后端**：`YdotoolBackend` + evdev 键码表 + socket/权限诊断 + 非 ASCII 拦截 + 测试 20–27。
   验收（真机）：GNOME Wayland、`ydotoold` 起停两种状态、非 `input` 组的报错文案、ASCII 文本 + `ctrl+v`。

若编排者只允许两个子任务：**02.1 = 抽象层；02.2 = wtype + ydotool**，但 02.2 会变成单个大 commit（~300 行新增），review 成本明显上升，**推荐 3 个**。

---

## 10. 代码骨架（可直接落地的最小形态）

```python
# ------------------------------------------------ 后端选择（放在 X11 绑定之后）
def _load_x11():
    """惰性 dlopen；纯 Wayland 机器上 import 本模块不得因为缺 libX11 而失败。"""
    global x11, xtst
    if x11 is not None:
        return
    x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
    # …70-115 行的 argtypes/restype 声明整体搬进来（幂等，重复调用无害）


def make_backend(name="auto", *, delay=0.008, remap_wait=0.02,
                 which=shutil.which, env=None, runner=None):
    env = os.environ if env is None else env
    name = choose_backend(name, env=env, which=which)   # §3.2
    if name == "x11":
        return X11Backend(delay=delay, remap_wait=remap_wait)
    if name == "wtype":
        return WtypeBackend(delay=delay, which=which, env=env, runner=runner)
    if name == "ydotool":
        return YdotoolBackend(delay=delay, which=which, env=env, runner=runner)
    raise BackendUnavailable(f"未知后端: {name}")
```

```python
class WtypeBackend(Backend):
    name = "wtype"
    MAX_ARG = 32 * 1024
    _PROTO_ERR = ("virtual keyboard", "protocol", "not supported", "failed to create")

    def __init__(self, delay=0.008, which=shutil.which, env=None, runner=None):
        if not which("wtype"):
            raise BackendUnavailable("未找到 wtype。\n" + _missing_hint("wayland", env))
        self.delay = delay
        self._run = runner or subprocess.run

    def _ms(self) -> str:
        return str(max(1, int(round(min(self.delay, MAX_DELAY) * 1000))))

    def _exec(self, args: list[str]) -> None:
        cp = self._run(["wtype", "-d", self._ms(), *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, check=False)
        if cp.returncode != 0:
            err = (cp.stderr or "").strip().splitlines()
            raise BackendError(f"wtype 失败（退出码 {cp.returncode}）："
                               f"{err[-1] if err else '无输出'}")

    def type_text(self, text: str) -> None:
        for chunk in _chunks(text, self.MAX_ARG):
            self._exec(["--", chunk])

    def press(self, combo: str) -> None:
        mods, key = parse_combo(combo)          # 从旧 press() 345-369 行提取
        args = []
        for m in mods:  args += ["-M", m]
        args += ["-k", SPECIAL.get(key.lower(), key)]
        for m in reversed(mods): args += ["-m", m]
        self._exec(args)

    def capabilities(self):
        return {"name": "wtype", "focus_info": False, "unicode": True,
                "needs_remap": False, "combos": True, "per_char_delay": True,
                "daemon": False}
```

```python
def parse_combo(name: str) -> tuple[list[str], str]:
    """从 Typer.press（345-369）提取：解析 'ctrl+alt+t'，key 原样返回。"""
    toks = name.split("+")
    mods = []
    for t in toks[:-1]:
        t = t.lower().strip()
        if t not in MODIFIERS:
            raise Abort(f"未知修饰键: {t}")
        mods.append(t)
    return mods, toks[-1].strip()
```

---

## 11. 验收清单（DoD）

- [ ] `python -m unittest discover -s tests` 全绿，且**不需要** `DISPLAY`/`WAYLAND_DISPLAY`。
- [ ] 纯 X11 会话：`--dry-run`、中文、`-k ctrl+v`、`-f` 文件、REPL `:d/:t` 与 8b22d3e 行为一致（人工逐项比对）。
- [ ] `env -u DISPLAY -u XDG_SESSION_TYPE -u WAYLAND_DISPLAY python3 autopaste.py --help` 退出 0（lazy load 生效）。
- [ ] `--backend wtype`（未装）→ 退出码 2 + 三行发行版安装命令。
- [ ] Wayland 真机（sway 或 KDE）：ASCII/中文/emoji/组合键；`--dry-run` 显示"焦点不可读"；多行 CLI 要求确认。
- [ ] GNOME 真机：auto 选 ydotool；中文被拦并给出可执行建议。
- [ ] `SECURITY.md` 中"不执行 subprocess"的表述已更新（否则属于安全文档失真）。
- [ ] 一个任务一个 commit；不 push。
