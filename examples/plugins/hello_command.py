"""Example plugin: register a slash command.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.
"""

from __future__ import annotations

from javis.commands.registry import Command, CommandContext, CommandResult


async def _hello_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    return CommandResult(message=f"Hello from plugin! args={args!r}")


def apply(ctx, config):
    ctx.register_command(Command("hello", "Say hello from a plugin", _hello_handler))
