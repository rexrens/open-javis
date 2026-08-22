# javis 插件系统实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 javis 实现 cordis-like 插件内核（本地目录加载 + PluginInstance 状态机 + ctx 服务/事件/effect），MVP 落地工具/命令/生命周期三类扩展点，并把静态 `ALL_TOOLS` 迁移为注册表。

**架构：** 新增 `javis/plugins/` 六模块（errors / context / instance / registry / loader / `__init__`）。`PluginInstance` 是状态机骨架（PENDING→LOADING→ACTIVE/FAILED→UNLOADING→DISPOSED），`apply(ctx, config)` 在 LOADING 期执行并注册工具/命令/服务；`ServiceRegistry` 支持 inject 依赖的运行时等待（asyncio.Condition）；`EventBus` 跨插件广播。runtime 的 `build_javis_runtime` 在创建 agent backend 前并行激活插件，工具经 `all_tools()` 快照进入引擎；`start_runtime`/`close_runtime` 变为真实生命周期。

**技术栈：** Python ≥3.10，asyncio，pydantic v2（插件 Config 校验），importlib（动态加载），pytest + pytest-asyncio（auto 模式）。

**规格：** `docs/superpowers/specs/2026-08-21-javis-plugin-system-design.md`

---

## 文件结构

**创建：**

| 文件 | 职责 |
|---|---|
| `corecoder/tools/__init__.py`（重写） | 工具注册表：`register_tool` / `get_tool` / `all_tools`，内建 7 工具自注册，`ALL_TOOLS` 兼容别名 |
| `javis/plugins/__init__.py` | 公开 API（PluginContext / PluginInstance / PluginRegistry / PluginState / ServiceRegistry / EventBus / LoadReport / 异常） |
| `javis/plugins/errors.py` | `PluginError` / `PluginConfigError` / `PluginDependencyError` |
| `javis/plugins/context.py` | `PluginContext`（register_tool/command/engine 委托、on/emit/emit_serial、effect/on_close/on_start）、`ServiceRegistry`、`EventBus` |
| `javis/plugins/instance.py` | `PluginInstance` + `PluginState`（状态机、async start/stop、依赖等待、config 校验） |
| `javis/plugins/registry.py` | `PluginRegistry`（instance 表、activate_all/close_all、list_plugins）、`LoadReport` |
| `javis/plugins/loader.py` | `plugin_dirs` / `discover_plugin_files` / `extract_plugins` / `load_plugins` |
| `examples/plugins/hello_tool.py` | 示例：注册自定义工具 |
| `examples/plugins/hello_command.py` | 示例：注册斜杠命令 |
| `docs/plugins.md` | 用户文档：写插件、目录、配置、生命周期、API |
| `tests/test_corecoder/test_tool_registry.py` | 工具注册表单测 |
| `tests/test_javis/plugins/__init__.py` | 测试包 |
| `tests/test_javis/plugins/test_context.py` | context/服务/事件/effect 单测 |
| `tests/test_javis/plugins/test_instance.py` | 状态机/依赖等待单测 |
| `tests/test_javis/plugins/test_registry.py` | registry/激活/报告单测 |
| `tests/test_javis/plugins/test_loader.py` | 加载器单测（含 fixtures） |
| `tests/test_javis/plugins/test_runtime_integration.py` | runtime 接入集成测试 |
| `tests/test_javis/plugins/fixtures/`（多个插件文件） | loader 测试插件（简单/声明式/对象/多插件/坏语法/需配置） |

**修改：**

| 文件 | 改动 |
|---|---|
| `corecoder/agent.py:40` | 默认工具列表 `ALL_TOOLS` → `all_tools()` |
| `corecoder/__init__.py:8,10` | `ALL_TOOLS` 保留为兼容别名（值改为 `all_tools()` 快照） |
| `javis/engines/corecoder/backend.py:216` | `Agent(llm=llm, ..., tools=all_tools())` —— 插件工具进入引擎的关键 |
| `tests/test_javis/test_corecoder_engine.py:13,138` | `ALL_TOOLS` 引用 → `all_tools()` |
| `javis/host/runtime.py` | build 流程接入插件加载/激活；`RuntimeBundle` 加 `plugins` 字段；start/close 真实生命周期 |
| `javis/session/config.py` | **无需改动**：`JavisConfig.plugins`（`dict[str, Any]`）已存在，loader 直接访问（比加辅助函数更 YAGNI，偏离规格 §16 的"读取辅助"一行，理由在此） |

---

### 任务 1：工具注册表化（阶段 2 前置，独立验收）

**文件：**
- 修改：`corecoder/tools/__init__.py`（重写）
- 修改：`corecoder/agent.py:40`
- 修改：`corecoder/__init__.py:8,10`
- 修改：`javis/engines/corecoder/backend.py:216`
- 修改：`tests/test_javis/test_corecoder_engine.py:13,138`
- 测试：`tests/test_corecoder/test_tool_registry.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_corecoder/test_tool_registry.py
"""Tests for the tool registry."""

from __future__ import annotations

from corecoder.tools import all_tools, get_tool, register_tool
from corecoder.tools.base import Tool


class TestEchoTool(Tool):
    name = "test_echo"
    description = "Echo text back"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return kwargs.get("text", "")


def test_builtin_tools_registered():
    names = {t.name for t in all_tools()}
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "agent"} <= names


def test_register_and_get():
    register_tool(TestEchoTool())
    assert get_tool("test_echo") is not None
    assert get_tool("test_echo").execute(text="hi") == "hi"


def test_get_unknown_returns_none():
    assert get_tool("definitely-not-a-tool") is None


def test_register_idempotent():
    register_tool(TestEchoTool())
    before = len(all_tools())
    register_tool(TestEchoTool())
    assert len(all_tools()) == before
    assert sum(1 for t in all_tools() if t.name == "test_echo") == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_corecoder/test_tool_registry.py -q`
预期：FAIL，`ImportError: cannot import name 'all_tools' from 'corecoder.tools'`

- [ ] **步骤 3：重写工具注册表**

```python
# corecoder/tools/__init__.py（整文件替换）
"""Tool registry.

Tools register themselves via ``register_tool``; ``all_tools()`` returns a
snapshot for the agent. ``ALL_TOOLS`` is kept as a deprecated import-time
alias. Built-in tools are registered at import time.
"""

from __future__ import annotations

import logging

from .agent import AgentTool
from .base import Tool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .write import WriteFileTool

log = logging.getLogger(__name__)

_TOOLS: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    """Register a tool. Re-registration overwrites with a warning (idempotent)."""
    if tool.name in _TOOLS:
        log.warning("Tool %r re-registered, overwriting previous entry", tool.name)
    _TOOLS[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    """Look up a tool by name."""
    return _TOOLS.get(name)


def all_tools() -> list[Tool]:
    """Snapshot of all registered tools (new list each call)."""
    return list(_TOOLS.values())


for _tool in (
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
):
    register_tool(_tool)

# Deprecated compatibility alias: import-time snapshot.
ALL_TOOLS = all_tools()

__all__ = ["ALL_TOOLS", "Tool", "all_tools", "get_tool", "register_tool"]
```

- [ ] **步骤 4：更新三个引用点**

```python
# corecoder/agent.py:40 — 原: from .tools import ALL_TOOLS → from .tools import all_tools
# 原: self.tools = tools if tools is not None else ALL_TOOLS
self.tools = tools if tools is not None else all_tools()
```

