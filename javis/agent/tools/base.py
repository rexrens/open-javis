from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Iterable
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
    def __init__(self, *tools: BaseTool | Iterable[BaseTool] ) -> None:
        self.tools: dict[str, BaseTool] = {}
        self._schemas_cache: list[dict] | None = None

        if tools:
            self.register(*tools)
    # ---------- 注册 ----------
    def register(self, *tools: BaseTool | Iterable[BaseTool]) -> ToolRegister:
        """支持 reg.register(a, b, c) 和 reg.register([a, b], c) 两种写法。"""

        self._schemas_cache = None

        for t in tools:
            # 允许传 list/tuple/set 等容器
            batch = t if isinstance(t, Iterable) and not isinstance(t, BaseTool) else (t,)
            for t in batch:
                if not isinstance(t, BaseTool):
                    raise TypeError(f"Tool Type Error, except BaseTool, actually {type(t).__name__}")
                if t.name in self.tools and self.tools[t.name] is not t:
                    raise ValueError(f"tool name conflict: {t.name!r}")
                self.tools[t.name] = t
        return self

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def all(self) -> list[BaseTool]:
        return builtins.list(self.tools.values())

    def schemas(self) -> list[dict[str, Any]]:

        # cache the schemas from high frequently used tools
        if self._schemas_cache is None:
            self._schemas_cache = [tool.to_openai_tool() for tool in self.tools.values()]

        return self._schemas_cache
    def __contains__(self, name: str) -> bool:
        return name in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    def __iter__(self):
        return iter(self.tools.values())
