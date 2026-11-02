"""System-prompt assembly (``ctx.systemPrompt``).

Sections are rendered in registration order; the tool catalogue is appended
from the live registry, so a plugin that registers a tool changes the prompt
without touching this service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .types import ToolSchema

if TYPE_CHECKING:
    from javis.cordis import Context

#: ``(tools, cwd) -> str``
Section = Callable[[list[ToolSchema], str], str]


def default_sections() -> list[tuple[str, Section]]:
    """The built-in prompt sections: identity and environment."""

    def identity(tools: list[ToolSchema], cwd: str) -> str:
        return (
            "You are a coding agent running in the user's terminal.\n"
            "Work on real files and real commands: inspect before you change, keep steps small, "
            "and verify what you claim.\n"
            "Use the provided tools to do the work; a tool result with is_error set means the call "
            "failed and you should adapt instead of repeating it."
        )

    def environment(tools: list[ToolSchema], cwd: str) -> str:
        from datetime import date

        return f"Working directory: {cwd}\nToday's date: {date.today().isoformat()}"

    return [("identity", identity), ("environment", environment)]


class SystemPromptService:
    """Prompt-section registry installed as ``ctx.systemPrompt``."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._sections: list[tuple[str, Section]] = []

    def register_section(self, name: str, render: Section, prepend: bool = False) -> Callable[[], None]:
        entry = (name, render)
        if prepend:
            self._sections.insert(0, entry)
        else:
            self._sections.append(entry)

        def disposer() -> None:
            if entry in self._sections:
                self._sections.remove(entry)

        return disposer

    def section_names(self) -> list[str]:
        return [name for name, _ in self._sections]

    def render(self, tools: list[ToolSchema], cwd: str) -> str:
        parts: list[str] = []
        for _, render in self._sections:
            text = render(tools, cwd)
            if text:
                parts.append(text.strip())
        if tools:
            lines = [f"- {tool.name}: {tool.description}" for tool in tools]
            parts.append("Tools available to you:\n" + "\n".join(lines))
        return "\n\n".join(parts)
