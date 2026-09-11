# Harness 组合式重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cordis.yml` 组合文件成为 Harness 组装的唯一来源 —— 默认发行全量六行组合，缺失/失败即报错，Harness 内部四服务直接上根 context。

**Architecture:** 机械改名（Harness / AgentLoop / stream）→ 循环侧工具服务改名 `agentTools` 并改为宿主注册表的实时视图 → 装载器入口断言 → 六条组合行 + `Harness` 去私有装配 + `build_runtime` 收敛为 boot + 断言（一次原子翻转）→ examples / 文档同步。

**Tech Stack:** Python 3.11+ / pydantic v2 / pytest + pytest-asyncio / Cordis 插件内核（`javis/cordis`）/ uv

**Spec:** `docs/superpowers/specs/2026-09-11-harness-composition-refactor-design.md`

**基线:** 工作区干净后 `uv run pytest tests/ -q` = **295 passed**（约 15s）。每个 Task 结束都必须保持全绿。

---

## 文件结构（落点地图）

| 文件 | 动作 | 职责 |
|---|---|---|
| `javis/contracts/engine.py` | 改名 → `harness.py` | `Harness` 协议（宿主唯一 seam） |
| `javis/contracts/services.py` | 修改 | 服务名常量：`HARNESS_SERVICE` / `AGENT_TOOLS_SERVICE` / `SYSTEM_PROMPT_SERVICE` / `AGENT_LOOP_SERVICE` |
| `javis/harness/llm.py` | 拆分删除 | `LLM`+`PreparedCall` → `types.py`；流处理 → `stream.py` |
| `javis/harness/stream.py` | 新增 | `normalized_stream` / `BlockAssembler` / `assemble_finish` / `chunk_response` |
| `javis/harness/agent.py` | 修改 | `ReactAgentLoop` → `AgentLoop`；服务读取名改 `agentTools` |
| `javis/harness/tools.py` | 修改 | 注册表读取名改 `agentTools` |
| `javis/harness/prompt.py` | 修改 | schema 来源改 `agentTools` |
| `javis/harness/tool_adapter.py` | 修改 | 新增 `AgentToolView`（宿主 `tools` 的实时视图）；删除 `adapt_registry` |
| `javis/harness/engine.py` | 改名 → `harness.py` 并重写 | `Harness` 实现：从根 context 读服务，无私有装配 |
| `javis/harness/build.py` | 删除 | 装配逻辑拆入插件行 |
| `javis/harness/plugins/*.py` | 新增 6 行 | llm / agent_tools / system_prompt / agent_loop / snip / harness |
| `javis/cordis/fiber.py` | 修改 | `Fiber.error` / `Fiber.missing_inject()` 访问器 |
| `javis/cordis/loader/__init__.py` | 修改 | `assert_entries_settled(ctx)` 入口断言 |
| `javis/session/config.py` | 修改 | `DEFAULT_COMPOSITION` 全量六行 + `ensure_default_composition` 写入 |
| `javis/app/runtime.py` | 修改 | 删除 `_build_default_engine`；boot + 断言 |
| `examples/dsh_harness/**` | 修改 | 服务名/类名对齐 |
| `docs/plugins.md`、`README.md`、`README.zh-CN.md` | 修改 | 内建服务表与目录树 |
| 测试 | 修改/新增 | conftest seam、plugin_runtime、engine/agent_loop 测试、loader 断言测试、实时视图测试 |

---

### Task 0: 提交进行中的改名工作

工作区已有 15 个未提交文件（`ReactLoopAgent` → `ReactAgentLoop` 改名及文档同步）。重构必须从干净的工作区出发，先把它作为一个独立提交落盘。

**Files:** 全部 `git status` 中的已修改文件

- [ ] **Step 1: 确认待提交内容**

Run: `git status --short && git diff --stat`
Expected: 15 个修改文件，无未跟踪的新文件。

- [ ] **Step 2: 确认基线全绿**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `295 passed`

- [ ] **Step 3: 提交**

```bash
git add README.md README.zh-CN.md examples/dsh_harness examples/mini_dsh javis/harness javis/tools/agent.py tests
git commit -m "refactor(harness): rename ReactLoopAgent to ReactAgentLoop across code and docs"
```

---

### Task 1: 契约改名 —— `Harness` 协议 + 服务名常量

**Files:**
- Rename: `javis/contracts/engine.py` → `javis/contracts/harness.py`
- Modify: `javis/contracts/services.py`
- Modify: `javis/contracts/__init__.py`
- Modify: `javis/app/runtime.py`（仅类型名与 import）
- Modify: `javis/commands/registry.py:24`
- Modify: `javis/app/backend_host.py:483`（docstring 一处）
- Test: `tests/test_javis/test_plugin_runtime.py`、`tests/test_javis/fake_backend.py`、`tests/test_javis/conftest.py`

- [ ] **Step 1: 改名契约文件**

Run: `git mv javis/contracts/engine.py javis/contracts/harness.py`

- [ ] **Step 2: 重写 `javis/contracts/harness.py` 的协议名**

把文件 docstring 与类名整体替换：

```python
"""Harness contract — the single engine seam.

The host (runtime / TUI / commands) talks to exactly one object: a
``Harness`` that owns conversation history and usage, and yields
``AgentEvent`` streams per turn. The built-in implementation is
``javis.harness.harness.Harness``; a composition row provides an instance
of this protocol under the ``harness`` service (see
``javis.contracts.services``) to replace it.

This replaces the old two-level seam (``AgentBackend`` protocol + a
``QueryEngine`` shell) with a single contract.
"""
```

类声明改为：

```python
@runtime_checkable
class Harness(Protocol):
    """One harness object: history + usage + event-stream turns.

    Optional hooks (probed with ``hasattr``, NOT part of the Protocol so a
    minimal implementation can skip them):
    ...
    """
```

`__all__ = ["Harness"]`。文件其余内容（属性/方法签名与 docstring）保持不变。

- [ ] **Step 3: 重写 `javis/contracts/services.py`**

```python
"""Typed service contracts for the plugin system.

A service is ``(name, type)``: plugins look it up by name and validate the
type via ``ctx.get(name, Type)``. The host provides built-ins (owner=None,
never revoked); composition rows provide their own with ``ctx.provide``.

Names are stable strings — changing one breaks every plugin using it. The
contract *types* live in ``javis.contracts`` (``ToolRegistry`` /
``HostContext``) or with the objects they describe (``JavisConfig`` /
``CommandRegistry``); this module only fixes the names so core and plugins
agree on them.
"""

from __future__ import annotations

TOOLS_SERVICE = "tools"
COMMANDS_SERVICE = "commands"
CONFIG_SERVICE = "config"
HOST_SERVICE = "host"

# -- harness rows (javis.harness.plugins.*) --------------------------------
#: Loop-facing tool registry (the live view over the host ``tools``).
AGENT_TOOLS_SERVICE = "agentTools"
#: Prompt assembly (persona + step context + tool schemas).
SYSTEM_PROMPT_SERVICE = "systemPrompt"
#: Loop driver configuration.
AGENT_LOOP_SERVICE = "agentLoop"
#: The model service (a ``javis.llm.LlmRuntime`` adapter registry).
LLM_SERVICE = "llm"
#: The whole agent: a ``javis.contracts.harness.Harness`` instance.
HARNESS_SERVICE = "harness"

__all__ = [
    "AGENT_LOOP_SERVICE",
    "AGENT_TOOLS_SERVICE",
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "HARNESS_SERVICE",
    "HOST_SERVICE",
    "LLM_SERVICE",
    "SYSTEM_PROMPT_SERVICE",
    "TOOLS_SERVICE",
]
```

- [ ] **Step 4: 更新 `javis/contracts/__init__.py`**

```python
from javis.contracts.harness import Harness
from javis.contracts.host import HostContext
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.services import (
    AGENT_LOOP_SERVICE,
    AGENT_TOOLS_SERVICE,
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    HARNESS_SERVICE,
    HOST_SERVICE,
    LLM_SERVICE,
    SYSTEM_PROMPT_SERVICE,
    TOOLS_SERVICE,
)
```

`__all__` 同步替换（`AgentEngine` → `Harness`，`ENGINE_SERVICE` → `HARNESS_SERVICE`，加入四个新常量）。模块 docstring 的 `engine.py — the AgentEngine interface` 行改为 `harness.py — the Harness interface`。

- [ ] **Step 5: 机械替换全部引用**

Run: `grep -rn "AgentEngine\|ENGINE_SERVICE\|contracts.engine" --include=*.py javis/ tests/`

逐处改为 `Harness` / `HARNESS_SERVICE` / `javis.contracts.harness`。已知落点：

- `javis/app/runtime.py:31` `from javis.contracts.harness import Harness`；`:37` import 常量块；`:76` `engine: Harness`；`:121` `-> Harness`；`:179`/`:186` docstring。
- `javis/commands/registry.py:14,24`：`from javis.contracts.harness import Harness`，`engine: Harness`。
- `javis/app/backend_host.py:483` docstring：``Harness.set_permission_checker``。
- `tests/test_javis/test_plugin_runtime.py:20`：`from javis.contracts import HARNESS_SERVICE`，`:37` 插件源码里的 import 与 `:112` `ctx.provide(HARNESS_SERVICE, PluginEngine())`（字符串内的插件源码也在这一步改，类名 `PluginEngine` 保留）。
- `tests/test_javis/fake_backend.py`：若有 `AgentEngine` 字样一并替换（`FakeEngine` 类名不变）。

