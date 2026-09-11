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
