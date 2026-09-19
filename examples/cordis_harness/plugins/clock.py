"""示例插件：给模型加一个工具，并追加一段系统提示词。

演示三件事：``inject`` 拿服务、``ctx.tools.register`` 注册工具、
``ctx.systemPrompt.register_section`` 加提示词段落——两个注册一起回滚。
"""

from datetime import datetime

from pydantic import BaseModel

from harness.tools import ToolDefinition

name = "clock"

inject = ["tools", "systemPrompt"]


class Config(BaseModel):
    format: str = "%Y-%m-%d %H:%M:%S"


def apply(ctx, config: Config):
    def clock(args, cwd):
        return datetime.now().strftime(args.get("format") or config.format)

    tool = ToolDefinition(
        name="clock",
        description="Report the current local time.",
        parameters={
            "type": "object",
            "properties": {"format": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=clock,
        approval=False,  # 只读工具：不需要人工确认
    )

    return [
        ctx.get("tools").register(tool),
        ctx.get("systemPrompt").register_section(
            "time-policy",
            lambda tools, cwd: "需要当前时间时调用 clock 工具，不要凭记忆猜。",
        ),
    ]