- [ ] **Step 6: 跑测试**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `295 passed`

- [ ] **Step 7: 提交**

```bash
git add -A javis tests
git commit -m "refactor(contracts): rename AgentEngine protocol to Harness, engine service to harness"
```

---

### Task 2: 拆分 `javis/harness/llm.py` → `stream.py` + `types.py`

**Files:**
- Rename: `javis/harness/llm.py` → `javis/harness/stream.py`
- Modify: `javis/harness/types.py`（新增 `PreparedCall` + `LLM`）
- Modify: `javis/harness/__init__.py:41,61`（`from . import llm as llm` → `from . import stream as stream`；`__all__` 的 `"llm"` → `"stream"`）
- Modify: `javis/harness/agent.py:44`
- Modify: `javis/llm/runtime.py:533-536`、`javis/llm/scripted.py:8`、`javis/llm/openai_compat.py:13,121`
- Modify: 4 个测试 + `examples/dsh_harness/mock_llm.py:27`

- [ ] **Step 1: 改名文件**

Run: `git mv javis/harness/llm.py javis/harness/stream.py`

- [ ] **Step 2: 把 `PreparedCall` 与 `LLM` 移入 `javis/harness/types.py`**

在 `types.py` 的 `LlmCallConfig` 定义之后（`GenerateOptions` 之前）插入：

```python
@dataclass
class PreparedCall:
    """The adapter registration that resolved one request's exact-model defaults."""

    config: LlmCallConfig
    #: Which config fields were supplied by the adapter, not the caller.
    adapter_defaults: dict[str, bool] = field(default_factory=dict)
    #: Adapter context (``{"contextWindow": int}``) when advertised.
    context: dict[str, Any] | None = None
    #: Optional retry policy (consumed by ``agent/request-error`` listeners).
    retry_policy: dict[str, Any] | None = None
    #: Adapter-bound stream for this exact-model registration; ``None`` lets
    #: the loop fall back to the provider's plain ``stream(options)``.
    stream: Callable[[GenerateOptions], AsyncIterator[Any]] | None = None


@runtime_checkable
class LLM(Protocol):
    """The model service. Implementations must be SDK-free at this seam."""

    def prepare_call(
        self, config: LlmCallConfig, signal: AbortSignal | None = None
    ) -> PreparedCall | Awaitable[PreparedCall]:
        """Resolve exact-model adapter defaults for ``config``."""
        ...

    def stream(self, options: GenerateOptions) -> AsyncIterator[Any]:
        """Emit the raw streaming protocol for one request."""
        ...
```

`types.py` 顶部 import 相应补齐（若缺）：`from collections.abc import AsyncIterator, Awaitable, Callable` / `from typing import Any, Protocol, runtime_checkable`。在文件末尾的 `__all__`（若无 `__all__` 则跳过）加入 `"LLM"`、`"PreparedCall"`。

判断依据：这两者属于"循环词汇表"，与 `LlmCallConfig` / `GenerateOptions` / `StreamChunk` 同处。

- [ ] **Step 3: 重写 `stream.py` 的头部与导出口**

docstring 改为：

```python
"""Loop-side stream assembly (dsh ``@deepseek-ai/dsh-llm`` stream surface).

- :func:`normalized_stream` — turn any producer failure into a terminal
  ``error``/``aborted`` finish so consumers always see a well-formed stream.
- :class:`BlockAssembler` (dsh ``BlockAssembler``) — folds
  :class:`~javis.harness.types.StreamChunk` deltas into assembled content
  blocks, usage, and the terminal finish reason.

The ``LLM`` service protocol and ``PreparedCall`` live in
:mod:`javis.harness.types`; provider adapters live in ``javis.llm``.
"""
```

import 块（去掉 `Protocol`/`runtime_checkable`/`dataclass`，保留 chunk 类型），末尾导出：

```python
__all__ = [
    "BlockAssembler",
    "assemble_finish",
    "chunk_response",
    "normalized_stream",
]
```

删除文件内已移入 `types.py` 的 `PreparedCall` / `LLM` 定义（原 `llm.py:49-82`）。

- [ ] **Step 4: 更新 7 处 import**

| 文件 | 改为 |
|---|---|
| `javis/harness/__init__.py:41,61` | `from . import stream as stream`；`__all__` 里 `"llm"` → `"stream"` |
| `javis/harness/agent.py:44` | `from .stream import BlockAssembler, assemble_finish, normalized_stream` |
| `javis/llm/runtime.py:536` | `from javis.harness.types import PreparedCall`（docstring `:533` 同步：`javis.harness.types.PreparedCall`） |
| `javis/llm/scripted.py:8` | docstring：`javis.harness.stream.chunk_response` |
| `javis/llm/openai_compat.py:13,121` | docstring：`javis.harness.stream`；`:121` `AgentEngine.set_model` → `Harness.set_model`（若 Task 1 未覆盖） |
| `tests/test_harness/test_llm_runtime.py:14` | `from javis.harness.stream import chunk_response` |
| `tests/test_harness/test_engine.py:15` | 同上 |
| `tests/test_harness/test_async_llm.py:12` | 同上 |
| `tests/test_harness/test_agent_loop.py:17` | 同上 |
| `examples/dsh_harness/mock_llm.py:27` | `from javis.harness.stream import chunk_response` |

- [ ] **Step 5: 跑测试**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `295 passed`

- [ ] **Step 6: 提交**

```bash
git add -A javis tests examples
git commit -m "refactor(harness): split llm.py into stream.py and move LLM/PreparedCall into types"
```

---

### Task 3: 术语改名 —— `AgentLoop` / `AgentLoopService`

**Files:**
- Modify: `javis/harness/agent.py:113`（`ReactAgentLoop` → `AgentLoop`）
- Modify: `javis/harness/types.py:579`（`class AgentLoop` → `class AgentLoopService`）
- Modify: `javis/harness/engine.py`（引用）
- Modify: `examples/dsh_harness/plugins/{driver.py,agent_loop_config.py}` + `README.md`
- Modify: `tests/test_javis/test_runtime.py:106,116`

- [ ] **Step 1: 改名循环类**

在 `javis/harness/agent.py` 中把 `class ReactAgentLoop:` 改为 `class AgentLoop:`（docstring 保持 "Drives one session through turn and step boundaries (dsh ``Agent``)"）。文件内无其它 `ReactAgentLoop` 自引用时，运行：

Run: `grep -rn "ReactAgentLoop" javis/ tests/ examples/`

`javis/harness/engine.py` 中 `from .agent import ReactAgentLoop` → `from .agent import AgentLoop`，两处 `ReactAgentLoop(...)` 构造同步改名。

- [ ] **Step 2: 改名服务持有类**

`javis/harness/types.py`：

```python
class AgentLoopService:
    """The ``"agentLoop"`` service: the loop driver's configuration."""

    def __init__(self, config: AgentLoopConfig) -> None:
        self.config = config
```

- [ ] **Step 3: 更新 examples 与测试**

- `examples/dsh_harness/plugins/driver.py`：`from javis.harness.agent import AgentLoop`；注释与构造改名。
- `examples/dsh_harness/plugins/agent_loop_config.py`：`from javis.harness.types import AgentLoopConfig, AgentLoopService`，`AgentLoopService(AgentLoopConfig(...))`；docstring 里的 `ReactAgentLoop` 改 `AgentLoop`。
- `examples/dsh_harness/README.md`：全部 `ReactAgentLoop` → `AgentLoop`（`:15,:21,:37,:44,:52,:203,:222`）。
- `tests/test_javis/test_runtime.py:106,116`：`from javis.harness.agent import AgentLoop`，`isinstance(bundle.engine.agent, AgentLoop)`。
- `tests/test_harness/test_agent_loop.py` docstring 里的 `ReactAgentLoop` → `AgentLoop`。

注意：`examples/mini_dsh/` 是独立的 core 副本（不 import `javis.harness`），本期不动。

- [ ] **Step 4: 跑测试**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `295 passed`

- [ ] **Step 5: 提交**

```bash
git add -A javis examples tests
git commit -m "refactor(harness): rename ReactAgentLoop to AgentLoop and the agentLoop service holder"
```

---

### Task 4: 循环侧工具服务改名 `agentTools` + 实时视图

核心循环从 `ctx.get("tools")` 改读 `ctx.get("agentTools")`（宿主 `tools` 是 javis `ToolRegistry`，语义不同，不能同名）。`agentTools` 实现为宿主注册表的**实时视图**：每次读取都委托宿主并即时适配，修掉"构建期快照后注册的工具不可见"的缺陷。

**Files:**
- Modify: `javis/harness/tools.py:196,255`（`ctx.get("tools")` → `AGENT_TOOLS_SERVICE`）
- Modify: `javis/harness/prompt.py:43`
- Modify: `javis/harness/tool_adapter.py`（新增 `AgentToolView`，删除 `adapt_registry`）
- Modify: `javis/harness/__init__.py:48,56`（`adapt_registry` 的 import 与 `__all__` 改 `AgentToolView`，否则删函数即 ImportError）
- Modify: `javis/harness/engine.py:184-190`（私有 ctx 改 provide `agentTools`）
- Modify: `examples/dsh_harness/plugins/{demo_tools.py,system_prompt.py,driver.py}` + `cordis.yml` + `README.md`
- Modify: `tests/test_demo_harness.py:276`（`_ctx.get("tools")` → `agentTools`）
- Test: `tests/test_harness/test_tool_view.py`（新增）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_harness/test_tool_view.py`：

```python
"""``AgentToolView``: the loop-facing live view over the host tool registry."""

