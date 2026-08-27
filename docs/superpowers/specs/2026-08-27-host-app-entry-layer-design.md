# javis/host/app.py 入口层设计

日期：2026-08-27
状态：已批准

## 背景

open-javis 目前把模式分发逻辑内联在 `javis/cli.py` 中：`--backend-only` → `run_backend_mode`，`--print` → `run_print_mode`，默认 → `launch_react_tui`。三个入口函数散落在 `host/runtime.py`、`host/backend_host.py`、`host/react_launcher.py` 三个文件中，入口职责不集中。

参考 OpenHarness：`src/openharness/cli.py` 只做 typer 参数解析 + `asyncio.run` 调用，入口函数集中定义在 `src/openharness/ui/app.py`（"Interactive session entry points"），实现层（`react_launcher.py` / `backend_host.py`）被 app 层调用。

## 目标

新建 `javis/host/app.py` 作为**入口层**，仿 OpenHarness 集中定义模式入口函数，`cli.py` 变薄只做参数解析。

## 非目标

- 不新增 task_worker / headless 入口（javis 无此概念）
- 不改动 wire 协议、engine、plugin 系统
- 不重构 `build_runtime` / `handle_line` 内部逻辑

## 架构

```
javis/cli.py            → 薄：typer 参数解析 + asyncio.run(app 入口函数)
javis/host/app.py       → 入口层 [新]：run_print_mode / run_tui_mode
javis/host/runtime.py   → 核心层：build_runtime / handle_line（run_print_mode 迁出）
javis/host/backend_host.py    → 实现层：run_backend_mode
javis/host/react_launcher.py  → 实现层：launch_react_tui
```

## 模式定义

**两个用户可见模式**（backend 是 tui_mode 的内部实现细节，由 React 前端 spawn）：

1. **print_mode**：`run_print_mode(prompt, ...)` — 单次 prompt，打印输出后退出。
2. **tui_mode**：`run_tui_mode(...)` — 默认启动 React 终端前端（`launch_react_tui`）；前端通过
   `OPENHARNESS_FRONTEND_CONFIG.backend_command`（即 `python -m javis --backend-only`）
   再 spawn 后端进程，即"先前端后后端"通讯。
   - `backend_only=True` 子参数：直接调 `run_backend_mode`（JSON-lines 后端宿主，前端 spawn 时用）。

## 入口层函数签名

```python
async def run_print_mode(*, prompt: str, cwd=None, workspace=None, model=None, max_turns=None) -> int

async def run_tui_mode(*, cwd=None, workspace=None, model=None, max_turns=None, backend_only=False) -> int
```

`backend_only` 参数与 OpenHarness `run_repl(backend_only=...)` 一致——backend 不暴露为独立入口，
只是 `run_tui_mode` 的一个子模式。

## 具体改动

| 文件 | 改动 |
|---|---|
| `javis/host/app.py` | **新建**。docstring 注明"入口层，仿 openharness.ui.app"；定义两个入口函数。`run_print_mode` 从 `runtime.py` 原样迁入（含 `os.chdir` / `sys.stdout` 逻辑）；`run_tui_mode` 内部分叉 `backend_only` |
| `javis/host/runtime.py` | 删除 `run_print_mode`（约 60 行）及 `__all__` 中的条目；清理不再使用的 `os`/`sys` import；模块 docstring 同步 |
| `javis/cli.py` | 三个分支（print / backend-only / 默认 TUI）统一改调 `app.run_print_mode` / `app.run_tui_mode(backend_only=...)`；删除分散在函数体内的局部 import |
| `tests/test_javis/test_runtime.py` | 第 125 行 `from javis.host.runtime import run_print_mode` 改为 `from javis.host.app import run_print_mode` |

### 模块依赖（无环）

- `app.py` → `runtime.py`（build_runtime / handle_line）、`backend_host.py`（run_backend_mode）、`react_launcher.py`（launch_react_tui）
- `backend_host.py` → `runtime.py`（不变）
- `cli.py` → `app.py`

## 错误处理

- `SystemExit` 由 cli.py 统一包 `asyncio.run(...)` 抛出（现有行为不变）
- `launch_react_tui` 的 `RuntimeError`（前端缺失）保持不变
- 无新增错误路径

## 测试策略

1. 迁移后跑全套测试（当前 186 passed），确认零回归
2. 新增 `tests/test_javis/test_app.py` 薄层测试：
   - `run_tui_mode(backend_only=True)` 分叉到 `run_backend_mode`（monkeypatch 替身）
   - `run_tui_mode()` 默认分叉到 `launch_react_tui`（monkeypatch 替身）
   - 两个入口的关键参数透传（cwd / model / max_turns / workspace）

## 成功标准

- [ ] `javis/host/app.py` 存在且定义两个入口函数
- [ ] `javis/host/runtime.py` 不再定义 `run_print_mode`
- [ ] `javis/cli.py` 不直接调用实现层函数（只调 app.py）
- [ ] 全套测试通过，新增 test_app.py