```python
# corecoder/__init__.py — 保持导出（值已是兼容快照，无需改 import 行）：
# 原: from corecoder.tools import ALL_TOOLS   ← 不变
# __all__ 不变
```

```python
# javis/engines/corecoder/backend.py — 216 行
from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.llm import OpenAICompatProvider
from corecoder.tools import all_tools  # 新增

# ...
agent = Agent(llm=llm, max_context_tokens=cfg.max_context_tokens, tools=all_tools())
```

```python
# tests/test_javis/test_corecoder_engine.py:13 — 原: from corecoder.tools import ALL_TOOLS
from corecoder.tools import all_tools
# 138 行 — 原: assert names == {t.name for t in ALL_TOOLS}
assert names == {t.name for t in all_tools()}
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_corecoder/test_tool_registry.py tests/test_javis/test_corecoder_engine.py tests/test_corecoder/ -q`
预期：PASS（含全部既有 corecoder 测试）

- [ ] **步骤 6：Commit**

```bash
git add corecoder/tools/__init__.py corecoder/agent.py corecoder/__init__.py javis/engines/corecoder/backend.py tests/test_corecoder/test_tool_registry.py tests/test_javis/test_corecoder_engine.py
git commit -m "refactor(tools): static ALL_TOOLS -> register_tool registry with all_tools() snapshot"
```

---

### 任务 2：错误类型 + PluginContext（服务/事件/effect）

**文件：**
- 创建：`javis/plugins/errors.py`
- 创建：`javis/plugins/context.py`
- 测试：`tests/test_javis/plugins/__init__.py`、`tests/test_javis/plugins/test_context.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_javis/plugins/test_context.py
"""Tests for PluginContext, ServiceRegistry and EventBus."""

from __future__ import annotations

import asyncio

import pytest

from javis.plugins.context import EventBus, PluginContext, ServiceRegistry
from corecoder.tools.base import Tool


class CtxTool(Tool):
    name = "ctx_test_tool"
    description = "tool registered through ctx"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "ok"


@pytest.fixture
def ctx():
    services = ServiceRegistry()
    bus = EventBus()
    services.provide("tools", type("T", (), {
        "register_tool": lambda self, t: None,
        "get_tool": lambda self, n: "found" if n == "ctx_test_tool" else None,
    })())
    services.provide("commands", type("C", (), {"register": lambda self, c: None})())
    services.provide("engines", type("E", (), {"register_engine": lambda self, n, f: None})())
    return PluginContext(name="p1", config=None, services=services, bus=bus, javis_config=None)


def test_provide_and_get(ctx):
    ctx.provide("svc", 42)
    assert ctx.get("svc") == 42


def test_get_unknown_service_raises(ctx):
    with pytest.raises(KeyError):
        ctx.get("nope")


def test_register_tool_goes_to_tools_service(ctx):
    ctx.register_tool(CtxTool())
    tools = ctx.get("tools")
    assert tools.get_tool("ctx_test_tool") is not None


def test_on_emit_sync_handler(ctx):
    seen = []
    ctx.on("evt", lambda payload: seen.append(payload))
    ctx.emit("evt", "x")
    assert seen == ["x"]


@pytest.mark.asyncio
async def test_emit_serial_awaits_async_handler(ctx):
    done = []

    async def handler(payload):
        await asyncio.sleep(0.01)
        done.append(payload)

    ctx.on("evt", handler)
    await ctx.emit_serial("evt", "y")
    assert done == ["y"]


def test_effect_disposers_run_in_reverse_order(ctx):
    order = []
    ctx.effect(lambda: order.append("first") or None)
    ctx.effect(lambda: order.append("second") or None)

    async def _close():
        await ctx.close()

    asyncio.run(_close())
    assert order == ["second", "first"]


def test_close_revokes_services_and_listeners(ctx):
    ctx.provide("svc", 1)
    ctx.on("evt", lambda p: None)

    async def _close():
        await ctx.close()

    asyncio.run(_close())
    assert not ctx._services.contains("svc")
    assert ctx._bus._listeners.get("evt", {}) == {}  # owner listeners removed
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/plugins/test_context.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'javis.plugins'`

- [ ] **步骤 3：编写 errors.py 与 context.py**

```python
# javis/plugins/errors.py
"""Plugin framework errors."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for plugin framework errors."""


class PluginConfigError(PluginError):
    """Plugin config failed pydantic validation."""


class PluginDependencyError(PluginError):
    """A plugin's inject dependencies were never provided."""
```

```python
# javis/plugins/context.py
"""Plugin context — service registry, cross-plugin events, lifecycle hooks.

- ``ServiceRegistry``: shared across plugins; ``provide`` wakes waiting
  instances (asyncio.Condition); owners let unload revoke exactly its services.
- ``EventBus``: cross-plugin broadcast; listeners grouped by owner plugin so
  unload removes exactly that plugin's listeners.
- ``PluginContext``: the ``ctx`` handed to ``apply(ctx, config)``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

Disposer = Callable[[], Awaitable[None] | None]
EventHandler = Callable[[Any], Awaitable[None] | None]
StartHook = Callable[[], Awaitable[None] | None]


class ServiceRegistry:
    """Cross-plugin service store with dependency-wakeup."""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._owners: dict[str, str] = {}
        self._cond = asyncio.Condition()

    def provide(self, name: str, value: Any, owner: str | None = None) -> None:
        self._services[name] = value
        if owner is not None:
            self._owners[name] = owner
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:
            pass  # no running loop — nothing to wake

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    def get(self, name: str) -> Any:
        return self._services.get(name)

    def contains(self, name: str) -> bool:
        return name in self._services

    async def wait_for(self, names: list[str], timeout: float) -> list[str]:
        """Wait until all ``names`` are provided (or timeout); return missing."""
        async def _ready() -> bool:
            return all(n in self._services for n in names)

        if _ready():
            return []
        try:
            async with self._cond:
                await asyncio.wait_for(self._cond.wait_for(_ready), timeout)
        except asyncio.TimeoutError:
            pass
        return [n for n in names if n not in self._services]

    def revoke_owner(self, owner: str) -> None:
        for name in [n for n, o in self._owners.items() if o == owner]:
            del self._services[name]
            del self._owners[name]


class EventBus:
    """Cross-plugin event dispatch (emit = fire-and-forget, emit_serial = await)."""

    def __init__(self) -> None:
        # event name -> owner plugin -> [handlers]
        self._listeners: dict[str, dict[str, list[EventHandler]]] = {}

    def on(self, event: str, owner: str, handler: EventHandler) -> Callable[[], None]:
        self._listeners.setdefault(event, {}).setdefault(owner, []).append(handler)

        def cancel() -> None:
            handlers = self._listeners.get(event, {}).get(owner)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return cancel

    def emit(self, event: str, payload: Any = None) -> None:
        for owner, handlers in list(self._listeners.get(event, {}).items()):
            for handler in list(handlers):
                result = handler(payload)
                if inspect.isawaitable(result):
                    asyncio.get_running_loop().create_task(_consume(result, event, owner))

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        for owner, handlers in list(self._listeners.get(event, {}).items()):
            for handler in list(handlers):
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result

    def remove_owner(self, owner: str) -> None:
        for event in self._listeners:
            self._listeners[event].pop(owner, None)


async def _consume(awaitable: Awaitable[None], event: str, owner: str) -> None:
    try:
        await awaitable
    except Exception:  # fire-and-forget handlers must not crash the loop
        logging.getLogger("javis.plugins").exception(
            "event handler for %r of plugin %r failed", event, owner
        )


class PluginContext:
    """Per-plugin context handed to ``apply(ctx, config)``."""

    def __init__(
        self,
        *,
        name: str,
        config: Any,
        services: ServiceRegistry,
        bus: EventBus,
        javis_config: Any,
    ) -> None:
        self.name = name
        self.config = config
        self.javis_config = javis_config
        self.logger = logging.getLogger(f"javis.plugins.{name}")
        self._services = services
        self._bus = bus
        self._disposers: list[Disposer] = []
        self._start_hooks: list[StartHook] = []
        self._services.provide("logger", self.logger)

    # ---- services -------------------------------------------------------
    def provide(self, name: str, value: Any) -> None:
        self._services.provide(name, value, owner=self.name)

    def get(self, name: str) -> Any:
        value = self._services.get(name)
        if value is None and not self._services.contains(name):
            raise KeyError(f"Service {name!r} not provided")
        return value

    # ---- extension points ----------------------------------------------
    def register_tool(self, tool: Any) -> None:
        self._services.get("tools").register_tool(tool)

    def register_command(self, command: Any) -> None:
        self._services.get("commands").register(command)

    def register_engine(self, name: str, factory: Any) -> None:
        self._services.get("engines").register_engine(name, factory)

    # ---- events ---------------------------------------------------------
    def on(self, event: str, handler: EventHandler) -> Callable[[], None]:
        return self._bus.on(event, self.name, handler)

    def emit(self, event: str, payload: Any = None) -> None:
        self._bus.emit(event, payload)

    async def emit_serial(self, event: str, payload: Any = None) -> None:
        await self._bus.emit_serial(event, payload)

    # ---- lifecycle hooks ------------------------------------------------
    def effect(self, disposer: Disposer) -> None:
        self._disposers.append(disposer)

    def on_close(self, fn: Disposer) -> None:
        self._disposers.append(fn)

    def on_start(self, fn: StartHook) -> None:
        self._start_hooks.append(fn)

    async def run_start_hooks(self) -> None:
        for hook in self._start_hooks:
            result = hook()
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        """Run disposers (reverse order), then drop listeners and owned services."""
        for disposer in reversed(self._disposers):
            result = disposer()
            if inspect.isawaitable(result):
                await result
        self._disposers.clear()
        self._bus.remove_owner(self.name)
        self._services.revoke_owner(self.name)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_javis/plugins/test_context.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add javis/plugins/errors.py javis/plugins/context.py tests/test_javis/plugins/
git commit -m "feat(plugins): PluginContext with ServiceRegistry, EventBus and effect lifecycle"
```