from __future__ import annotations

from typing import Any, ClassVar

from javis.contracts.tools import Tool as JavisTool
from javis.contracts.tools import ToolRegistry as JavisToolRegistry
from javis.cordis import Context
from javis.harness.tool_adapter import AgentToolView


class LateTool(JavisTool):
    name = "late_tool"
    description = "registered after the view exists"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return "late"


def test_view_sees_tools_registered_after_it_is_built():
    host = JavisToolRegistry()
    ctx = Context()
    view = AgentToolView(host, ctx)

    assert view.get("late_tool") is None
    assert view.schemas() == []

    host.register(LateTool())

    assert view.get("late_tool") is not None
    assert [schema.name for schema in view.schemas()] == ["late_tool"]
    assert [tool.name for tool in view.all()] == ["late_tool"]
    assert view.execution_mode("late_tool").kind == "parallel"


def test_view_register_forwards_to_the_host_registry():
    host = JavisToolRegistry()
    ctx = Context()
    view = AgentToolView(host, ctx)

    view.register(LateTool())

    assert host.get("late_tool") is not None
    assert [schema.name for schema in view.schemas()] == ["late_tool"]
```

- [ ] **Step 2: 运行确认失败**

Run: `timeout 120 uv run pytest tests/test_harness/test_tool_view.py -q`
Expected: FAIL — `ImportError: cannot import name 'AgentToolView'`

- [ ] **Step 3: 在 `tool_adapter.py` 实现视图，删除 `adapt_registry`**

把 `adapt_registry`（原 `:83-100`）整段替换为：

```python
class AgentToolView:
    """Live loop-facing view over the host's javis ``ToolRegistry``.

    Read operations delegate to the host registry on every call and adapt
    the result on the fly — a tool registered after this view was built is
    still visible to the loop. ``register`` forwards to the host registry,
    which stays the single source of truth.

    Why not a snapshot: the host ``tools`` service is a *javis* registry
    (``javis.contracts.tools``) while the loop needs *core* tools
    (``.tools``), and plugin rows may register tools in any order.
    """

    def __init__(
        self,
        host_registry: JavisToolRegistry,
        ctx: Any,
        *,
        sub_agent_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._host = host_registry
        self._ctx = ctx
        self._sub_agent_factory = sub_agent_factory

    def register(self, javis_tool: JavisTool) -> Any:
        """Register into the host registry (the single source of truth)."""
        return self._host.register(javis_tool)

    def get(self, name: str) -> CoreTool | None:
        javis_tool = self._host.get(name)
        return None if javis_tool is None else self._adapt(javis_tool)

    def all(self) -> list[CoreTool]:
        return [self._adapt(javis_tool) for javis_tool in self._host.all()]

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema for tool in self.all()]

    def execution_mode(self, name: str) -> ExclusiveMode | ParallelMode:
        javis_tool = self._host.get(name)
        if javis_tool is not None and getattr(javis_tool, "exclusive", False):
            return ExclusiveMode()
        return ParallelMode()

    def _adapt(self, javis_tool: JavisTool) -> CoreTool:
        return adapt_tool(javis_tool, sub_agent_factory=self._sub_agent_factory)
```

import 段改为 `from .types import ExclusiveMode, ParallelMode, ToolExecutionResult, ToolSchema`（`ToolExecutionResult` 已有，新增三个）；`__all__ = ["AgentToolView", "adapt_tool"]`。文件 docstring 的 `- ``AgentTool`` …` 段落补充视图说明。`javis/harness/__init__.py:48` 的 `from .tool_adapter import adapt_registry, adapt_tool` 改 `AgentToolView, adapt_tool`，`:56` 的 `"adapt_registry"` 原地改 `"AgentToolView"`（Task 8 会整体重写该列表，此处不做重排）。

- [ ] **Step 4: 跑视图测试**

Run: `timeout 120 uv run pytest tests/test_harness/test_tool_view.py -q`
Expected: `2 passed`

- [ ] **Step 5: 核心改读 `agentTools`**

- `javis/harness/tools.py`：顶部 `from javis.contracts.services import AGENT_TOOLS_SERVICE`；`:196` 与 `:255` 的 `ctx.get("tools")` → `ctx.get(AGENT_TOOLS_SERVICE)`；docstring `:6/:73` 的服务名改 `"agentTools"`。
- `javis/harness/prompt.py:43`：`self._ctx.get("agentTools")`。
- `javis/harness/engine.py`：`from javis.contracts.services import AGENT_TOOLS_SERVICE`；删除 `adapt_registry` 与 `CoreToolRegistry` 相关装配，改为

```python
        if javis_tools is not None:
            self._core_tools = AgentToolView(
                javis_tools, self._loop_ctx, sub_agent_factory=self._run_sub_agent
            )
        else:
            self._core_tools = AgentToolView(
                JavisToolRegistry(), self._loop_ctx, sub_agent_factory=self._run_sub_agent
            )
        self._loop_ctx.provide(AGENT_TOOLS_SERVICE, self._core_tools)
```

（`CoreToolRegistry` import 删除；`from .tool_adapter import AgentToolView`。）

这是过渡接线（服务名先对齐，保证本步全绿）；Task 6 会把这段整体移入 `agent_tools` 组合行、从 `Harness.__init__` 删除。

- [ ] **Step 6: examples/dsh_harness 对齐服务名**

- `plugins/demo_tools.py`：`ctx.provide("agentTools", registry)`（docstring 同步）。
- `plugins/system_prompt.py:59`：`self._ctx.get("agentTools")`。
- `plugins/driver.py:26`：`inject = ["llm", "agentTools", "systemPrompt", "agentLoop"]`；docstring 图示同步。
- `cordis.yml`：`driver` 条目 `inject: [llm, agentTools, systemPrompt, agentLoop]`（`:32`）。
- `README.md`：服务名说明同步 —— `:33` `provide("tools")`→`provide("agentTools")`、`:36`/`:52`/`:202`/`:246` 的 `inject=[llm, tools, …]` → `agentTools`、`:197` `provide("tools")` → `provide("agentTools")`。**不要动**：场景名（`:26`/`:66`/`:87`/`:106`）与事件名 `tools/execute`、`tools/post-execute`、`tools/result`（`:35`/`:201`/`:214`）。
- `tests/test_demo_harness.py:276`：`registry = _ctx.get("tools")` → `_ctx.get("agentTools")`（demo 注册表改名后，原查找返回 None，测试会红）。

- [ ] **Step 7: 跑全量测试**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `297 passed`（295 + 2 新增）

- [ ] **Step 8: 提交**

```bash
git add -A javis tests examples
git commit -m "refactor(harness): rename the loop tool service to agentTools and back it with a live host view"
```

---

### Task 5: 装载器入口断言

`settle()`（`javis/cordis/registry.py:185-209`）用 `return_exceptions=True` 收集 fiber inertia，**FAILED 与 PENDING 都被静默吞掉**。补 dsh `assertEntriesLoaded`/`assertEntriesActivated` 的等价物。

**Files:**
- Modify: `javis/cordis/fiber.py`（新增两个访问器）
- Modify: `javis/cordis/loader/__init__.py`（新增 `assert_entries_settled`，加入 `__all__` 若无）
- Test: `tests/test_cordis/test_loader_assert.py`（新增）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cordis/test_loader_assert.py`：

```python
"""``assert_entries_settled``: fail loud on FAILED / PENDING composition rows."""

from __future__ import annotations

import pytest

from javis.cordis import Context
from javis.cordis.loader import Loader, assert_entries_settled
from javis.cordis.registry import settle


def _write(tmp_path, body: str):
    path = tmp_path / "cordis.yml"
    path.write_text(body, encoding="utf-8")
    return path


async def _boot(tmp_path, body: str) -> Context:
    ctx = Context()
    fiber = ctx.plugin(Loader, {"file": str(_write(tmp_path, body))})
    await fiber
    await settle(ctx)
    return ctx


@pytest.mark.asyncio
async def test_active_entries_pass(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("def apply(ctx):\n    ctx.provide('thing', 1)\n", encoding="utf-8")
    ctx = await _boot(tmp_path, "- id: ok\n  name: ./ok.py\n")
    assert_entries_settled(ctx)  # does not raise
    assert ctx.get("thing") == 1


@pytest.mark.asyncio
async def test_failed_entry_reports_original_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def apply(ctx):\n    raise ValueError('boom')\n", encoding="utf-8")
    ctx = await _boot(tmp_path, "- id: bad\n  name: ./bad.py\n")
    with pytest.raises(RuntimeError, match=r"'bad'.*boom"):
        assert_entries_settled(ctx)


@pytest.mark.asyncio
async def test_pending_entry_lists_missing_services(tmp_path):
    late = tmp_path / "late.py"
    late.write_text("def apply(ctx):\n    pass\n", encoding="utf-8")
    ctx = await _boot(
        tmp_path,
        "- id: late\n  name: ./late.py\n  inject: [notARealService]\n",
    )
    with pytest.raises(RuntimeError, match="notARealService"):
        assert_entries_settled(ctx)
```

- [ ] **Step 2: 运行确认失败**

Run: `timeout 120 uv run pytest tests/test_cordis/test_loader_assert.py -q`
Expected: FAIL — `ImportError: cannot import name 'assert_entries_settled'`

