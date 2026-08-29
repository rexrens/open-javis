"""Ordered system-prompt plugin — owns its demo-local contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

SectionText = str | Callable[[dict[str, Any]], str]


class SystemPromptService(ABC):
    """Ordered system-prompt section registry (demo-local contract)."""

    @abstractmethod
    def section(self, name: str, order: int, text: SectionText) -> None:
        raise NotImplementedError

    @abstractmethod
    def assemble(self, context: dict[str, Any] | None = None) -> str:
        raise NotImplementedError


name = "system_prompt"
inject: list[str] = []
provides = [SystemPromptService]


class Config(BaseModel):
    persona: str = Field(
        default=(
            "You are javis, a CLI coding agent. "
            "Answer concisely and use tools when they help."
        )
    )


@dataclass(frozen=True)
class PromptSection:
    name: str
    order: int
    text: SectionText


class DemoSystemPromptService(SystemPromptService):
    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def section(self, name: str, order: int, text: SectionText) -> None:
        if name in self._sections:
            raise ValueError(f"duplicate system-prompt section {name!r}")
        self._sections[name] = PromptSection(name=name, order=order, text=text)

    def assemble(self, context: dict[str, Any] | None = None) -> str:
        rendered: list[str] = []
        for section in sorted(self._sections.values(), key=lambda item: (item.order, item.name)):
            text = section.text(context or {}) if callable(section.text) else section.text
            if text:
                rendered.append(text)
        return "\n\n".join(rendered)

    def render(self, template: str, variables: dict[str, Any]) -> str:
        result = template
        for key, value in variables.items():
            result = result.replace("{{" + key + "}}", str(value))
        return result


def apply(ctx: Any, config: Config) -> None:
    service = DemoSystemPromptService()
    service.section("identity", -100, config.persona)
    service.section(
        "environment",
        0,
        lambda context: (
            "Environment:\n"
            f"- cwd: {context.get('cwd', '<unknown>')}\n"
            f"- date: {context.get('date', '<unknown>')}"
        ),
    )
    service.section(
        "tools-guide",
        100,
        (
            "Use the available tools when they help. "
            "Read files before summarizing them; run tests before reporting results."
        ),
    )
    ctx.provide(SystemPromptService, service)