---

### 任务 3：PluginInstance 状态机 + 依赖等待

**文件：**
- 创建：`javis/plugins/instance.py`
- 测试：`tests/test_javis/plugins/test_instance.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_javis/plugins/test_instance.py
"""Tests for the PluginInstance state machine."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ValidationError

from javis.plugins.context import EventBus, PluginContext, ServiceRegistry
from javis.plugins.errors import PluginConfigError, PluginDependencyError
from javis.plugins.instance import PluginInstance, PluginState


class Cfg(BaseModel):
    greeting: str = "hi"


def make_ctx(services, bus):
    def _build(name, config):
        return PluginContext(name=name, config=config, services=services, bus=bus, javis_config=None)

    return _build


@pytest.fixture
def env():
    services = ServiceRegistry()
    bus = EventBus()
    services.provide("tools", type("T", (), {"register_tool": lambda self, t: None})())
    services.provide("commands", type("C", (), {"register": lambda self, c: None})())
    services.provide("engines", type("E", (), {"register_engine": lambda self, n, f: None})())
    return services, bus


@pytest.mark.asyncio
async def test_sync_apply_reaches_active(env):
    services, bus = env
    applied = []

    def apply(ctx, config):
        applied.append(config.greeting)

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=Cfg, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.ACTIVE
    assert applied == ["hi"]


@pytest.mark.asyncio
async def test_async_apply_supported(env):
    services, bus = env

    async def apply(ctx, config):
        await asyncio.sleep(0.01)

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.ACTIVE


@pytest.mark.asyncio
async def test_config_validation_failure_fails_plugin(env):
    services, bus = env
    applied = []

    def apply(ctx, config):
        applied.append(config)

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=Cfg, inject=[], raw_config={"greeting": 123},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.FAILED
    assert isinstance(inst.error, PluginConfigError)
    assert applied == []  # apply never ran


@pytest.mark.asyncio
async def test_apply_exception_fails_plugin(env):
    services, bus = env

    def apply(ctx, config):
        raise RuntimeError("boom")

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    assert inst.state is PluginState.FAILED
    assert isinstance(inst.error, RuntimeError)


@pytest.mark.asyncio
async def test_missing_dependency_fails_after_timeout(env):
    services, bus = env

    def apply(ctx, config):
        pass

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=["never-provided"],
        raw_config={}, ctx_builder=make_ctx(services, bus), start_timeout=0.1,
    )
    await inst.start()
    assert inst.state is PluginState.FAILED
    assert isinstance(inst.error, PluginDependencyError)
    assert "never-provided" in str(inst.error)


@pytest.mark.asyncio
async def test_dependency_provided_later_wakes_instance(env):
    services, bus = env
    entered = []

    def apply(ctx, config):
        entered.append(True)

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=["late-svc"],
        raw_config={}, ctx_builder=make_ctx(services, bus), start_timeout=2.0,
    )
    start_task = asyncio.create_task(inst.start())
    await asyncio.sleep(0.02)  # let it reach PENDING on the dependency
    assert inst.state is PluginState.PENDING
    services.provide("late-svc", object())
    await asyncio.wait_for(start_task, 1.0)
    assert inst.state is PluginState.ACTIVE
    assert entered == [True]


@pytest.mark.asyncio
async def test_stop_runs_disposers_in_reverse_order(env):
    services, bus = env
    order = []

    def apply(ctx, config):
        ctx.effect(lambda: order.append("first") or None)
        ctx.effect(lambda: order.append("second") or None)

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    await inst.stop()
    assert inst.state is PluginState.DISPOSED
    assert order == ["second", "first"]


@pytest.mark.asyncio
async def test_apply_return_value_is_used_as_disposer(env):
    services, bus = env
    closed = []

    def apply(ctx, config):
        def disposer():
            closed.append(True)
        return disposer

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    await inst.stop()
    assert closed == [True]


@pytest.mark.asyncio
async def test_stop_revokes_owned_service(env):
    services, bus = env

    def apply(ctx, config):
        ctx.provide("my-svc", object())

    inst = PluginInstance(
        name="p", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=make_ctx(services, bus), start_timeout=0.5,
    )
    await inst.start()
    assert services.contains("my-svc")
    await inst.stop()
    assert not services.contains("my-svc")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/plugins/test_instance.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'javis.plugins.instance'`

- [ ] **步骤 3：编写 instance.py**