- [ ] **Step 3: 加 `Fiber` 访问器**

`javis/cordis/fiber.py`，在 `assertActive` 之后插入：

```python
    @property
    def error(self) -> BaseException | None:
        """The exception that failed this fiber's last load (``None`` if none)."""
        return self._error

    def missing_inject(self) -> list[str]:
        """Requested service names this fiber has not resolved yet."""
        return [name for name in self.inject if self._store.get(name) is None]
```

- [ ] **Step 4: 加 `assert_entries_settled`**

`javis/cordis/loader/__init__.py`，import 段加 `from ..fiber import FiberState`；在 `Loader` 类之后追加：

```python
def assert_entries_settled(ctx: "Context") -> None:
    """Fail loud when any composition entry did not activate.

    ``settle()`` gathers fiber inertia with ``return_exceptions=True``, so a
    FAILED plugin body — or a PENDING fiber still waiting on services that
    will never appear — would otherwise be silently ignored. Call this right
    after ``settle(ctx)`` as the boot-time entry assertion (dsh
    ``assertEntriesLoaded`` / ``assertEntriesActivated``).

    Group members are mounted as fibers without an entry of their own, so the
    scan walks the mounted fibers first; entries with no fiber at all are
    reported afterwards.
    """
    loader = ctx.get("loader")
    if loader is None:
        raise RuntimeError("composition loader is not available (loader service missing)")
    entries = loader.entries()
    fibers = loader.fibers()

    for entry_id, fiber in fibers.items():
        entry = entries.get(entry_id)
        if entry is not None and entry.disabled:
            continue
        if fiber.state is FiberState.ACTIVE:
            continue
        label = entry.name if entry is not None else fiber.name
        if fiber.state is FiberState.FAILED:
            raise RuntimeError(
                f"composition entry {entry_id!r} ({label}) failed: "
                f"{type(fiber.error).__name__}: {fiber.error}"
            ) from fiber.error
        if fiber.state is FiberState.PENDING:
            missing = fiber.missing_inject()
            raise RuntimeError(
                f"composition entry {entry_id!r} ({label}) is PENDING: "
                f"unresolved services: {', '.join(missing) or '(unknown)'}"
            )
        raise RuntimeError(
            f"composition entry {entry_id!r} ({label}) is {fiber.state.name} (not settled)"
        )

    for entry_id, entry in entries.items():
        if entry.disabled or entry_id in fibers:
            continue
        raise RuntimeError(
            f"composition entry {entry_id!r} ({entry.name}) was not mounted"
        )
```

> 实现后按质量评审补强：主循环遍历 `fibers()`（覆盖 group 成员——它们不在
> `entries()` 里，这是初版会静默放过的失败类别），FAILED 消息带异常类型，
> PENDING 与 LOADING/UNLOADING/DISPOSED 分开措辞。测试共 5 个：
> 原有的 3 个加 `test_failed_group_member_is_reported` 与
> `test_missing_loader_service_raises`。

若文件有 `__all__` 则加入 `"assert_entries_settled"`（当前无 `__all__`，可跳过）。

- [ ] **Step 5: 跑测试**

Run: `timeout 300 uv run pytest tests/test_cordis -q`
Expected: 全绿（含 5 个新测试）；全量 `timeout 300 uv run pytest tests/ -q` → 303 passed

- [ ] **Step 6: 提交**

```bash
git add javis/cordis tests/test_cordis
git commit -m "feat(cordis): add boot-time composition entry assertion"
```

---

### Task 6: 组合行 + `Harness` 去私有装配 + `build_runtime` = boot + 断言（原子翻转）

本任务是整个重构的落点，一次性完成：新增六条组合行、`Harness` 从根 context 读服务、默认组合全量写入、`build_runtime` 收敛、删除 `_build_default_engine` 与 `build.py`、测试同步。中间态无法保持全绿（旧的兜底路径被删除），因此作为一个提交。

**Files:**
- Create: `javis/harness/plugins/__init__.py`、`llm.py`、`agent_tools.py`、`system_prompt.py`、`agent_loop.py`、`snip.py`、`harness.py`
- Rename+Rewrite: `javis/harness/engine.py` → `javis/harness/harness.py`
- Delete: `javis/harness/build.py`
- Modify: `javis/harness/__init__.py`
- Modify: `javis/session/config.py`
- Modify: `javis/app/runtime.py`
- Modify: `tests/test_javis/conftest.py`、`tests/test_javis/test_plugin_runtime.py`
- Create: `tests/test_harness/support.py`
- Modify: `tests/test_harness/test_engine.py`、`tests/test_harness/test_agent_loop.py`

- [ ] **Step 1: 先写组合行的目录与 `llm` 行**

`javis/harness/plugins/__init__.py`：

```python
"""Harness composition rows — the only place the harness is assembled.

Each module is a Cordis plugin (``apply(ctx, config)``) addressing one
service (module name == service name, except ``snip`` which only registers
a ``tools/post-execute`` listener):

- ``llm``            → ``llm`` (``javis.llm.LlmRuntime`` + provider adapter)
- ``agent_tools``    → ``agentTools`` (live view over the host ``tools``)
- ``system_prompt``  → ``systemPrompt``
- ``agent_loop``     → ``agentLoop`` (loop config, incl. history compression)
- ``snip``           → tool-output truncation middleware (no service)
- ``harness``        → ``harness`` (Session + AgentLoop + Harness shell)

Rows declare their dependencies with module-level ``inject``; the Cordis
loader activates them only once every listed service is ACTIVE.
"""
```

`javis/harness/plugins/llm.py`：

```python
"""组合行：``llm`` 服务 —— provider adapter 注册与路由解析。

Config resolution (provider / model / api-key / max-tokens) lives here now:
the row reads the ``config`` service and the ``host`` per-session facts
(CLI ``--model`` override included) and registers an ``OpenAICompatAdapter``
under the resolved provider route.
"""

from __future__ import annotations

from typing import Any

from javis.contracts.services import CONFIG_SERVICE, HOST_SERVICE
from javis.llm import LlmRuntime, OpenAICompatAdapter
from javis.session.config import resolve_provider_and_model
from javis.session.credentials import resolve_api_key

name = "javis.harness.plugins.llm"
inject = [CONFIG_SERVICE, HOST_SERVICE]


def build_runtime(ctx: Any) -> LlmRuntime:
    """Resolve the route and register the adapter (``llm`` service)."""
    cfg = ctx.get(CONFIG_SERVICE)
    host = ctx.get(HOST_SERVICE)
    provider_name, model_id = resolve_provider_and_model(cfg, cli_model=host.model_override)
    provider_cfg = cfg.providers[provider_name]
    api_key = resolve_api_key(
        provider_name,
        provider_cfg.api_key_env,
        provider_cfg.api_key,
        workspace=host.workspace,
        cwd=host.cwd,
    )
    max_tokens = next(
        (m.max_tokens for m in provider_cfg.models if m.id == model_id),
        None,
    )
    adapter_kwargs: dict[str, Any] = {
        "model": model_id,
        "api_key": api_key or "",
        "base_url": provider_cfg.base_url,
    }
    if max_tokens is not None:
        adapter_kwargs["max_tokens"] = max_tokens
    # LlmRuntime's constructor auto-provides the "llm" service (Service base).
    runtime = LlmRuntime(ctx)
    runtime.register_adapter([provider_name], OpenAICompatAdapter(**adapter_kwargs))
    return runtime


def apply(ctx):
    build_runtime(ctx)
```

- [ ] **Step 2: 写 `agent_tools` / `system_prompt` / `agent_loop` / `snip` 行**

`javis/harness/plugins/agent_tools.py`：

```python
"""组合行：``agentTools`` 服务 —— 宿主 ``tools`` 的循环侧实时视图。

The sub-agent factory resolves the ``harness`` service lazily (at tool-call
time) because this row loads *before* the ``harness`` row it belongs to.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from javis.contracts.services import (
    AGENT_TOOLS_SERVICE,
    HARNESS_SERVICE,
    TOOLS_SERVICE,
)

from ..tool_adapter import AgentToolView

name = "javis.harness.plugins.agent_tools"
inject = [TOOLS_SERVICE]


def _sub_agent_factory(ctx: Any) -> Callable[[str], str]:
    def factory(task: str) -> str:
        harness = ctx.get(HARNESS_SERVICE)
        if harness is None:
            return "Error: agent tool unavailable (no harness service)"
        return harness.run_sub_agent(task)

    return factory


def apply(ctx):
    ctx.provide(
        AGENT_TOOLS_SERVICE,
        AgentToolView(
            ctx.get(TOOLS_SERVICE),
            ctx,
            sub_agent_factory=_sub_agent_factory(ctx),
        ),
    )
```

`javis/harness/plugins/system_prompt.py`：

```python
"""组合行：``systemPrompt`` 服务 —— persona + 每步 context + 工具 schema。"""

from __future__ import annotations

from javis.contracts.services import (
    AGENT_TOOLS_SERVICE,
    CONFIG_SERVICE,
    HOST_SERVICE,
    SYSTEM_PROMPT_SERVICE,
)

from ..prompt import HarnessPromptService

name = "javis.harness.plugins.system_prompt"
inject = [CONFIG_SERVICE, HOST_SERVICE, AGENT_TOOLS_SERVICE]


def apply(ctx):
    host = ctx.get(HOST_SERVICE)
    ctx.provide(
        SYSTEM_PROMPT_SERVICE,
        HarnessPromptService(
            ctx,
            host.system_prompt,
            cwd=host.cwd,
            workspace=host.workspace,
            session_id=host.session_id,
        ),
    )
```

