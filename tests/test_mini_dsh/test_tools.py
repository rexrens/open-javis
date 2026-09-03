"""Tool registry + execute_tool_calls scheduling semantics."""
import json

import pytest
from core import types as t
from core.session import Session
from core.tools import Tool, ToolRegistry, execute_tool_calls

from javis.cordis import Context


def _tc(id: str, name: str, arguments: dict) -> t.ToolCallBlock:
    return t.ToolCallBlock(id=id, name=name, arguments=json.dumps(arguments))


def _agent():
    return type("A", (), {"session": None})()


def _ctx_with_tools(max_parallel: int = 2) -> tuple[Context, ToolRegistry, list[dict]]:
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=max_parallel)))
    log: list[dict] = []
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)

    def run(name: str) -> callable:
        def body(_input):
            log.append({"tool": name, "at": len(log)})
            return f"{name}-done"
        return body

    registry.register(Tool(name="a", body=run("a"), mode="parallel"))
    registry.register(Tool(name="x", body=run("x"), mode="exclusive"))
    registry.register(Tool(name="b", body=run("b"), mode="parallel"))
    return ctx, registry, log


def test_registry_register_get_schemas_modes():
    ctx = Context()
    registry = ToolRegistry(ctx)
    registry.register(Tool(name="t1", body=lambda _i: "ok"))
    assert registry.get("t1") is not None
    assert [s.name for s in registry.schemas()] == ["t1"]
    registry.register(Tool(name="t2", body=lambda _i: "ok", mode="exclusive"))
    assert isinstance(registry.execution_mode("t2"), t.ExclusiveMode)
    assert isinstance(registry.execution_mode("t1"), t.ParallelMode)
    with pytest.raises(ValueError):
        registry.register(Tool(name="t1", body=lambda _i: "dup"))


@pytest.mark.asyncio
async def test_exclusive_barrier_before_parallel_pool():
    ctx, _registry, log = _ctx_with_tools()
    session = Session("s1")
    agent = _agent()
    turn = step = 1
    calls = [_tc("x", "x", {}), _tc("a", "a", {}), _tc("b", "b", {})]
    concluded = await execute_tool_calls(
        ctx,
        session,
        agent,
        turn,
        step,
        calls,
        t.AbortSignal(),
        accept_context=lambda msg: None,
    )
    # 三个普通工具均无 concludesTurn → javis/dsh 语义下返回 False，loop 会再走
    # 一步让模型看到结果后收尾（只有 end_session 类工具才会让 batch 直接收尾）。
    assert concluded is False
    order = [entry["tool"] for entry in log]
    assert order == ["x", "a", "b"]  # exclusive 屏障先于 parallel 池
    # 仓库规范读法：tool/result 的 message.content[0] 是 ToolResultBlock，解包取文本
    # （对照 tests/test_demo_harness.py 的 tool_result_text helper）。
    results = [
        "".join(
            block.text
            for block in e.data["message"].content[0].content
            if isinstance(block, t.TextBlock)
        )
        for e in session.events_of("tool/result")
    ]
    assert results == ["x-done", "a-done", "b-done"]