```python
# javis/plugins/instance.py
"""Plugin instance — the lifecycle state machine (aligned with dsh's Fiber).

The state machine is the skeleton; everything else hangs off its
transitions. The plugin's own logic (inside ``apply``) is not our business:
we call it at the right time, record its state, and clean up on stop.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from javis.plugins.context import Disposer, PluginContext, ServiceRegistry
from javis.plugins.errors import PluginConfigError, PluginDependencyError

ApplyFn = Callable[[PluginContext, Any], Awaitable[None] | None | Disposer | None]
CtxBuilder = Callable[[str, Any], PluginContext]


class PluginState(str, enum.Enum):
    PENDING = "pending"      # waiting for inject dependencies
    LOADING = "loading"      # apply(ctx, config) is running
    ACTIVE = "active"        # loaded and providing
    FAILED = "failed"        # config validation or apply threw
    UNLOADING = "unloading"  # disposers are running
    DISPOSED = "disposed"    # removed; cannot restart


class PluginInstance:
    """Runtime instance of one plugin application."""

    def __init__(
        self,
        *,
        name: str,
        apply_fn: ApplyFn,
        config_model: type | None,
        inject: list[str],
        raw_config: dict,
        ctx_builder: CtxBuilder,
        services: ServiceRegistry,
        start_timeout: float = 10.0,
    ) -> None:
        self.name = name
        self.state = PluginState.PENDING
        self.config: Any = None
        self.ctx: PluginContext | None = None
        self.error: Exception | None = None
        self._apply_fn = apply_fn
        self._config_model = config_model
        self._inject = list(inject)
        self._raw_config = raw_config
        self._ctx_builder = ctx_builder
        self._services = services
        self._start_timeout = start_timeout

    async def start(self) -> None:
        """Resolve config, wait for inject deps, run apply. Never raises."""
        if self.state is not PluginState.PENDING:
            return
        try:
            self.config = self._resolve_config()
        except Exception as exc:
            self._fail(PluginConfigError(f"plugin {self.name!r} config invalid: {exc}") from exc)
            return

        if self._inject:
            missing = await self._services.wait_for(self._inject, self._start_timeout)
            if missing:
                self._fail(PluginDependencyError(
                    f"plugin {self.name!r} missing injected services: {sorted(missing)}"
                ))
                return

        self.state = PluginState.LOADING
        self.ctx = self._ctx_builder(self.name, self.config)
        try:
            result = self._apply_fn(self.ctx, self.config)
            if inspect.isawaitable(result):
                result = await result
            if result is not None and callable(result):
                self.ctx.effect(result)
            self.state = PluginState.ACTIVE
        except Exception as exc:
            self._fail(exc)

    def _resolve_config(self) -> Any:
        if self._config_model is None:
            return None
        return self._config_model.model_validate(self._raw_config)

    def _fail(self, exc: Exception) -> None:
        self.error = exc
        self.state = PluginState.FAILED

    async def stop(self) -> None:
        """Run disposers (reverse order), drop listeners, revoke services."""
        if self.state is PluginState.DISPOSED:
            return
        self.state = PluginState.UNLOADING
        try:
            if self.ctx is not None:
                await self.ctx.close()
        finally:
            self.state = PluginState.DISPOSED

    @property
    def inject(self) -> list[str]:
        return list(self._inject)
```

注意 `_fail` 中 config 校验失败应包装为 `PluginConfigError`（保留原始异常为 cause）。修正：

```python
    try:
        self.config = self._resolve_config()
    except Exception as exc:
        self._fail(PluginConfigError(f"plugin {self.name!r} config invalid: {exc}") from exc)
        return
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_javis/plugins/test_instance.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add javis/plugins/instance.py tests/test_javis/plugins/test_instance.py
git commit -m "feat(plugins): PluginInstance state machine with async start/stop and inject waiting"
```

---

### 任务 4：PluginRegistry + LoadReport

**文件：**
- 创建：`javis/plugins/registry.py`
- 测试：`tests/test_javis/plugins/test_registry.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_javis/plugins/test_registry.py
"""Tests for PluginRegistry."""

from __future__ import annotations

import pytest

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.registry import LoadReport, PluginRegistry


def _default_ctx_builder(services, bus):
    def build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, bus=bus, javis_config=None
        )

    return build


@pytest.fixture
def registry():
    services = ServiceRegistry()
    bus = EventBus()
    return PluginRegistry(
        services=services, bus=bus,
        ctx_builder=_default_ctx_builder(services, bus),
    )


def _instance(registry, name, *, inject=None, fail=False, start_timeout=0.2):
    def apply(ctx, config):
        if fail:
            raise RuntimeError("boom")

    return PluginInstance(
        name=name,
        apply_fn=apply,
        config_model=None,
        inject=inject or [],
        raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services,
        start_timeout=start_timeout,
    )


@pytest.mark.asyncio
async def test_activate_all_activates_ok_plugins(registry):
    registry.add(_instance(registry, "a"))
    report = await registry.activate_all()
    assert isinstance(report, LoadReport)
    assert report.loaded == ["a"]
    assert report.failed == []


@pytest.mark.asyncio
async def test_activate_all_reports_failed(registry):
    registry.add(_instance(registry, "bad", fail=True))
    report = await registry.activate_all()
    assert report.failed == ["bad"]
    assert report.loaded == []
    assert "boom" in report.errors["bad"]


@pytest.mark.asyncio
async def test_list_plugins_shows_state_and_error(registry):
    registry.add(_instance(registry, "bad", fail=True))
    await registry.activate_all()
    plugins = registry.list_plugins()
    by_name = {p["name"]: p for p in plugins}
    assert by_name["bad"]["state"] is PluginState.FAILED
    assert "boom" in str(by_name["bad"]["error"])


@pytest.mark.asyncio
async def test_close_all_disposes(registry):
    closed = []

    def apply(ctx, config):
        ctx.effect(lambda: closed.append(ctx.name) or None)

    inst = PluginInstance(
        name="c", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services, start_timeout=0.2,
    )
    registry.add(inst)
    await registry.activate_all()
    await registry.close_all()
    assert closed == ["c"]
    assert inst.state is PluginState.DISPOSED


@pytest.mark.asyncio
async def test_run_start_hooks(registry):
    order = []

    def apply(ctx, config):
        ctx.on_start(lambda: order.append(ctx.name) or None)

    inst = PluginInstance(
        name="s", apply_fn=apply, config_model=None, inject=[], raw_config={},
        ctx_builder=registry.ctx_builder,
        services=registry.services, start_timeout=0.2,
    )
    registry.add(inst)
    await registry.activate_all()
    await registry.run_start_hooks()
    assert order == ["s"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/plugins/test_registry.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'javis.plugins.registry'`

- [ ] **步骤 3：编写 registry.py**

```python
# javis/plugins/registry.py
"""PluginRegistry — the table of PluginInstances and their lifecycle driver."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginInstance, PluginState


@dataclass
class LoadReport:
    """Outcome of activating all plugins."""

    loaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class PluginRegistry:
    """Owns the plugin instance table and drives activation/shutdown."""

    def __init__(
        self,
        *,
        services: ServiceRegistry,
        bus: EventBus,
        ctx_builder: "CtxBuilder",
    ) -> None:
        self.services = services
        self.bus = bus
        self.ctx_builder = ctx_builder
        self._instances: dict[str, PluginInstance] = {}

    def add(self, instance: PluginInstance) -> None:
        self._instances[instance.name] = instance

    def get(self, name: str) -> PluginInstance | None:
        return self._instances.get(name)

    async def activate_all(self, timeout: float = 10.0) -> LoadReport:
        """Start every instance in parallel; never raises."""
        report = LoadReport(skipped=[])
        results = await asyncio.gather(
            *(i.start() for i in self._instances.values()),
            return_exceptions=True,
        )
        for name, result in zip(self._instances, results):
            inst = self._instances[name]
            if isinstance(result, Exception):
                inst.error = result
                inst.state = PluginState.FAILED
            if inst.state is PluginState.ACTIVE:
                report.loaded.append(name)
            elif inst.state is PluginState.FAILED:
                report.failed.append(name)
                report.errors[name] = str(inst.error)
        return report

    async def close_all(self) -> None:
        """Stop every instance in parallel; disposer errors are logged, not raised."""
        await asyncio.gather(
            *(i.stop() for i in self._instances.values()),
            return_exceptions=True,
        )

    async def run_start_hooks(self) -> None:
        for inst in self._instances.values():
            if inst.ctx is not None and inst.state is PluginState.ACTIVE:
                await inst.ctx.run_start_hooks()

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "state": inst.state, "error": inst.error}
            for name, inst in sorted(self._instances.items())
        ]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_javis/plugins/test_registry.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add javis/plugins/registry.py tests/test_javis/plugins/test_registry.py
git commit -m "feat(plugins): PluginRegistry drives activation/shutdown and reports LoadReport"
```