（``config`` 保留在 ``inject`` 里与已评审 spec 的组合文件一致。）

`javis/harness/plugins/agent_loop.py`：

```python
"""组合行：``agentLoop`` 服务 —— 循环驱动器配置（含历史压缩）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from javis.contracts.services import AGENT_LOOP_SERVICE

from ..compression import HISTORY_MAX_MESSAGES, HistoryCompressor
from ..types import AgentLoopService

name = "javis.harness.plugins.agent_loop"


class MutableLoopConfig:
    """Mutable stand-in for the frozen ``AgentLoopConfig`` dataclass.

    ``Harness.set_max_turns`` mutates ``max_steps_per_turn`` live; the loop
    reads attributes via ``getattr`` so any object shape works.
    """

    def __init__(
        self,
        *,
        max_parallel_tool_calls: int,
        max_steps_per_turn: int,
        history_compressor: Any = None,
    ) -> None:
        self.max_parallel_tool_calls = max(1, int(max_parallel_tool_calls))
        self.max_steps_per_turn = max(1, int(max_steps_per_turn))
        self.history_compressor = history_compressor


class Config(BaseModel):
    """Row config; both ``maxParallelToolCalls`` and snake_case are accepted."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    max_parallel_tool_calls: int = 4
    max_steps_per_turn: int = 20
    history_max_messages: int = HISTORY_MAX_MESSAGES


def apply(ctx, config):
    ctx.provide(
        AGENT_LOOP_SERVICE,
        AgentLoopService(
            MutableLoopConfig(
                max_parallel_tool_calls=config.max_parallel_tool_calls,
                max_steps_per_turn=config.max_steps_per_turn,
                history_compressor=HistoryCompressor(config.history_max_messages),
            )
        ),
    )
```

`javis/harness/plugins/snip.py`：

```python
"""组合行：工具输出截断中间件（``tools/post-execute``），不 provide 服务。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ..compression import MAX_TOOL_OUTPUT_CHARS, make_snip_listener
from ..types import Events

name = "javis.harness.plugins.snip"


class Config(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    tool_output_max_chars: int = MAX_TOOL_OUTPUT_CHARS


def apply(ctx, config):
    ctx.on(Events.TOOLS_POST_EXECUTE, make_snip_listener(config.tool_output_max_chars))
```

- [ ] **Step 3: 写 `harness` 驱动行**

`javis/harness/plugins/harness.py`：

```python
"""组合行：``harness`` 服务 —— 驱动（Session + AgentLoop + Harness 外壳）。

This row is the composition root: it resolves the per-session route from
``config`` + ``host`` and constructs the ``Harness`` shell. Replacing the
built-in harness means replacing *this row* (point ``name`` at your own
driver plugin, or ``disabled: true`` plus your own row) — the ``harness``
service cannot be provided twice, so selection is replacement.

``build_harness`` is the single seam tests patch to inject a fake harness.
"""

from __future__ import annotations

from typing import Any

from javis.contracts.services import (
    AGENT_LOOP_SERVICE,
    AGENT_TOOLS_SERVICE,
    CONFIG_SERVICE,
    HARNESS_SERVICE,
    HOST_SERVICE,
    LLM_SERVICE,
    SYSTEM_PROMPT_SERVICE,
)
from javis.session.config import resolve_provider_and_model

from ..harness import Harness

name = "javis.harness.plugins.harness"
inject = [
    LLM_SERVICE,
    AGENT_TOOLS_SERVICE,
    SYSTEM_PROMPT_SERVICE,
    AGENT_LOOP_SERVICE,
    CONFIG_SERVICE,
    HOST_SERVICE,
]


def build_harness(ctx: Any) -> Harness:
    """Resolve host facts and construct the harness shell."""
    cfg = ctx.get(CONFIG_SERVICE)
    host = ctx.get(HOST_SERVICE)
    provider_name, model_id = resolve_provider_and_model(cfg, cli_model=host.model_override)
    max_turns = host.max_turns_override
    if max_turns is None and cfg.session.max_turns is not None:
        max_turns = cfg.session.max_turns
    return Harness(
        ctx,
        provider_name=provider_name,
        model=model_id,
        system_prompt=host.system_prompt,
        cwd=host.cwd,
        workspace=host.workspace,
        session_id=host.session_id,
        max_turns=max_turns,
        tool_metadata=host.tool_metadata,
    )


def apply(ctx):
    ctx.provide(HARNESS_SERVICE, build_harness(ctx))
```

- [ ] **Step 4: 改名并重写 `Harness` 实现**

Run: `git mv javis/harness/engine.py javis/harness/harness.py`

模块 docstring 改为：

```python
"""Harness — the javis-side harness over the dsh-style ``AgentLoop``.

``Harness`` implements :class:`javis.contracts.harness.Harness` (the host's
single seam): it owns the javis conversation mirror (``ConversationMessage``),
accumulates usage, and yields ``AgentEvent`` streams per turn — driven by the
dsh-style loop in ``javis.harness.agent`` (phase state machine, inbox, session
event log, exclusive/parallel tool scheduling, ``agent/*`` waterfalls).

Assembly (mirrors the demo's ``driver`` plugin):

- every service the loop needs comes from the root context and was provided by
  its own composition row — ``llm`` (``javis.llm.LlmRuntime`` adapter
  registry), ``agentTools`` (the live view over the host tool registry),
  ``systemPrompt``, ``agentLoop``. ``Harness`` builds nothing privately;
  ``javis.harness.plugins.harness`` is the row that constructs it.
- middleware registered on this context: ``tools/execute`` permission checker
  (``Harness.set_permission_checker``), ``agent/request`` model routing so
  ``set_model`` takes effect, ``agent/limit`` max-steps status. Tool-output
  snip lives in its own ``snip`` row.

The turn bridge maps the session event log to ``AgentEvent`` (text/reasoning
deltas, tool start/result, turn end with per-turn usage) and maintains the
javis message mirror (user / tool results as user messages / assistant with
tool uses) so session save/restore round-trips.
"""
```

import 段调整：

```python
from javis.contracts.harness import Harness as HarnessContract
from javis.contracts.services import AGENT_LOOP_SERVICE, SYSTEM_PROMPT_SERVICE
...
from .agent import AgentLoop
from .compression import HISTORY_MAX_MESSAGES  # 仅 _reset_session 不再用则删除
from .session import Session
from .types import AgentOptions, Events, ReasoningDeltaChunk, SessionEvents, TextDeltaChunk, ToolCallBlock, ToolExecutionResult
```

删除的 import：`LlmRuntime`、`HarnessPromptService`、`adapt_registry`、`make_snip_listener`、`HistoryCompressor`、`CoreToolRegistry`、`JavisToolRegistry`（若不再用）、`ToolExecutionResult`（若仅权限监听器用则保留）。

`__init__` 替换为：

```python
class Harness(HarnessContract):
    """javis-side harness over a dsh-style ``AgentLoop``."""

    def __init__(
        self,
        ctx: Context,
        *,
        provider_name: str,
        model: str,
        system_prompt: str = "",
        cwd: str | Path = "",
        workspace: str | Path = "",
        session_id: str = "",
        max_turns: int | None = None,
        tool_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._ctx = ctx
        self._provider_name = provider_name
        self._model = model
        self._system_prompt = system_prompt
        self._cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
        self._workspace = str(Path(workspace).expanduser().resolve()) if workspace else self._cwd
        self._session_id = session_id
        self._max_turns = None if max_turns is None else max(1, int(max_turns))
        self._tool_metadata = dict(tool_metadata or {})
        self._effort: str | None = None
        self._usage = UsageSnapshot()
        self._permission_checker: Any = None
        self._messages: list[ConversationMessage] = []
        self._call_names: dict[str, str] = {}
        self._last_limit: dict[str, Any] | None = None
        self._sub_depth = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._append_event: asyncio.Event | None = None

        # -- services from the root context (provided by composition rows) ---
        self._prompt_service = ctx.get(SYSTEM_PROMPT_SERVICE)
        loop_service = ctx.get(AGENT_LOOP_SERVICE)
        self._loop_config = getattr(loop_service, "config", None) or loop_service
        self._default_max_steps = max(1, int(getattr(self._loop_config, "max_steps_per_turn", 20)))
        # ctor-level max_turns wins over the row's max_steps_per_turn (parity
        # with the old ``HarnessEngine`` and ``build()`` path).
        self._loop_config.max_steps_per_turn = (
            self._max_turns if self._max_turns is not None else self._default_max_steps
        )

        # -- middleware on the harness's own context -------------------------
        ctx.on(Events.TOOLS_EXECUTE, self._permission_listener)
        ctx.on(Events.AGENT_REQUEST, self._request_middleware)
        ctx.on(Events.AGENT_LIMIT, self._on_agent_limit)

        self._reset_session()
```

`_reset_session` 改用 `AgentLoop`：

```python
        self._agent = AgentLoop(
            self._ctx,
            self._session_id,
            AgentOptions(provider=self._provider_name or "javis", model=self._model),
            self._session,
        )
```

`set_model` 删除 `self._adapter.set_model(model)` 一行（路由经 `agent/request` → `config.model` → adapter 的 `options.model or self.model`，行为等价）。

`set_max_turns` 保持：

```python
        self._loop_config.max_steps_per_turn = (
            self._max_turns if self._max_turns is not None else self._default_max_steps
        )
```

