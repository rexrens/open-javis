"""ReactLoopAgent turn/step loop over a minimal composed ctx."""
import json

import pytest
from core import types as t
from core.agent import ReactLoopAgent
from core.llm import PreparedCall, SystemPrompt, chunk_response
from core.session import Session
from core.tools import Tool, ToolRegistry

from javis.cordis import Context


class _FakeLLM:
    """脚本化 LLM：每 stream() 吐一条 chunk 序列（用 chunk_response 构造）。"""

    def __init__(self, script: list[list]) -> None:
        self._script = script
        self._i = 0
        self.on_tool_call = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def stream(self, options: t.GenerateOptions):
        chunks = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1

        async def gen():
            for chunk in chunks:
                if self.on_tool_call is not None and isinstance(chunk, t.BlockStartChunk) and chunk.block_type == "tool-call":
                    self.on_tool_call()
                yield chunk

        return gen()


def _tc(id: str, name: str, arguments: dict) -> t.ToolCallBlock:
    return t.ToolCallBlock(id=id, name=name, arguments=json.dumps(arguments))


def _compose(script: list[list], *, tools: list[Tool] | None = None) -> tuple[Context, ReactLoopAgent, Session]:
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2)))
    session = Session("test-agent", cwd="/tmp")
    ctx.provide("session", session)
    ctx.provide("systemPrompt", SystemPrompt(ctx, "You are mini.", cwd="/tmp", session_id=session.id))
    ctx.provide("llm", _FakeLLM(script))
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    for tool in tools or []:
        registry.register(tool)
    agent = ReactLoopAgent(ctx, session.id, t.AgentOptions(provider="fake", model="mini"), session)
    ctx.provide("agent", agent)
    return ctx, agent, session


async def _run_turn(agent: ReactLoopAgent, prompt: str) -> None:
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


@pytest.mark.asyncio
async def test_text_turn_completes():
    _, agent, session = _compose(
        [chunk_response(text="2 + 2 = 4.", reasoning="basic arithmetic")]
    )
    await _run_turn(agent, "what is 2+2?")
    events = [e.type for e in session.events]
    assert "turn/start" in events and "turn/end" in events
    messages = session.derive_messages()
    assert any("2 + 2 = 4" in getattr(m, "text", "") for m in messages)


@pytest.mark.asyncio
async def test_tool_turn_executes_and_concludes():
    def note(_input):
        return "note saved"
    tools = [Tool(name="set_note", description="append a note", body=note, mode="exclusive")]
    script = [
        chunk_response(tool_calls=[_tc("n1", "set_note", {"text": "buy milk"})]),
        chunk_response(text="Note saved."),
    ]
    _, agent, session = _compose(script, tools=tools)
    await _run_turn(agent, "save a note")
    # Task 6 裁决（javis parity）：tool/result 的 data["message"].content[0] 是
    # ToolResultBlock，文本从 content[0].content 解包（对照 tests/test_demo_harness.py
    # 的 tool_result_text helper）——message.text 只拼顶层 TextBlock → 空串。
    results = [
        "".join(
            block.text for block in e.data["message"].content[0].content if isinstance(block, t.TextBlock)
        )
        for e in session.events_of("tool/result")
    ]
    assert results == ["note saved"]
    assert session.find_last("turn/end") is not None


@pytest.mark.asyncio
async def test_pre_step_veto_blocks_turn():
    ctx, agent, session = _compose([chunk_response(text="should not appear")])

    def veto(_payload, next):
        return t.PreStepReject(reason="blocked by test")

    ctx.on(t.Events.AGENT_PRE_STEP, veto)
    await _run_turn(agent, "hi")
    end = session.find_last("turn/end")
    assert end is not None
    # veto 必须让 turn 以 blocked 结束（而非 error——veto handler 的异常会被
    # 错误包容机制吞成 TurnError，断言必须区分两者）
    assert end.data["reason"].kind == "blocked"


@pytest.mark.asyncio
async def test_max_steps_per_turn_guard():
    def note(_input):
        return "ok"
    tools = [Tool(name="ping", description="ping", body=note, mode="parallel")]
    # 每一步都调工具 → 超过 max_steps_per_turn（配置为 3）
    script = [chunk_response(tool_calls=[_tc(f"p{i}", "ping", {})]) for i in range(5)]
    script.append(chunk_response(text="done"))
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2, max_steps_per_turn=3)))
    session = Session("guard", cwd="/tmp")
    ctx.provide("session", session)
    ctx.provide("systemPrompt", SystemPrompt(ctx, "mini", cwd="/tmp", session_id=session.id))
    ctx.provide("llm", _FakeLLM(script))
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    registry.register(tools[0])
    agent = ReactLoopAgent(ctx, session.id, t.AgentOptions(provider="fake", model="mini"), session)
    ctx.provide("agent", agent)
    await _run_turn(agent, "go")
    # guard 事件触发，turn 结束（不无限循环）
    assert session.find_last("turn/end") is not None