---

### 任务 5：loader（目录扫描 + 元数据提取 + 配置注入）

**文件：**
- 创建：`javis/plugins/loader.py`
- 测试：`tests/test_javis/plugins/test_loader.py`
- 创建：`tests/test_javis/plugins/fixtures/`（6 个插件文件）

- [ ] **步骤 1：编写测试插件 fixtures**

```python
# tests/test_javis/plugins/fixtures/simple_apply.py（形态①：模块级 apply）
"""Fixture: plain apply function."""


def apply(ctx, config):
    ctx.provide("simple-svc", config or "no-config")
```

```python
# tests/test_javis/plugins/fixtures/declarative.py（形态②：声明式变量）
"""Fixture: module-level Config/inject/name + apply."""
from pydantic import BaseModel


class Config(BaseModel):
    greeting: str = "hi"


inject = ["tools"]
name = "decl-plugin"


def apply(ctx, config):
    ctx.provide("decl-svc", config.greeting)
```

```python
# tests/test_javis/plugins/fixtures/object_form.py（形态③：plugin 对象）
"""Fixture: module-level plugin dict."""
from pydantic import BaseModel


class Config(BaseModel):
    n: int = 1


def _apply(ctx, config):
    ctx.provide("obj-svc", config.n)


plugin = {"name": "obj-plugin", "Config": Config, "apply": _apply}
```

```python
# tests/test_javis/plugins/fixtures/multi.py（形态④：__plugins__ 列表）
"""Fixture: one file exporting two plugins."""
from pydantic import BaseModel


class CfgA(BaseModel):
    tag: str = "a"


class CfgB(BaseModel):
    tag: str = "b"


def apply_a(ctx, config):
    ctx.provide("multi-a", config.tag)


def apply_b(ctx, config):
    ctx.provide("multi-b", config.tag)


class PluginA:
    name = "multi-a"
    Config = CfgA
    apply = staticmethod(apply_a)


class PluginB:
    name = "multi-b"
    Config = CfgB
    apply = staticmethod(apply_b)


__plugins__ = [PluginA, PluginB]
```

```python
# tests/test_javis/plugins/fixtures/bad_syntax.py（import 失败）
"""Fixture: syntax error that must not break other plugins."""
def broken(:
    pass
```

```python
# tests/test_javis/plugins/fixtures/needs_config.py（配置校验）
"""Fixture: config validation failure surfaces as PluginConfigError."""
from pydantic import BaseModel


class Config(BaseModel):
    count: int  # required


def apply(ctx, config):
    ctx.provide("needs-config", config.count)
```

- [ ] **步骤 2：编写失败的测试**

```python
# tests/test_javis/plugins/test_loader.py
"""Tests for the plugin loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginState
from javis.plugins.loader import (
    discover_plugin_files,
    extract_plugins,
    load_plugins,
    plugin_dirs,
)
from javis.plugins.registry import PluginRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def test_discover_scans_py_files_and_dirs(tmp_path):
    (tmp_path / "a.py").write_text("def apply(ctx, config): pass\n")
    (tmp_path / "bdir").mkdir()
    (tmp_path / "bdir" / "__init__.py").write_text("def apply(ctx, config): pass\n")
    found = [name for _path, name in discover_plugin_files([tmp_path])]
    assert sorted(found) == ["a", "bdir"]


def test_discover_later_dir_wins_on_name_collision(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "dup.py").write_text("def apply(ctx, config): pass\n")
    other = tmp_path / "other"
    other.mkdir()
    (other / "dup.py").write_text("def apply(ctx, config): pass\n")
    found = dict(discover_plugin_files([tmp_path / "p", other]))
    assert Path(found["dup"]) == other / "dup.py"


def test_extract_apply_function():
    module = _load("simple_apply")
    specs = extract_plugins(module, "simple_apply")
    assert len(specs) == 1
    assert specs[0].name == "simple_apply"
    assert specs[0].config_model is None
    assert specs[0].inject == []


def test_extract_declarative():
    module = _load("declarative")
    specs = extract_plugins(module, "declarative")
    assert specs[0].name == "decl-plugin"
    assert specs[0].config_model is not None
    assert specs[0].inject == ["tools"]


def test_extract_object_form():
    module = _load("object_form")
    specs = extract_plugins(module, "object_form")
    assert specs[0].name == "obj-plugin"
    assert specs[0].config_model is not None


def test_extract_multi():
    module = _load("multi")
    specs = extract_plugins(module, "multi")
    assert [s.name for s in specs] == ["multi-a", "multi-b"]


def _load(module_name):
    import importlib.util

    path = FIXTURES / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_fixture_{module_name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def reg():
    services = ServiceRegistry()
    bus = EventBus()

    def _build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, bus=bus, javis_config=None
        )

    return PluginRegistry(services=services, bus=bus, ctx_builder=_build)


@pytest.mark.asyncio
async def test_load_plugins_injects_config_and_activates(reg):
    # decl-plugin declares inject=["tools"]; provide the service before activating
    reg.services.provide("tools", type("T", (), {"register_tool": lambda self, t: None})())
    reg.services.provide("commands", type("C", (), {"register": lambda self, c: None})())
    reg.services.provide("engines", type("E", (), {"register_engine": lambda self, n, f: None})())

    plugins_cfg = {
        "decl-plugin": {"enabled": True, "config": {"greeting": "from-cfg"}},
        "object_form": {"enabled": True, "config": {"n": 7}},
    }
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    await reg.activate_all()
    assert reg.get("decl-plugin").config.greeting == "from-cfg"
    assert reg.get("obj-plugin").config.n == 7
    assert reg.get("simple_apply").config is None
    assert reg.get("decl-plugin").state is PluginState.ACTIVE  # inject=["tools"] satisfied


@pytest.mark.asyncio
async def test_load_plugins_disabled_skipped(reg):
    plugins_cfg = {"simple_apply": {"enabled": False}}
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    assert reg.get("simple_apply") is None


@pytest.mark.asyncio
async def test_load_plugins_isolates_bad_syntax(reg, caplog):
    plugins_cfg = {}
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    # bad_syntax.py must not exist as a plugin; others still loaded
    assert reg.get("bad_syntax") is None
    assert reg.get("simple_apply") is not None


@pytest.mark.asyncio
async def test_plugin_dirs_global_then_project(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "ws"))
    from javis.session.workspace import get_workspace_root

    project = tmp_path / "proj" / ".javis"
    project.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "proj")
    dirs = plugin_dirs(cwd=str(tmp_path / "proj"))
    assert len(dirs) >= 1
    assert dirs[0].name == "plugins"
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_javis/plugins/test_loader.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'javis.plugins.loader'`

- [ ] **步骤 4：编写 loader.py**