`_run_sub_agent` 改名 `run_sub_agent`（公开，供 `agent_tools` 行调用），`_run_sub_agent_async` 里的 `ReactAgentLoop` 构造改 `AgentLoop`，`_loop_ctx` 改 `self._ctx`。`__all__ = ["Harness"]`。

同时整体删除（都已搬去插件行 / 不再被本类使用）：
- 类 `_MutableLoopConfig`（`agent_loop` 行自带同名类）；
- `self._core_tools`（视图由 `agent_tools` 行提供，`__init__` 里不再赋值）；
- import：`javis.llm.LlmRuntime`、`.prompt.HarnessPromptService`、`.tool_adapter.adapt_registry`、`.tools.ToolRegistry as CoreToolRegistry`、`.compression` 全部（`HISTORY_MAX_MESSAGES` / `MAX_TOOL_OUTPUT_CHARS` / `HistoryCompressor` / `make_snip_listener`）；
- `javis.contracts.tools.ToolRegistry as JavisToolRegistry` 与 `javis.contracts.services.AGENT_TOOLS_SERVICE`（本类不再直接触碰工具注册表）。

- [ ] **Step 5: 删除 `build.py`、更新 `__init__.py`、删除 `_build_default_engine`**

Run: `git rm javis/harness/build.py`

`javis/harness/__init__.py` 必须同步最小改动（否则 `from .build import build` / `from .engine import ...` 直接 ImportError，全仓 import 失败）：

```python
from . import agent as agent
from . import inbox as inbox
from . import session as session
from . import stream as stream    # Task 2 已改
from . import tools as tools
from . import types as types
from .build import build          # → 删除
from .compression import HistoryCompressor, make_snip_listener
from .engine import HarnessEngine # → 改为: from .harness import Harness
from .tool_adapter import adapt_registry, adapt_tool  # → 改为: from .tool_adapter import AgentToolView, adapt_tool
```

`__all__` 相应把 `"HarnessEngine"`/`"build"`/`"adapt_registry"` 换成 `"Harness"`/`"AgentToolView"`（完整版在 Task 8 Step 1 重写）。

`javis/app/runtime.py`：
- 删除整个 `_build_default_engine`（`:111-164`）；
- 删除只服务于它的局部 import（`javis.harness.build` / `resolve_provider_and_model` / `resolve_api_key`）；
- `build_system_prompt` 里 "backed by an ``AgentEngine`` implementation" 一句改为 "backed by the Harness contract"（Task 8 的 grep 也会扫到，这里顺手改掉）。

- [ ] **Step 6: `ensure_default_composition` 写全量组合**

`javis/session/config.py`，在 `ensure_default_composition` 之前加常量：

```python
#: Default composition: the harness is assembled by these rows and nothing
#: else. Written when ``<workspace>/cordis.yml`` is missing; an existing file
#: (even an empty one) is never rewritten — an empty composition now fails
#: loudly at boot instead of silently falling back to a built-in engine.
DEFAULT_COMPOSITION = """\
- id: llm
  name: javis.harness.plugins.llm
  inject: [config, host]
- id: agent-tools
  name: javis.harness.plugins.agent_tools
  inject: [tools]
- id: system-prompt
  name: javis.harness.plugins.system_prompt
  inject: [config, host, agentTools]
- id: agent-loop
  name: javis.harness.plugins.agent_loop
  config: {maxParallelToolCalls: 4, maxStepsPerTurn: 20}
- id: snip
  name: javis.harness.plugins.snip
  config: {toolOutputMaxChars: 8000}
- id: harness
  name: javis.harness.plugins.harness
  inject: [llm, agentTools, systemPrompt, agentLoop, config, host]
"""
```

`ensure_default_composition` 改为：

```python
def ensure_default_composition(workspace: str | Path | None = None) -> Path:
    """Create ``<workspace>/cordis.yml`` with the full default composition."""
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    path = root / COMPOSITION_FILENAME
    if not path.exists():
        path.write_text(DEFAULT_COMPOSITION, encoding="utf-8")
        log.info("Created default plugin composition at %s", path)
    return path
```

`__all__` 加 `"DEFAULT_COMPOSITION"`。

- [ ] **Step 7: `build_runtime` 收敛为 boot + 断言**

`javis/app/runtime.py` 中 `:240-270` 段替换为：

```python
    loader_fiber = ctx.plugin(Loader, {"file": str(composition)})
    try:
        await loader_fiber
    except BaseException as exc:
        log.exception("Plugin composition %s failed to load", composition)
        raise RuntimeError(
            f"plugin composition {composition} failed to load: {exc}"
        ) from exc
    await settle(ctx)
    assert_entries_settled(ctx)

    engine_obj = ctx.get(HARNESS_SERVICE)
    if not isinstance(engine_obj, Harness):
        got = "no service" if engine_obj is None else type(engine_obj).__name__
        raise RuntimeError(
            f"composition {composition} provides no 'harness' service ({got}). "
            "Add a row 'name: javis.harness.plugins.harness' with "
            "'inject: [llm, agentTools, systemPrompt, agentLoop, config, host]', "
            "or delete the composition file to regenerate the default one."
        )
    # Explicit CLI overrides win over the row's resolved defaults.
    if model is not None:
        engine_obj.set_model(model)
    if system_prompt is not None:
        engine_obj.set_system_prompt(system_prompt)

    model_name = model or engine_obj.model or "unknown"
```

（变量名保持 `engine_obj` —— 函数末尾的 `restore_messages` / `RuntimeBundle(engine=...)` 都引用它，不要改名。）

import 段：`from javis.cordis.loader import Loader, assert_entries_settled`；`from javis.contracts.services import ... HARNESS_SERVICE ...`。

`build_runtime` docstring 的兜底描述改写为"组合缺失即报错"，并删除 `RuntimeBundle.engine` 上方的 `AgentEngine` 表述（Task 1 已改类型）。

- [ ] **Step 8: 重写测试 seam**

`tests/test_javis/conftest.py`：

```python
"""Shared fixtures for the javis test suite."""

from __future__ import annotations

import pytest

from javis.contracts.harness import Harness
from tests.test_javis.fake_backend import FakeEngine


@pytest.fixture
def fake_engine_factory(monkeypatch):
    """Route the ``harness`` composition row's construction to a test double.

    The built-in harness is built by ``javis.harness.plugins.harness``; the
    row loads through the dotted module name, so patching the module-level
    ``build_harness`` swaps the harness for every composition in the suite
    (including the default one).

    Usage::

        engine = fake_engine_factory()                 # plain FakeEngine
        engine = fake_engine_factory(RecordingEngine())  # custom double
        bundle = await build_runtime(cwd=..., ...)
    """

    def _patch(engine: Harness | None = None) -> FakeEngine:
        impl = engine if engine is not None else FakeEngine()
        monkeypatch.setattr(
            "javis.harness.plugins.harness.build_harness", lambda *_a, **_k: impl
        )
        return impl

    return _patch
```

注意：只 patch `build_harness` 就够了 —— 默认组合里的 `llm` 行会真实运行，但 `DEFAULT_TEMPLATE` 保证 `resolve_provider_and_model` 能解析出 `deepseek/deepseek-chat`，且 `OpenAICompatAdapter` 的 SDK 客户端是惰性构造（不校验/不联网），所以该行能正常 ACTIVE，无需再 patch。

- [ ] **Step 9: 重写 `tests/test_javis/test_plugin_runtime.py`**

保留文件顶部的 `ENGINE_PLUGIN` / `EXTRA_TOOLS_PLUGIN` 插件源码（`ENGINE_PLUGIN` 里 `ctx.provide(HARNESS_SERVICE, PluginEngine())` —— Task 1 已改），删除 `BAD_ENGINE_PLUGIN`，并按下列测试集重写测试体：

```python
@pytest.mark.asyncio
async def test_plugin_harness_provides_instance(plugin_workspace, fake_engine_factory):
    """A composition row that provides ``harness`` replaces the built-in one
    and sees the host services (config / tools / host) inside ``apply``."""
    fake_engine_factory()  # proves the built-in harness is NOT used
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "harness", "name": "./engine_plugin.py", "inject": ["config", "tools", "host"]},
    ])

    bundle = await build_runtime(cwd=str(plugin_workspace.parent))

    assert type(bundle.engine).__name__ == "PluginEngine"
    seen = _seen(plugin_workspace)
    assert seen["has_config"] == "JavisConfig"
    assert seen["session_id"] == bundle.session_id
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "agent"} <= set(seen["tool_names"])
    await bundle.close()


@pytest.mark.asyncio
async def test_missing_composition_writes_full_default(plugin_workspace, fake_engine_factory):
    """No composition → the full six-row default composition is written, every
    row activates, and the harness service is the patched test double."""
    from javis.session.config import DEFAULT_COMPOSITION

    fake_engine_factory()
    bundle = await build_runtime(cwd=str(plugin_workspace.parent))

    assert isinstance(bundle.engine, FakeEngine)
    composition = (plugin_workspace / "cordis.yml").read_text(encoding="utf-8")
    assert composition == DEFAULT_COMPOSITION
    assert bundle.context is not None
    assert bundle.context.get("agentTools") is not None
    assert bundle.context.get("systemPrompt") is not None
    assert bundle.context.get("agentLoop") is not None
    assert bundle.context.get("llm") is not None
    await bundle.close()


@pytest.mark.asyncio
async def test_composition_without_harness_row_raises(plugin_workspace, fake_engine_factory):
    """A composition that never provides ``harness`` fails loudly instead of
    silently falling back to a built-in engine."""
    fake_engine_factory()
    (plugin_workspace / "extra_tools.py").write_text(EXTRA_TOOLS_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "extra-tools", "name": "./extra_tools.py", "inject": ["tools", "commands"]},
    ])

    with pytest.raises(RuntimeError, match="provides no 'harness' service"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_entry_with_missing_dependency_raises(plugin_workspace, fake_engine_factory):
    """A row whose ``inject`` names a service nobody provides fails the boot
    assertion with the missing service name (``settle`` would swallow it)."""
    fake_engine_factory()
    write_composition(plugin_workspace, [
        {"id": "broken", "name": "./engine_plugin.py", "inject": ["nosuchservice"]},
    ])

    with pytest.raises(RuntimeError, match="nosuchservice"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_failing_entry_reports_original_error(plugin_workspace, fake_engine_factory):
    """A row whose ``apply`` raises surfaces the original exception message."""
    fake_engine_factory()
    (plugin_workspace / "boom.py").write_text(
        "def apply(ctx):\n    raise ValueError('row boom')\n", encoding="utf-8"
    )
    write_composition(plugin_workspace, [
        {"id": "boom", "name": "./boom.py"},
    ])

    with pytest.raises(RuntimeError, match="row boom"):
        await build_runtime(cwd=str(plugin_workspace.parent))
```

