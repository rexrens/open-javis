from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class ToolExecutionContext:
    cwd: Path = field(default_factory=Path.cwd)


@dataclass(frozen=True)
class ToolCallResult:
    output: str = ""
    is_error: bool = False


class BaseTool(ABC):
    """Base class for all tools."""


    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] | None = None

    def to_openai_tool(self, strict: bool = False) -> dict[str, Any]:
        """生成 OpenAI Chat Completions tools 数组中的单个工具定义。"""

        def _clean_schema(node: Any) -> Any:
            # 移除 title 属性, 避免openai sechma 验证失败
            if isinstance(node, dict):
                node.pop("title", None)
                for v in node.values():
                    _clean_schema(v)
            elif isinstance(node, list):
                for item in node:
                    _clean_schema(item)
            return node

        if self.args_schema is not None:
            params = self.args_schema.model_json_schema()
        else:
            params = {"type": "object", "properties": {}, "required": []}

        if strict:
            params = _clean_schema(params)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    @abstractmethod
    async def _run(
        self,
        context: ToolExecutionContext,
        args: BaseModel | None,
    ) -> ToolCallResult:
        """Run the tool.

        :param context: 运行时上下文（cwd 等）
        :param args: 校验后的参数模型；未定义 args_schema 时为 None
        :return: ToolCallResult
        """
        ...

    # ---- 统一入口 ----
    async def __call__(
        self,
        context: ToolExecutionContext,
        args: BaseModel | dict[str, Any] | None,
    ) -> ToolCallResult:
        try:
            if self.args_schema is not None:
                validated = self.args_schema.model_validate(args)
            else:
                validated = None
            return await self._run(context, validated)
        except ValidationError as e:
            return ToolCallResult(output=f"参数校验失败: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001 — 工具不能把 agent 循环搞崩
            return ToolCallResult(output=f"{type(e).__name__}: {e}", is_error=True)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


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
        return [tool.to_openai_tool() for tool in self.tools.values()]