```python
# javis/plugins/loader.py
"""Local-directory plugin loader.

Directory sources are an ordered list (global, then project) — the profile
layer is reserved: a future profile only inserts one more directory.
Each plugin is a ``.py`` file or a directory with ``__init__.py``.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from javis.plugins.instance import PluginInstance
from javis.plugins.registry import LoadReport, PluginRegistry
from javis.session.workspace import find_project_javis_dir, get_workspace_root

log = logging.getLogger(__name__)


@dataclass
class PluginSpec:
    name: str
    apply: Callable
    config_model: type | None
    inject: list[str]


def plugin_dirs(cwd: str | None = None, workspace: str | Path | None = None) -> list[Path]:
    """Ordered plugin directory sources: global, then project. Profile layer reserved."""
    root = get_workspace_root(workspace)
    dirs = [Path(root) / "plugins"]
    project_dir = find_project_javis_dir(cwd)
    if project_dir is not None and project_dir.resolve() != Path(root).resolve():
        dirs.append(project_dir / "plugins")
    return dirs


def discover_plugin_files(dirs: list[Path]) -> list[tuple[Path, str]]:
    """Return [(path, plugin_name)] in dir order; same-name later dir wins."""
    found: dict[str, Path] = {}
    for directory in dirs:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                found[entry.stem] = entry
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                found[entry.name] = entry
    return [(path, name) for name, path in found.items()]


def extract_plugins(module: Any, fallback_name: str) -> list[PluginSpec]:
    """Extract plugin specs from a loaded module (four forms, see spec §4)."""
    specs: list[PluginSpec] = []

    # Form ④: __plugins__ list wins if present.
    if getattr(module, "__plugins__", None):
        for entry in module.__plugins__:
            name = getattr(entry, "name", None) or getattr(entry, "Config", None) or fallback_name
            specs.append(
                PluginSpec(
                    name=str(getattr(entry, "name", fallback_name)),
                    apply=getattr(entry, "apply"),
                    config_model=getattr(entry, "Config", None),
                    inject=list(getattr(entry, "inject", [])),
                )
            )
        return specs

    apply_fn = getattr(module, "apply", None)
    if apply_fn is None:
        # Form ③: module-level plugin dict.
        plugin_obj = getattr(module, "plugin", None)
        if isinstance(plugin_obj, dict):
            apply_fn = plugin_obj.get("apply")
            if apply_fn is not None:
                specs.append(
                    PluginSpec(
                        name=str(plugin_obj.get("name", fallback_name)),
                        apply=apply_fn,
                        config_model=plugin_obj.get("Config"),
                        inject=list(plugin_obj.get("inject", [])),
                    )
                )
        return specs

    # Forms ① and ②: module-level apply (+ optional Config/inject/name).
    specs.append(
        PluginSpec(
            name=str(getattr(module, "name", fallback_name)),
            apply=apply_fn,
            config_model=getattr(module, "Config", None),
            inject=list(getattr(module, "inject", [])),
        )
    )
    return specs


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


async def load_plugins(
    registry: PluginRegistry,
    dirs: list[Path],
    plugins_cfg: dict[str, Any],
) -> LoadReport:
    """Discover, import and register plugin instances. Never raises.

    ``plugins_cfg`` is the ``config.plugins`` dict: ``{name: {enabled, config}}``.
    Import failures are isolated per plugin (logged, skipped).
    """
    report = LoadReport()
    for path, name in discover_plugin_files(dirs):
        entry = plugins_cfg.get(name, {})
        if not entry.get("enabled", True):
            report.skipped.append(name)
            continue
        try:
            module = _load_module(path, f"javis_plugin_{name}")
            specs = extract_plugins(module, name)
        except Exception as exc:
            log.warning("Plugin %r failed to load from %s: %s", name, path, exc)
            report.skipped.append(name)
            report.errors[name] = str(exc)
            continue
        for spec in specs:
            raw_config = dict(plugins_cfg.get(spec.name, {}).get("config", {}))
            instance = PluginInstance(
                name=spec.name,
                apply_fn=spec.apply,
                config_model=spec.config_model,
                inject=spec.inject,
                raw_config=raw_config,
                ctx_builder=registry.ctx_builder,
                services=registry.services,
            )
            registry.add(instance)
    return report
```

`registry.ctx_builder`：PluginRegistry 需要暴露 ctx_builder——把 ctx_builder 作为 registry 构造参数（由 runtime 提供）：

```python
class PluginRegistry:
    def __init__(self, *, services: ServiceRegistry, bus: EventBus, ctx_builder: CtxBuilder) -> None:
        ...
        self.ctx_builder = ctx_builder
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_javis/plugins/test_loader.py -q`
预期：PASS。若 `test_plugin_dirs_global_then_project` 因 `JAVIS_WORKSPACE` 语义不符而失败，调整断言为仅检查返回列表非空且第一个目录名为 `plugins`。

- [ ] **步骤 6：Commit**

```bash
git add javis/plugins/loader.py tests/test_javis/plugins/test_loader.py tests/test_javis/plugins/fixtures/
git commit -m "feat(plugins): local-directory loader with four plugin forms and config injection"
```

---

### 任务 6：接入 runtime（build / start / close / config）

**文件：**
- 修改：`javis/host/runtime.py`
- 修改：`javis/plugins/registry.py`（加 `ctx_builder` 参数）
- 修改：`javis/plugins/loader.py`（`load_plugins` 的 dirs 从 `plugin_dirs()` 取）
- 测试：`tests/test_javis/plugins/test_runtime_integration.py`

- [ ] **步骤 1：编写失败的集成测试**

```python
# tests/test_javis/plugins/test_runtime_integration.py
"""End-to-end: plugins loaded through build_javis_runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.host.runtime import build_javis_runtime
from tests.test_javis.fake_backend import FakeBackend

PLUGIN_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_build_loads_plugins_from_plugin_dirs(isolated_env, monkeypatch):
    from javis.host.runtime import _build_plugin_dirs

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs",
                        lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert bundle.plugins is not None
    names = {p["name"] for p in bundle.plugins.list_plugins()}
    assert {"simple_apply", "decl-plugin", "obj-plugin", "multi-a", "multi-b"} <= names


@pytest.mark.asyncio
async def test_plugin_command_registered_into_bundle(isolated_env, monkeypatch):
    from javis.host.runtime import _build_plugin_dirs

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert bundle.commands.lookup("/plughello") is not None  # plugin command
    assert bundle.commands.lookup("/help") is not None  # built-ins still present


@pytest.mark.asyncio
async def test_close_runtime_runs_disposers(isolated_env, monkeypatch):
    from javis.host.runtime import _build_plugin_dirs, close_runtime

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    await close_runtime(bundle)
    for p in bundle.plugins.list_plugins():
        assert p["state"].value == "disposed"
```

为了让集成测试可断言"插件命令进入 bundle"，在 fixtures 加一个命令插件：

```python
# tests/test_javis/plugins/fixtures/command_plugin.py
"""Fixture: registers a slash command through the plugin API."""
from javis.commands.registry import Command, CommandContext, CommandResult


async def _handler(args: str, context: CommandContext) -> CommandResult:
    return CommandResult(message=f"plugin-echo {args}")


def apply(ctx, config):
    ctx.register_command(Command("plughello", "Plugin command", _handler))
```

集成测试断言：

```python
@pytest.mark.asyncio
async def test_plugin_command_registered_into_bundle(isolated_env, monkeypatch):
    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert bundle.commands.lookup("/plughello") is not None
    assert bundle.commands.lookup("/help") is not None
```

