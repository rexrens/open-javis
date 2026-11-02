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
