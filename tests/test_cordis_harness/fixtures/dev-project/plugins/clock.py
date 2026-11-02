"""Project-local plugin: a tool plus the prompt section that advertises it."""

from datetime import datetime

from pydantic import BaseModel

from harness.tools import ToolDefinition

name = "clock"

#: Load only once the tool registry and the prompt assembler exist.
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
        approval=False,
    )

    # Two registrations, rolled back together on unload.
    return [
        ctx.get("tools").register(tool),
        ctx.get("systemPrompt").register_section(
            "time-policy",
            lambda tools, cwd: "需要当前时间时调用 clock 工具，不要凭记忆猜。",
        ),
    ]