工具进入引擎的断言（corecoder 注册表是模块级，用唯一工具名避免与其他测试互相干扰）：

```python
# tests/test_javis/plugins/fixtures/tool_plugin.py
"""Fixture: registers a tool through the plugin API."""
from corecoder.tools.base import Tool


class PlugTool(Tool):
    name = "plug_tool"
    description = "tool registered by plugin"
    parameters = {"type": "object", "properties": {"x": {"type": "integer"}}}

    def execute(self, **kwargs) -> str:
        return str(kwargs.get("x", 0))


def apply(ctx, config):
    ctx.register_tool(PlugTool())
```

```python
@pytest.mark.asyncio
async def test_plugin_tool_registered_into_corecoder(isolated_env, monkeypatch):
    from corecoder.tools import get_tool

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert get_tool("plug_tool") is not None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_javis/plugins/test_runtime_integration.py -q`
预期：FAIL，`build_javis_runtime` 尚未接入插件系统（`bundle.plugins` 不存在）

- [ ] **步骤 3：接入 runtime.py**

**先加 ruff exclude**（任务 5 的 `tests/test_javis/plugins/fixtures/bad_syntax.py` 故意含语法错误，`ruff check` 会报 invalid-syntax 失败）：

```toml
# pyproject.toml [tool.ruff] 段，line-length 下方新增：
exclude = ["tests/**/fixtures/**"]
```

在 `javis/host/runtime.py` 顶部 import 增加：

```python
from javis.plugins import (
    PluginContext,
    PluginRegistry,
    PluginState,
    load_plugins,
    plugin_dirs,
)
from javis.plugins.context import EventBus, ServiceRegistry
```

在 `RuntimeBundle` 增加字段：

```python
@dataclass
class RuntimeBundle:
    ...
    plugins: PluginRegistry | None = None
```

新增模块级辅助函数（集成测试 monkeypatch 的目标）：

```python
def _build_plugin_dirs(*, cwd: str | None = None, workspace: str | Path | None = None) -> list[Path]:
    return plugin_dirs(cwd=cwd, workspace=workspace)
```

**cfg 解析提到函数顶部**（engine 分支外），插件系统在 `cfg` 非 None 时总是激活（显式 `agent_backend` 的调用也能用插件命令/工具）。原 engine 分支内 `cfg = load_config(...)` 移除，改为复用顶部变量：

```python
    from javis.session.config import load_config
    cfg = load_config(cwd=cwd_resolved, workspace=workspace_root)  # 函数顶部，无条件
    plugins: PluginRegistry | None = None
```

`load_config` 深合并 global + project config.json；非法 JSON 会 raise `ValueError`（配置错误直接暴露，不吞）；否则恒返回 `JavisConfig`（默认模板兜底），因此 `cfg` 永远非 None，插件系统始终激活。注意：显式传 `agent_backend` 的调用同样会读取/创建配置文件。在 `create_agent_backend` 之前插入插件激活块：

```python
        # --- plugin system: activate before the backend is built so plugin
        # tools reach the engine via all_tools() ---
        from javis.commands.registry import create_default_command_registry
        from corecoder.tools import all_tools

        services = ServiceRegistry()
        bus = EventBus()
        commands = create_default_command_registry()

        def _make_ctx(name: str, config: Any) -> PluginContext:
            return PluginContext(
                name=name, config=config, services=services, bus=bus,
                javis_config=cfg,
            )

        registry = PluginRegistry(
            services=services, bus=bus, ctx_builder=_make_ctx,
        )
        # built-in services (owner=None, never revoked)
        services.provide("tools", _tool_module())
        services.provide("commands", commands)
        services.provide("engines", _engine_module())
        services.provide("config", cfg)

        plugins_cfg = dict(getattr(cfg, "plugins", {}) or {})
        await load_plugins(registry, _build_plugin_dirs(cwd=cwd_resolved, workspace=workspace_root), plugins_cfg)
        await registry.activate_all()
        del all_tools  # tools flow into the backend factory via corecoder.tools
```

其中 `_tool_module` / `_engine_module` 辅助：

```python
def _tool_module():
    import corecoder.tools

    return corecoder.tools


def _engine_module():
    import javis.engines

    return javis.engines
```

engine 分支的 `create_agent_backend(...)` 调用**不需要改动**——backend.py 已改为 `tools=all_tools()`（任务 1），插件工具已在注册表中。

`RuntimeBundle` 构造处把 `commands=create_default_command_registry()` 替换为插件块中创建的 `commands`（`plugins` 同样传入）：

```python
    return RuntimeBundle(
        engine=engine_obj,
        cwd=cwd_resolved,
        app_state=app_state,
        commands=commands if cfg is not None else create_default_command_registry(),
        session_backend=session_backend or JavisSessionBackend(workspace_root),
        session_id=session_id,
        system_prompt=system_prompt_text,
        plugins=plugins,
    )
```

`start_runtime` / `close_runtime` 变为真实生命周期：

```python
async def start_runtime(bundle: RuntimeBundle) -> None:
    """Run plugin on_start hooks (application-level startup)."""
    if bundle.plugins is not None:
        await bundle.plugins.run_start_hooks()


async def close_runtime(bundle: RuntimeBundle) -> None:
    """Stop all plugins: disposers reverse-order, services revoked."""
    if bundle.plugins is not None:
        await bundle.plugins.close_all()
```

- [ ] **步骤 4：更新 javis/plugins/__init__.py 公开 API**

```python
"""javis plugin system — cordis-like kernel.

Public API:
- PluginContext (services / events / lifecycle hooks)
- PluginInstance + PluginState (state machine)
- PluginRegistry (activation / shutdown / report)
- ServiceRegistry / EventBus (kernel primitives)
- loader helpers: load_plugins / plugin_dirs
- errors
"""

from __future__ import annotations

from javis.plugins.context import EventBus, PluginContext, ServiceRegistry
from javis.plugins.errors import (
    PluginConfigError,
    PluginDependencyError,
    PluginError,
)
from javis.plugins.instance import PluginInstance, PluginState
from javis.plugins.loader import load_plugins, plugin_dirs
from javis.plugins.registry import LoadReport, PluginRegistry

__all__ = [
    "EventBus",
    "LoadReport",
    "PluginConfigError",
    "PluginContext",
    "PluginDependencyError",
    "PluginError",
    "PluginInstance",
    "PluginRegistry",
    "PluginState",
    "ServiceRegistry",
    "load_plugins",
    "plugin_dirs",
]
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_javis/plugins/ tests/test_javis/test_runtime.py -q`
预期：PASS（既有 runtime 测试不受影响——插件系统在 `cfg is None` 时跳过）

- [ ] **步骤 6：Commit**

```bash
git add javis/host/runtime.py javis/plugins/__init__.py tests/test_javis/plugins/test_runtime_integration.py tests/test_javis/plugins/fixtures/
git commit -m "feat(plugins): wire plugin loading into build_javis_runtime; real start/close lifecycle"
```

---

### 任务 7：示例插件 + 示例可用性测试

**文件：**
- 创建：`examples/plugins/hello_tool.py`
- 创建：`examples/plugins/hello_command.py`
- 测试：`tests/test_javis/plugins/test_examples.py`

- [ ] **步骤 1：编写示例插件**

```python
# examples/plugins/hello_tool.py
"""Example plugin: register a custom tool.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.
"""

from __future__ import annotations

from corecoder.tools.base import Tool


class GreetTool(Tool):
    name = "greet"
    description = "Greet someone by name"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to greet"},
        },
        "required": ["name"],
    }

    def execute(self, **kwargs) -> str:
        return f"Hello, {kwargs.get('name', 'world')}!"


def apply(ctx, config):
    """Register the tool. ``config`` is None unless the plugin declares a Config."""
    ctx.register_tool(GreetTool())
```

