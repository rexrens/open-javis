# javis/host/app.py 入口层实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新建 `javis/host/app.py` 作为入口层，仿 OpenHarness `ui/app.py` 集中定义 `run_print_mode` / `run_tui_mode` 两个模式入口，`cli.py` 变薄只做 typer 参数解析。

**架构：** 分层——`cli.py`（薄：参数解析 + `asyncio.run`）→ `host/app.py`（入口层：定义入口函数）→ 实现层（`runtime.py` 的 `build_runtime`/`handle_line`、`backend_host.py` 的 `run_backend_mode`、`react_launcher.py` 的 `launch_react_tui`）。`run_tui_mode` 带 `backend_only` 子参数（仿 OpenHarness `run_repl(backend_only=...)`）：True → `run_backend_mode`（前端 spawn 用），False → `launch_react_tui`（前端再按 `backend_command` spawn 后端）。

**技术栈：** Python 3.10+ / asyncio / typer / pytest-asyncio（`asyncio_mode = auto`）/ ruff

**设计文档：** `docs/superpowers/specs/2026-08-27-host-app-entry-layer-design.md`

---

## 文件结构

- **创建：`javis/host/app.py`** —— 入口层。docstring 注明"仿 openharness.ui.app"。定义 `run_tui_mode`（任务 1）与 `run_print_mode`（任务 2，从 runtime.py 迁入）。顶部绑定 import `build_runtime` / `handle_line`（来自 `javis.host.runtime`）、`run_backend_mode`（来自 `javis.host.backend_host`）、`launch_react_tui`（来自 `javis.host.react_launcher`）——绑定 import 保证测试能 monkeypatch `javis.host.app.<name>`。
- **修改：`javis/host/runtime.py`** —— 删除 `run_print_mode`（273-331 行）及 `__all__` 条目、`import os`、`import sys`、`AgentError`/`AgentStatus`（types import 中仅此两个随函数迁出；`AgentEvent`/`AgentTextDelta`/`AgentTurnEnd` 被 `handle_line`/`_replay_assistant` 使用，保留）；docstring 第 10 行删除 `- ``run_print_mode`` — non-interactive single-prompt mode`。
- **修改：`javis/cli.py`** —— 顶部 `from javis.host.runtime import run_print_mode` 改为 `from javis.host.app import run_print_mode, run_tui_mode`；`--backend-only` 分支改调 `run_tui_mode(backend_only=True, ...)`（删局部 import `run_backend_mode`）；默认分支改调 `run_tui_mode(...)`（删局部 import `launch_react_tui`）；print 分支调用不变（符号同名，来自 app）。
- **修改：`tests/test_javis/test_runtime.py`** —— `test_print_mode_treats_slash_prompt_as_user_message`：import 改 `from javis.host.app import run_print_mode`；patch 目标 `"javis.host.runtime.build_runtime"` 改 `"javis.host.app.build_runtime"`。
- **创建：`tests/test_javis/test_app.py`** —— 入口层薄层测试（`run_tui_mode` 分叉 + 参数透传）。

**关键约束：**
- `backend_host.py` 从 `runtime.py` import `build_runtime`/`handle_line`/`RuntimeBundle` —— 不受本计划影响（无环）。
- app.py 的 `run_print_mode` 调用 `build_runtime`（app 模块命名空间），所以测试 patch 必须指向 `javis.host.app.build_runtime` 而非 `javis.host.runtime.build_runtime`。
- 迁移期间 runtime.py 与 app.py 会短暂同时定义 `run_print_mode`（任务 2 完成后、任务 3 删除前）——此期间全量测试仍须绿。

---

## 任务 1：新建 app.py 骨架 + `run_tui_mode` 分叉

**文件：**
- 创建：`javis/host/app.py`
- 测试：`tests/test_javis/test_app.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_javis/test_app.py`：

