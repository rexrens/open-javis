"""Fixture: registers a slash command through the plugin API."""
from javis.commands.registry import Command, CommandContext, CommandResult


async def _handler(args: str, context: CommandContext) -> CommandResult:
    return CommandResult(message=f"plugin-echo {args}")


def apply(ctx, config):
    ctx.register_command(Command("plughello", "Plugin command", _handler))