```python
# examples/plugins/hello_command.py
"""Example plugin: register a slash command.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.
"""

from __future__ import annotations

from javis.commands.registry import Command, CommandContext, CommandResult


async def _hello_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    return CommandResult(message=f"Hello from plugin! args={args!r}")


def apply(ctx, config):
    ctx.register_command(Command("hello", "Say hello from a plugin", _hello_handler))
```

- [ ] **步骤 2：编写示例可用性测试**

```python
# tests/test_javis/plugins/test_examples.py
"""The shipped examples must load and register their extensions."""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginState
from javis.plugins.loader import load_plugins
from javis.plugins.registry import PluginRegistry

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "plugins"


@pytest.fixture
def reg():
    services = ServiceRegistry()
    bus = EventBus()

    def _build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, bus=bus, javis_config=None
        )

    return PluginRegistry(services=services, bus=bus, ctx_builder=_build)


@pytest.mark.asyncio
async def test_example_tool_plugin_loads_and_registers(reg):
    assert EXAMPLES.is_dir(), f"examples dir missing: {EXAMPLES}"
    await load_plugins(reg, [EXAMPLES], {"hello_tool": {}, "hello_command": {}})
    await reg.activate_all()
    from corecoder.tools import get_tool

    assert get_tool("greet") is not None
    tool = get_tool("greet")
    assert tool.execute(name="pi") == "Hello, pi!"
    assert reg.get("hello_tool").state is PluginState.ACTIVE
```

```python
@pytest.mark.asyncio
async def test_example_command_plugin_registers_command(reg):
    from javis.commands.registry import CommandRegistry

    commands = CommandRegistry()
    reg.services.provide("commands", commands)
    await load_plugins(reg, [EXAMPLES], {"hello_tool": {}, "hello_command": {}})
    await reg.activate_all()
    assert commands.lookup("/hello") is not None
    assert commands.lookup("/help") is None  # 内建命令不在此 registry
```

- [ ] **步骤 3：运行测试验证失败/通过**

运行：`uv run pytest tests/test_javis/plugins/test_examples.py -q`
预期：PASS（examples 加载并注册 greet 工具与 /hello 命令）

- [ ] **步骤 4：Commit**

```bash
git add examples/plugins/ tests/test_javis/plugins/test_examples.py
git commit -m "feat(plugins): example tool and command plugins with availability test"
```

---

### 任务 8：用户文档 docs/plugins.md

**文件：**
- 创建：`docs/plugins.md`

- [ ] **步骤 1：编写文档**

```markdown
# javis 插件系统

javis 借鉴 DeepSeek Harness 的 cordis 模型实现了轻量插件内核：
插件 = `apply(ctx, config)`，`ctx` 提供服务仓库、事件与生命周期钩子；
`PluginInstance` 状态机跟踪每个插件的生命周期（PENDING→LOADING→ACTIVE/FAILED→UNLOADING→DISPOSED）。

## 快速开始

1. 建目录 `~/.javis/plugins/`（全局）或 `<项目>/.javis/plugins/`（项目级）
2. 放入一个 `.py` 文件（见 `examples/plugins/`）
3. 启动 javis —— 插件在启动时自动加载

## 插件形态（四种）

1. 模块级 `apply(ctx, config)` 函数
2. 模块级声明式变量：`Config`（pydantic）/ `inject` / `name` + `apply`
3. 模块级 `plugin = {"name": ..., "Config": ..., "inject": [...], "apply": ...}` 对象
4. 模块级 `__plugins__ = [...]` 列表（一个文件多个插件）

`apply` 可以返回一个 disposer（或 async disposer），插件卸载时逆序执行。

## 配置

```json
{
  "plugins": {
    "hello": { "enabled": true, "config": { "greeting": "你好" } }
  }
}
```

- `enabled: false` 跳过该插件
- `config` 用插件声明的 pydantic `Config` 校验后传入 `apply` 的第二个参数
- 校验失败 → 该插件 FAILED，不影响其他插件

## ctx API

| 方法 | 说明 |
|---|---|
| `ctx.register_tool(tool)` | 注册工具（进入引擎工具集） |
| `ctx.register_command(cmd)` | 注册斜杠命令 |
| `ctx.register_engine(name, factory)` | 注册引擎（预留） |
| `ctx.provide(name, value)` / `ctx.get(name)` | 跨插件服务 |
| `ctx.on(event, handler)` / `ctx.emit(event, payload)` | 事件（fire-and-forget） |
| `ctx.emit_serial(event, payload)` | 事件（等待所有 handler） |
| `ctx.effect(disposer)` / `ctx.on_close(fn)` | 卸载清理（逆序） |
| `ctx.on_start(fn)` | 应用启动钩子 |
| `ctx.config` / `ctx.logger` | 校验后的插件配置 / 独立 logger |

`inject = ["service-name"]` 声明依赖：依赖服务未提供时插件停在 PENDING，提供后自动继续；超时（10s）未提供则 FAILED。

## 生命周期

```
启动: build_javis_runtime → 扫描目录 → import → 并行激活（依赖等待 → apply → ACTIVE）
运行: 工具/命令直接可用；start_runtime 触发 on_start 钩子
退出: close_runtime → 逆序执行 disposers → 撤销服务 → DISPOSED
```

## 设计预留

- **profile**：`~/.javis/profiles/<name>/plugins/` 未来作为第三层目录源（`javis --profile <name>`）
- **热重载**：PluginInstance 模型支持 dispose + 重建，未来加目录 watcher 即可

## 调试

- 日志：`javis.plugins`（WARNING 及以上默认可见；`-v` 看 debug）
- 插件加载失败只影响该插件，不阻塞启动；失败原因在日志中
```

- [ ] **步骤 2：校验文档引用与 API 一致**

运行：`uv run pytest tests/ -q`
预期：全部通过（77 个既有测试 + 新增插件测试）

- [ ] **步骤 3：Commit**

```bash
git add docs/plugins.md
git commit -m "docs(plugins): user guide for the plugin system"
```

---

## 自检记录

- **规格覆盖度**：§3 六模块 → 任务 2-5；§4 四种形态 → 任务 5（fixtures 全覆盖）；§5 ctx 服务/事件/钩子 → 任务 2；§6 状态机/依赖等待 → 任务 3；§7 加载器 → 任务 5；§8 配置注入 → 任务 5；§9 扩展点矩阵（工具/命令/生命周期 MVP、引擎薄封装、provider 预留）→ 任务 1/2/6；§10 工具注册表化 → 任务 1；§11 profile 预留 → `plugin_dirs` 注释；§12 错误处理 → 任务 3（FAILED）/任务 5（import 隔离）/LoadReport；§13 测试策略 → 任务 1-7；§14 落地 A-D → 任务 1-8；§16 影响面 → 任务 1/6。
- **占位符扫描**：无 TODO/待定；所有代码步骤含完整代码。
- **类型一致性**：`PluginContext(name, config, services, bus, javis_config)`、`PluginInstance(name, apply_fn, config_model, inject, raw_config, ctx_builder, services, start_timeout)`、`PluginRegistry(services, bus, ctx_builder)` 在各任务中签名一致；`register_tool/get_tool/all_tools`、`PluginState` 枚举值（`"disposed"` 等）跨任务一致。