```python
"""Entry-layer tests for javis.host.app mode dispatch."""

from __future__ import annotations

import pytest

from javis.host.app import run_tui_mode


@pytest.mark.asyncio
async def test_run_tui_mode_backend_only_dispatches_to_backend(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_backend(**kwargs: object) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr("javis.host.app.run_backend_mode", fake_backend)
    code = await run_tui_mode(
        backend_only=True,
        cwd="/tmp/proj",
        model="m1",
        max_turns=4,
        workspace="/tmp/ws",
    )
    assert code == 7
    assert captured["cwd"] == "/tmp/proj"
    assert captured["model"] == "m1"
    assert captured["max_turns"] == 4
    assert captured["workspace"] == "/tmp/ws"


@pytest.mark.asyncio
async def test_run_tui_mode_default_dispatches_to_react_launcher(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_launcher(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("javis.host.app.launch_react_tui", fake_launcher)
    code = await run_tui_mode(cwd="/tmp/proj")
    assert code == 0
    assert captured["cwd"] == "/tmp/proj"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/test_app.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'javis.host.app'`

- [ ] **步骤 3：创建 app.py 实现 `run_tui_mode`**

创建 `javis/host/app.py`：

```python
"""Application entry points: mode dispatch for javis.

Forked from openharness.ui.app and trimmed: javis exposes exactly two
user-facing modes — ``run_print_mode`` (single prompt, print to stdout) and
``run_tui_mode`` (React terminal frontend, which spawns the JSON-lines backend
itself via ``OPENHARNESS_FRONTEND_CONFIG.backend_command``). The backend host
is an implementation detail of TUI mode: ``backend_only=True`` runs it
directly, mirroring openharness' ``run_repl(backend_only=...)``.

Layer layout (entry → implementation):
    javis.cli           typer parsing only
    javis.host.app      this file — entry functions
    javis.host.runtime  build_runtime / handle_line
    javis.host.backend_host / react_launcher  implementations
"""

from __future__ import annotations

from pathlib import Path

from javis.host.backend_host import run_backend_mode
from javis.host.react_launcher import launch_react_tui


async def run_tui_mode(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    backend_only: bool = False,
) -> int:
    """Run the interactive React TUI, or the JSON-lines backend it spawns.

    ``backend_only=True`` is the mode the React frontend launches via
    ``python -m javis --backend-only`` — it is not a third user-facing mode.
    """
    if backend_only:
        return await run_backend_mode(
            cwd=cwd,
            workspace=workspace,
            model=model,
            max_turns=max_turns,
        )
    return await launch_react_tui(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
    )


__all__ = ["run_tui_mode"]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_javis/test_app.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add javis/host/app.py tests/test_javis/test_app.py
git commit -m "feat(host): app.py entry layer with run_tui_mode dispatch"
```

---

## 任务 2：迁移 `run_print_mode` 到 app.py

**文件：**
- 修改：`tests/test_javis/test_runtime.py`（`test_print_mode_treats_slash_prompt_as_user_message` 的 import 与 patch 目标）
- 修改：`javis/host/app.py`
- 测试：`tests/test_javis/test_runtime.py`

- [ ] **步骤 1：更新测试的 import 与 patch 目标**

修改 `tests/test_javis/test_runtime.py` 中 `test_print_mode_treats_slash_prompt_as_user_message`（约 125-139 行）：

```python
-    from javis.host.runtime import run_print_mode
+    from javis.host.app import run_print_mode
...
-    monkeypatch.setattr("javis.host.runtime.build_runtime", _fake_build)
+    monkeypatch.setattr("javis.host.app.build_runtime", _fake_build)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/test_runtime.py::test_print_mode_treats_slash_prompt_as_user_message -q`
预期：FAIL，`ImportError: cannot import name 'run_print_mode' from 'javis.host.app'`

- [ ] **步骤 3：把 `run_print_mode` 迁入 app.py**

从 `javis/host/runtime.py` 原样复制 `run_print_mode`（当前 273-331 行，含 `os.chdir` / `sys.stdout` 逻辑）到 `javis/host/app.py`，并补齐 import：

```python
import os
import sys

from javis.contracts.messages import ConversationMessage
from javis.contracts.types import AgentError, AgentEvent, AgentStatus
from javis.host.runtime import build_runtime, handle_line
```

`__all__` 改为：

```python
__all__ = ["run_print_mode", "run_tui_mode"]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_javis/test_runtime.py tests/test_javis/test_app.py -q`
预期：PASS（runtime + app 两组全绿；此刻 runtime.py 与 app.py 并存两份 `run_print_mode`，属预期中间态）

