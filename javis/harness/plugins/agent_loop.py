"""组合行：``agentLoop`` 服务 —— 循环驱动器配置（含历史压缩）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from javis.contracts.services import AGENT_LOOP_SERVICE

from ..compression import HISTORY_MAX_MESSAGES, HistoryCompressor
from ..types import AgentLoopService, MutableLoopConfig

name = "javis.harness.plugins.agent_loop"


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
