from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolExecutionContext:
    cwd: Path = field(default_factory=Path.cwd)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallResult:
    output: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def run(self, args: dict[str, Any], context: ToolExecutionContext) -> ToolCallResult:
        """Run the tool.

        :param args: The tool arguments.
        :return: The tool output.
        """

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    def schma(self) -> dict[str, Any]:
        return self.schema()


class ToolRegister:
    def __init__(self) -> None:
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def list(self) -> builtins.list[BaseTool]:
        return builtins.list(self.tools.values())

    def schemas(self) -> builtins.list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_model.model_json_schema(),
                },
            }
            for tool in self.tools.values()
        ]

    def schma(self) -> builtins.list[dict[str, Any]]:
        return self.schemas()
