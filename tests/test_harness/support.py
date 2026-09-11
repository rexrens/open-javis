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