`test_plugin_tools_and_commands_reach_engine` 保留，并在 `await bundle.close()` 前追加实时视图断言：

```python
    assert bundle.context is not None
    view = bundle.context.get("agentTools")
    assert view.get("hello_tool") is not None
    assert "hello_tool" in {schema.name for schema in view.schemas()}
```

（`agent-tools` 行在组合里排在 `extra-tools` 之前，视图先建、工具后注册 —— 正是"后注册可见"的集成证据。）

`test_close_disposes_plugins_and_revokes_engine`：`ctx.get(ENGINE_SERVICE)` → `ctx.get(HARNESS_SERVICE)`，测试名改 `test_close_disposes_plugins_and_revokes_harness`。

`test_explicit_composition_path`：把 `comp.write_text("[]\n")` 改为写 `DEFAULT_COMPOSITION`；其余断言不变。

删除 `test_invalid_engine_service_falls_back`。该测试是文件里 `import logging` 的唯一使用者，删掉它同时删掉这行 import（否则 ruff F401）。

- [ ] **Step 10: 重写 harness 测试的构造入口**

创建 `tests/test_harness/support.py`：

```python
"""Shared builder: a root context with the four loop services + a Harness."""

from __future__ import annotations

from typing import Any

from javis.contracts.services import (
    AGENT_LOOP_SERVICE,
    AGENT_TOOLS_SERVICE,
    SYSTEM_PROMPT_SERVICE,
    TOOLS_SERVICE,
)
from javis.cordis import Context
from javis.harness.compression import HISTORY_MAX_MESSAGES, HistoryCompressor
from javis.harness.harness import Harness
from javis.harness.plugins.agent_loop import MutableLoopConfig
from javis.harness.prompt import HarnessPromptService
from javis.harness.tool_adapter import AgentToolView
from javis.harness.types import AgentLoopService
from javis.llm import LlmRuntime, ScriptedAdapter
from javis.tools import create_default_tool_registry


def make_harness(
    script: list[Any],
    *,
    tools: Any = None,
    system_prompt: str = "test prompt",
    max_steps_per_turn: int = 20,
    **kwargs: Any,
) -> Harness:
    """Build a Harness over a root context (same wiring the rows perform)."""
    ctx = Context()
    runtime = LlmRuntime(ctx)
    runtime.register_adapter(["scripted"], ScriptedAdapter(script=script))
    host_tools = tools if tools is not None else create_default_tool_registry()
    ctx.provide(TOOLS_SERVICE, host_tools)
    ctx.provide(AGENT_TOOLS_SERVICE, AgentToolView(host_tools, ctx))
    ctx.provide(
        SYSTEM_PROMPT_SERVICE,
        HarnessPromptService(ctx, system_prompt, cwd="/tmp", workspace="/tmp", session_id="sess"),
    )
    ctx.provide(
        AGENT_LOOP_SERVICE,
        AgentLoopService(
            MutableLoopConfig(
                max_parallel_tool_calls=4,
                max_steps_per_turn=max_steps_per_turn,
                history_compressor=HistoryCompressor(HISTORY_MAX_MESSAGES),
            )
        ),
    )
    return Harness(
        ctx,
        provider_name="scripted",
        model="scripted-demo",
        system_prompt=system_prompt,
        cwd="/tmp",
        workspace="/tmp",
        session_id="sess",
        **kwargs,
    )
```

`tests/test_harness/test_engine.py`：

- 删除 `_engine`，`from tests.test_harness.support import make_harness`，调用点全部 `_engine(x)` → `make_harness(x)`。
- `test_setters` 中 `assert engine._adapter.model == "other-model"` 删除（adapter 已不在 Harness 手中）—— 改为断言路由生效：`assert engine.model == "other-model"`（已有）。
- `test_initial_state` / 其余断言不变（`engine._session`、`engine._loop_config` 仍是实现细节，保留）。
- import：`from javis.harness.harness import Harness`、`from javis.harness.stream import chunk_response`、`from tests.test_harness.support import make_harness`；删除随 `_engine` 一起失效的 `from javis.llm import ScriptedAdapter` 与 `from javis.tools import create_default_tool_registry`（否则 ruff F401）。

`tests/test_harness/test_agent_loop.py`：

- `_make_engine(script, **kwargs)` → `make_harness(script, **kwargs)`（`max_steps_per_turn=2` 已是 helper 的命名参数）。
- `test_default_tools_include_core_set`：

```python
    engine = _make_engine([_resp(content="ok")])
    names = {tool.name for tool in engine._ctx.get("agentTools").all()}
    assert {"read_file", "write_file", "edit_file", "bash", "glob", "grep", "agent"} <= names
```

- `HarnessEngine` 类型标注 → `Harness`。import 改为 `from javis.harness.harness import Harness` 并新增 `from tests.test_harness.support import make_harness`；删除失效的 `from javis.harness.engine import HarnessEngine`、`from javis.llm import ScriptedAdapter`、`from javis.tools import create_default_tool_registry`（`from javis.harness.stream import chunk_response` 仍被 `_resp` 使用，保留）。

`tests/test_javis/test_runtime.py`（`test_build_javis_runtime_default_engine_is_harness`，`:105-116`）：

```python
@pytest.mark.asyncio
async def test_build_javis_runtime_default_engine_is_harness(isolated_env, monkeypatch):
    from javis.harness.agent import AgentLoop
    from javis.harness.harness import Harness

    # The installed openai SDK refuses to construct a client without a
    # non-empty api_key (it validates credentials eagerly). The fixture already
    # stripped proxy vars; supply a dummy key so AsyncOpenAI can build.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    bundle = await build_runtime(cwd=str(isolated_env))
    assert isinstance(bundle.engine, Harness)
    assert isinstance(bundle.engine.agent, AgentLoop)
```

这条测试**不 patch 任何 seam**：它走默认组合的六行真实装配（`DEFAULT_TEMPLATE` 保证有 deepseek/deepseek-chat 可解析，adapter 是惰性客户端），是本重构最重要的端到端证据。

- [ ] **Step 11: 跑全量测试**

Run: `timeout 300 uv run pytest tests/ -q`
Expected: `300 passed` 左右（295 − 删除的 `test_invalid_engine_service_falls_back` + 新增 5 条插件运行时测试；实时视图 2 条已在 Task 4）。

若失败，重点排查：
- `RuntimeError: composition ... provides no 'harness' service` → 组合行 `inject` 里的服务名与实际 provide 名不一致；
- `unresolved services: ...` → 某行模块级 `inject` 与组合 YAML 的 `inject` 不一致，或 row 模块 import 失败（fiber FAILED）；
- `duplicate provide` → 某行没有 `ctx.effect` 包装、或 `LlmRuntime` 之外又 provide 了 `llm`。

- [ ] **Step 12: 提交**

```bash
git add -A javis tests
git commit -m "feat(harness): assemble the harness from composition rows only, fail loud on missing harness"
```

---

### Task 7: examples/dsh_harness 对齐

**Files:**
- Modify: `examples/dsh_harness/README.md`

- [ ] **Step 1: 检查残留**

Run: `grep -rn "ReactAgentLoop\|ReactLoopAgent\|harness.llm\|ctx.get(\"tools\")\|\"agentTools\"\|AgentEngine" examples/dsh_harness/`

Task 3/4 已改代码文件；README 的分类表（`:52` "llm/tools/systemPrompt/agentLoop"）与服务名说明需要同步为 `agentTools`，类名统一 `AgentLoop`。

- [ ] **Step 2: 更新 README**

- 表格与图示中 `ReactAgentLoop` → `AgentLoop`；
- 服务名 `tools` → `agentTools`（出现于插件角色表、组合文件片段、对照表 `packages/core/agent-loop` 行）；
- 补一句：`javis.harness.plugins.*` 是生产侧同样的行式装配（`javis/harness/plugins/`），demo 与生产共用同一套服务契约。

- [ ] **Step 3: 冒烟运行 demo**

Run: `timeout 120 uv run python examples/dsh_harness/cli.py --scenario tools 2>&1 | tail -20`
Expected: 退出码 0，输出含 weather 工具调用结果（脚本化模型，无网络）。

