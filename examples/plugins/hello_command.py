"""Example plugin: register a slash command.

Put this file (or a copy) into ~/.javis/plugins/ to enable it.
"""

from __future__ import annotations

from javis.commands.registry import Command, CommandContext, CommandRegistry, CommandResult


async def _hello_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    return CommandResult(message=f"Hello from plugin! args={args!r}")


def apply(ctx, config):
    commands = ctx.get("commands", CommandRegistry)
    ctx.effect(commands.register(Command("hello", "Say hello from a plugin", _hello_handler)))
