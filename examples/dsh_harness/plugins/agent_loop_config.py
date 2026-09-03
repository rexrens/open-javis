"""插件：循环驱动器配置（服务 ``"agentLoop"``）。

dsh 的工具调用调度器会读 ``ctx.agentLoop.config.maxParallelToolCalls``
决定并行池上限，这里 ``tools`` 服务以同样方式读取。本插件只干一件事：
把这份配置以 ``agentLoop`` 服务的名义发布到组合里，供 driver 装配
ReactLoopAgent 时取用。
"""

from pydantic import BaseModel, Field

from javis.harness.types import AgentLoop, AgentLoopConfig

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "agent-loop-config"


class Config(BaseModel):
    """插件配置 schema：cordis.yml 的 ``config:`` 字段按此校验。

    ``max_parallel_tool_calls`` 是工具调度的并行池上限——同一 step 里
    最多几个 parallel 模式工具同时跑（exclusive 工具不受此限，它本身
    就是屏障）。demo 取 2，正好让 tools 场景的 weather×2 并行对打满上限。
    """

    max_parallel_tool_calls: int = Field(default=2, ge=1)


def apply(ctx, config):
    # 发布 agentLoop 服务：AgentLoop 包着一份 AgentLoopConfig，
    # 循环的工具调度器从 config.max_parallel_tool_calls 读并行上限。
    ctx.provide(
        "agentLoop",
        AgentLoop(AgentLoopConfig(max_parallel_tool_calls=config.max_parallel_tool_calls)),
    )
