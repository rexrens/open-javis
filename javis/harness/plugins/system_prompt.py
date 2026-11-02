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
