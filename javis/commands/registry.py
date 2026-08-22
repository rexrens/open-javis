"""Minimal slash-command registry for javis.

Forked conceptually from openharness.commands but trimmed to the essentials:
register commands, look them up by ``/name``, dispatch. No plugin hooks, no
skill lookup, no memory backend — just built-in commands.

To add a command: register it in ``create_default_command_registry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from javis.contracts.messages import ConversationMessage
from javis.session.state import AppStateStore
from javis.contracts.usage import UsageSnapshot


@dataclass
class CommandContext:
    """Context passed to a command handler."""

    engine: object  # QueryEngine-shaped; typed loosely to avoid a circular import
    app_state: AppStateStore
    cwd: str
    session_id: str


@dataclass
class CommandResult:
    """Outcome of running a slash command."""

    message: str | None = None
    should_exit: bool = False
    clear_screen: bool = False
    submit_prompt: str | None = None
    continue_pending: bool = False
    continue_turns: int | None = None
    replay_messages: list[ConversationMessage] | None = None


CommandHandler = Callable[[str, CommandContext], Awaitable[CommandResult]]


@dataclass
class Command:
    """One slash command."""

    name: str
    description: str
    handler: CommandHandler


class CommandRegistry:
    """Lookup table for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def lookup(self, line: str) -> tuple[Command, str] | None:
        """Return ``(command, args)`` if ``line`` is a registered slash command."""
        if not line.startswith("/"):
            return None
        stripped = line[1:]
        parts = stripped.split(None, 1)
        if not parts:
            return None
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        command = self._commands.get(name)
        if command is None:
            return None
        return command, args

    def list_commands(self) -> list[Command]:
        return list(self._commands.values())

    def help_text(self) -> str:
        lines = ["Available commands:"]
        for command in self._commands.values():
            lines.append(f"  /{command.name} - {command.description}")
        return "\n".join(lines)


def create_default_command_registry() -> CommandRegistry:
    """Create the built-in command registry."""
    registry = CommandRegistry()

    async def _help_handler(_: str, context: CommandContext) -> CommandResult:
        del context
        return CommandResult(message=registry.help_text())

    async def _exit_handler(_: str, context: CommandContext) -> CommandResult:
        del context
        return CommandResult(should_exit=True)

    async def _clear_handler(_: str, context: CommandContext) -> CommandResult:
        context.engine.clear()
        return CommandResult(message="Conversation cleared.", clear_screen=True)

    async def _version_handler(_: str, context: CommandContext) -> CommandResult:
        del context
        from javis import __version__
        return CommandResult(message=f"javis {__version__}")

    async def _status_handler(_: str, context: CommandContext) -> CommandResult:
        usage: UsageSnapshot = context.engine.total_usage
        state = context.app_state.get()
        return CommandResult(
            message=(
                f"Model: {state.model}\n"
                f"Messages: {len(context.engine.messages)}\n"
                f"Tokens: {usage.total_tokens} (in {usage.input_tokens} / out {usage.output_tokens})\n"
                f"CWD: {context.cwd}\n"
                f"Session: {context.session_id}"
            )
        )

    async def _theme_handler(args: str, context: CommandContext) -> CommandResult:
        value = args.strip()
        if not value:
            return CommandResult(message="Usage: /theme <name>")
        context.app_state.set(theme=value)
        return CommandResult(message=f"Theme set to {value}.")

    async def _turns_handler(args: str, context: CommandContext) -> CommandResult:
        value = args.strip()
        if value.lower() in ("", "unlimited", "none"):
            context.engine.set_max_turns(None)
            return CommandResult(message="Max turns set to unlimited.")
        try:
            turns = int(value)
        except ValueError:
            return CommandResult(message=f"Invalid max turns: {value!r}. Use a number or 'unlimited'.")
        context.engine.set_max_turns(turns)
        return CommandResult(message=f"Max turns set to {turns}.")

    async def _permissions_handler(args: str, context: CommandContext) -> CommandResult:
        value = args.strip().lower()
        if not value:
            return CommandResult(message="Usage: /permissions <default|full_auto|plan>")
        if value not in ("default", "full_auto", "plan"):
            return CommandResult(
                message=f"Invalid permission mode: {value!r}. Use 'default', 'full_auto' or 'plan'."
            )
        context.app_state.set(permission_mode=value)
        context.engine.tool_metadata["permission_mode"] = value
        labels = {"default": "Default", "full_auto": "Auto", "plan": "Plan Mode"}
        return CommandResult(message=f"Permission mode set to {labels[value]}.")

    registry.register(Command("help", "Show this help", _help_handler))
    registry.register(Command("exit", "Exit javis", _exit_handler))
    registry.register(Command("quit", "Exit javis", _exit_handler))
    registry.register(Command("clear", "Clear conversation history", _clear_handler))
    registry.register(Command("version", "Show javis version", _version_handler))
    registry.register(Command("status", "Show session status", _status_handler))
    registry.register(Command("theme", "Set UI theme", _theme_handler))
    registry.register(Command("turns", "Set max turns", _turns_handler))
    registry.register(Command("permissions", "Set permission mode", _permissions_handler))
    return registry


__all__ = [
    "Command",
    "CommandContext",
    "CommandHandler",
    "CommandRegistry",
    "CommandResult",
    "create_default_command_registry",
]
