"""Typed service contracts for the dsh-style demo runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

LLM_SERVICE = "llm"
TOOLS_SERVICE = "tools"
SESSION_SERVICE = "session"
SYSTEM_PROMPT_SERVICE = "system_prompt"
AGENTS_SERVICE = "agents"


class LLM(ABC):
    """Streaming LLM adapter contract."""

    @abstractmethod
    def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield text, tool-call, and usage chunks."""
        raise NotImplementedError


@dataclass(frozen=True)
class Tool:
    """One tool definition backed by an executable callable."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


class ToolRegistry(ABC):
    """Registry of model-facing tools."""

    @abstractmethod
    def register(self, tool: Tool) -> Callable[[], None]:
        """Register a tool and return its unregister disposer."""
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> list[dict[str, Any]]:
        """Return OpenAI-style tool schemas."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return text suitable for the model."""
        raise NotImplementedError


class SessionStore(ABC):
    """Append-only event-sourced session store."""

    @abstractmethod
    def create(self, session_id: str, *, cwd: str | None = None, title: str = "") -> Any:
        raise NotImplementedError

    @abstractmethod
    def get(self, session_id: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def append(self, session_id: str, event_type: str, data: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    @abstractmethod
    def derive_messages(
        self,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class SystemPromptService(ABC):
    """Ordered system-prompt section registry."""

    @abstractmethod
    def section(self, name: str, order: int, text: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def assemble(self, context: dict[str, Any] | None = None) -> str:
        raise NotImplementedError


class AgentsService(ABC):
    """Agent factory service, shaped after dsh ``ctx.agents``."""

    @abstractmethod
    async def create(self, options: dict[str, Any]) -> Any:
        """Create an agent handle for a session."""
        raise NotImplementedError


__all__ = [
    "AGENTS_SERVICE",
    "LLM",
    "LLM_SERVICE",
    "SESSION_SERVICE",
    "SYSTEM_PROMPT_SERVICE",
    "TOOLS_SERVICE",
    "AgentsService",
    "SessionStore",
    "SystemPromptService",
    "Tool",
    "ToolRegistry",
]