- [ ] **Step 4: 提交**

```bash
git add examples/dsh_harness
git commit -m "docs(dsh_harness): align the demo with the agentTools service and AgentLoop naming"
```

---

### Task 8: 文档同步

**Files:**
- Modify: `javis/harness/__init__.py`（模块地图 + `__all__`）
- Modify: `javis/__init__.py`、`javis/contracts/__init__.py`、`javis/contracts/harness.py` 的包级 docstring
- Modify: `docs/plugins.md`
- Modify: `README.md`、`README.zh-CN.md`

- [ ] **Step 1: 重写 `javis/harness/__init__.py`**

```python
"""javis.harness — the harness: dsh-style agent loop + javis integration.

Loop core (naming aligned with the dsh reference):
- ``agent.py`` — ``AgentLoop`` phase state machine (idle / maintenance / running)
- ``inbox.py`` — next-turn / next-step inbox with splice logging
- ``session.py`` — session event log + ``derive_messages``
- ``stream.py`` — loop-side stream assembly (``normalized_stream`` /
  ``BlockAssembler``)
- ``tools.py`` — exclusive/parallel tool scheduling, ``concludes_turn``, abort
  synthesis; reads the ``agentTools`` service
- ``types.py`` — dsh-aligned data contracts (blocks / chunks / finish / events /
  config / the ``LLM`` protocol)

Javis integration shell:
- ``harness.py`` — ``Harness`` implements ``javis.contracts.harness.Harness``
  (message mirror, usage, session save/restore, permission/request/limit
  middleware); every service it drives comes from the root context
- ``plugins/`` — the composition rows that assemble it (``llm`` /
  ``agent_tools`` / ``system_prompt`` / ``agent_loop`` / ``snip`` / ``harness``)
- ``tool_adapter.py`` — adapts javis ``Tool`` → core ``Tool`` + the
  ``AgentToolView`` live view
- ``prompt.py`` — prompt assembly (sections + tool schemas)
- ``compression.py`` — history compression middleware (snip + cap)

LLM providers live in ``javis.llm`` (LlmRuntime adapter registry + adapters),
built-in tools in ``javis.tools`` — host-level services, not harness internals.
"""

from __future__ import annotations

from . import agent as agent
from . import inbox as inbox
from . import plugins as plugins
from . import session as session
from . import stream as stream
from . import tools as tools
from . import types as types
from .compression import HistoryCompressor, make_snip_listener
from .harness import Harness
from .tool_adapter import AgentToolView, adapt_tool

__version__ = "0.1.0"

__all__ = [
    "AgentToolView",
    "Harness",
    "HistoryCompressor",
    "__version__",
    "adapt_tool",
    "agent",
    "inbox",
    "make_snip_listener",
    "plugins",
    "session",
    "stream",
    "tools",
    "types",
]
```

- [ ] **Step 2: 包级 docstring 与残留引用**

Run: `grep -rn "AgentEngine\|HarnessEngine\|ReactAgentLoop\|javis.harness.llm\|javis/harness/engine\|javis/harness/build\|\bENGINE_SERVICE\b" --include=*.py --include=*.md javis/ tests/ examples/ README.md README.zh-CN.md docs/`
Run: `grep -rn 'engine' --include=*.py javis/contracts/host.py javis/app/runtime.py`（抓 docstring 里的字符串形态）

逐处按新术语替换。已知落点（grep 之外的字符串形态）：

- `javis/__init__.py:4` 的引擎描述；
- `javis/contracts/host.py:11` docstring 示例 `ctx.provide("engine", build_engine(...))` → `ctx.provide("harness", build_harness(...))`；
- `javis/app/runtime.py:13` 模块 docstring 的兜底描述、`:66` `build_system_prompt` 正文的 ``AgentEngine``、`:186`（Step 5 已随 `_build_default_engine` 删除）；
- `javis/harness/session.py:70` 的注释；
- `tests/test_harness/test_engine.py:1`、`tests/test_harness/test_agent_loop.py:1` 的 docstring；
- `docs/plugins.md` 见下一步。

- [ ] **Step 3: 重写 `docs/plugins.md`**

- 「组合文件」段：默认文件不再是空列表，而是全量六行组合；空 `[]` 现在启动即报错。
- 「内建服务」表：`engine` → `harness`（`javis.contracts.harness.Harness` 实例，**组合行提供**）；`llm` 从"预留"改为组合行实装；补 `agentTools` / `systemPrompt` / `agentLoop` 三行（注明由 `javis.harness.plugins.*` 提供）。
- 「引擎插件」整节改为「替换 Harness」：删掉"告警并回退内建"与"工具条目排序要求"，改为

```markdown
## 替换 Harness

内置 Harness 由组合里的 `harness` 行装配（`javis.harness.plugins.harness`）。
替换 = 改这一行：把 `name` 指向自己的驱动插件，或 `disabled: true` 后另加自建行。
服务不可重复 provide，所以不存在两套实现并存。

```yaml
- id: harness
  name: './my_harness.py'
  inject: [llm, agentTools, systemPrompt, agentLoop, config, host]
```

缺失 `harness` 行（含空组合 `[]`）→ 启动 `RuntimeError`，错误信息含组合文件路径与补救提示。
```

- 「权限钩子」：`AgentEngine` → `Harness`。
- 「生命周期」：启动步骤改为 `Context → 内建服务 → Loader → settle → assert_entries_settled → 读 harness`；退出不变。
- 「扩展点」：删掉已完成的 `llm` 服务接线与 `engines`→`harness` 改名两条，保留 HMR / 多组合合并。

- [ ] **Step 4: README 目录树**

`README.md` 的 Project layout 树更新：

- 删掉已不存在的 `javis/engines/`、`javis/host/`；
- `javis/harness/` 下补 `plugins/`、`stream.py`、`harness.py`（原 `engine.py`）、`tool_adapter.py`；
- `javis/contracts/` 下 `engine.py` → `harness.py`；
- 正文中的 `AgentEngine` / `ReactAgentLoop` 按新术语表更新为 `Harness` / `AgentLoop`；
- 插件一节描述组合行装配。
- `README.zh-CN.md` 同步对应段落。

- [ ] **Step 5: 收尾验证**

Run: `timeout 300 uv run pytest tests/ -q && grep -rn "AgentEngine\|HarnessEngine\|ReactAgentLoop\|\bENGINE_SERVICE\b" --include=*.py javis/ tests/ examples/`
Expected: 测试全绿；grep 无输出（`examples/mini_dsh` 的 `ReactAgentLoop` 是独立副本，如出现则在计划中确认保留，不在 javis 命名体系内）。

- [ ] **Step 6: 提交**

```bash
git add -A javis docs README.md README.zh-CN.md
git commit -m "docs(harness): update module map, plugin guide and README for the composition-only harness"
```

---

## 自检清单（写计划时已核对）

**Spec 覆盖**

| Spec 要求 | 落点 |
|---|---|
| §2 术语改名（Harness / AgentLoop / stream 拆分） | Task 1 / 2 / 3 |
| §3 根 context 九服务 + 无私有装配 | Task 6 Step 3/4 |
| §3 `agentTools` 实时视图 | Task 4 |
| §3 plugins/ 六行 | Task 6 Step 1-3 |
| §4 默认组合全量六行 | Task 6 Step 6 |
| §5 build_runtime = boot + 断言；删 `_build_default_engine` / `build.py` | Task 6 Step 5/7；断言 Task 5 |
| §6 迁移顺序（改名 → 拆行 → 去私有装配 → 测试 → 文档） | Task 1-3 → 6 → 8 |
| §7 失败模式五种（缺文件/空文件/模块路径错/缺依赖/类型错） | Task 6 Step 7 + Step 9 测试覆盖 |
| §8 影响面（runtime / harness / contracts / session / llm docstring / 测试文档） | Task 1/2/6/7/8 |

**类型/命名一致性**（跨任务必须一致）

- `HARNESS_SERVICE = "harness"`、`AGENT_TOOLS_SERVICE = "agentTools"`、`SYSTEM_PROMPT_SERVICE = "systemPrompt"`、`AGENT_LOOP_SERVICE = "agentLoop"` —— Task 1 定义，Task 4/6 使用。
- `Harness(ctx, *, provider_name, model, system_prompt, cwd, workspace, session_id, max_turns, tool_metadata)` —— Task 6 Step 4 定义，Step 3（row）与 Step 10（测试 helper）调用一致。
- `build_harness(ctx)` 单参数 —— Task 6 Step 3 定义，Step 8 conftest 以 `lambda *_a, **_k` patch。
- `AgentToolView(host_registry, ctx, *, sub_agent_factory=None)` —— Task 4 定义，Task 6 Step 2/10 使用。
- `MutableLoopConfig(*, max_parallel_tool_calls, max_steps_per_turn, history_compressor)` —— Task 6 Step 2 定义，Step 10 使用。
- `assert_entries_settled(ctx)` —— Task 5 定义，Task 6 Step 7 调用。

**已知取舍（评审时可直接讨论）**

1. `system-prompt` 行的 `inject` 含 `config` 但当前 `HarnessPromptService` 不读它 —— 与已评审 spec 的组合文件逐字一致，故保留。
2. Task 6 是单次原子提交（删除兜底路径与新增组合行必须同时落地），其余任务均可独立提交。
3. `examples/mini_dsh/` 是独立 core 副本，不随主包改名。