- [ ] **步骤 5：Commit**

```bash
git add javis/host/app.py tests/test_javis/test_runtime.py
git commit -m "refactor(host): move run_print_mode from runtime.py to app.py"
```

---

## 任务 3：cli.py 薄化 + runtime.py 清理

**文件：**
- 修改：`javis/cli.py`
- 修改：`javis/host/runtime.py`
- 测试：`tests/test_javis/`（全量回归）

- [ ] **步骤 1：cli.py 三个分支改调 app 函数**

`javis/cli.py` 顶部（第 17 行）：

```python
- from javis.host.runtime import run_print_mode
+ from javis.host.app import run_print_mode, run_tui_mode
```

`--backend-only` 分支（第 63-74 行）——删除局部 import，改调 `run_tui_mode(backend_only=True)`：

```python
     if backend_only:
-        from javis.host.backend_host import run_backend_mode
-
         raise SystemExit(
             asyncio.run(
-                run_backend_mode(
+                run_tui_mode(
+                    backend_only=True,
                     cwd=cwd_path,
                     workspace=workspace_root,
                     model=model,
```

默认 TUI 分支（第 89-97 行）——删除局部 import，改调 `run_tui_mode`：

```python
-    from javis.host.react_launcher import launch_react_tui
-
     raise SystemExit(
         asyncio.run(
-            launch_react_tui(
+            run_tui_mode(
                 cwd=cwd_path,
                 workspace=workspace_root,
                 model=model,
```

print 分支（第 78-87 行）调用 `run_print_mode(...)` 不变（符号现来自 app.py 顶部 import）。

- [ ] **步骤 2：runtime.py 删除 `run_print_mode` 并清理 import**

- 删除 `run_print_mode` 函数（273-331 行整段，含函数前空行）
- 顶部删除 `import os`、`import sys`
- types import 改为：`from javis.contracts.types import AgentEvent, AgentTextDelta, AgentTurnEnd`（去掉 `AgentError, AgentStatus`）
- `__all__` 删除 `"run_print_mode",`
- 模块 docstring（第 10 行）删除 `- ``run_print_mode`` — non-interactive single-prompt mode`

- [ ] **步骤 3：全量测试验证**

运行：`uv run pytest -q`
预期：PASS（全量，当前基线 186 tests）

- [ ] **步骤 4：ruff 检查**

运行：`uv run ruff check javis/`
预期：clean（0 errors）

- [ ] **步骤 5：Commit**

```bash
git add javis/cli.py javis/host/runtime.py
git commit -m "refactor(cli): thin cli.py, dispatch via host.app entry layer"
```

---

## 任务 4：最终验证

**文件：**
- 无（验证 + 可选微调）

- [ ] **步骤 1：全量测试 + ruff**

运行：`uv run pytest -q && uv run ruff check javis/`
预期：全绿 + clean

- [ ] **步骤 2：CLI 冒烟（不触发真实 API 调用）**

运行：`uv run python -m javis --help`
预期：三模式帮助文本正常（默认 TUI / `--print` / `--backend-only` 隐藏项）

运行：`uv run python -m javis doctor`
预期：workspace / frontend 检查输出正常（验证 cli.py 子命令未受影响）

- [ ] **步骤 3：确认无残留引用**

运行：`rg -n "host.runtime import run_print_mode|from javis.host.runtime import run_print_mode" javis/ tests/`
预期：无输出（无任何模块再引用 `runtime.run_print_mode`）

- [ ] **步骤 4：Commit（如有改动）**

```bash
git add -A && git commit -m "chore(host): final app entry layer cleanup" || echo "nothing to commit"
```

---

## 成功标准

- [ ] `javis/host/app.py` 存在，定义 `run_print_mode` / `run_tui_mode`（`backend_only` 子参数）
- [ ] `javis/host/runtime.py` 不再定义 `run_print_mode`，import 无 `os`/`sys`/`AgentError`/`AgentStatus`
- [ ] `javis/cli.py` 只调用 app.py 的入口函数，不直接调实现层
- [ ] 全量测试通过（基线 186 + 新增 2），ruff clean
