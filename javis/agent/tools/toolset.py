from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from javis.agent.tools.base import BaseTool
from javis.agent.tools.function import Function


class ToolSet:
    """A selectable group of tools defined on a subclass."""

    name: str = "toolset"

    def __init__(
        self,
        tools: BaseTool | Iterable[BaseTool] | Mapping[str, BaseTool] | None = None,
        *,
        name: str | None = None,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
    ) -> None:
        if name is not None:
            self.name = name

        self.tools: dict[str, BaseTool] = {}
        for item in self._discover_tool_methods():
            self._add(item)
        for item in self._iter_tools(tools):
            self._add(item)

        include_names = self._normalize_names(include)
        exclude_names = self._normalize_names(exclude)
        requested = (include_names or set()) | (exclude_names or set())
        unknown = sorted(requested - self.tools.keys())
        if unknown:
            available = ", ".join(self.tools) or "<none>"
            raise ValueError(
                f"unknown tool(s) in toolset {self.name!r}: "
                f"{', '.join(unknown)}; available: {available}"
            )

        selected_names = set(self.tools) if include_names is None else include_names
        if exclude_names is not None:
            selected_names -= exclude_names
        self.selected: dict[str, BaseTool] = {
            tool_name: tool
            for tool_name, tool in self.tools.items()
            if tool_name in selected_names
        }

    def _discover_tool_methods(self) -> Iterator[BaseTool]:
        """Resolve the most-derived @tool attributes once per method name."""
        seen: set[str] = set()
        for cls in type(self).__mro__:
            for attr_name, value in vars(cls).items():
                if attr_name in seen:
                    continue
                seen.add(attr_name)
                if not isinstance(value, Function):
                    continue
                bound = getattr(self, attr_name)
                if isinstance(bound, BaseTool):
                    yield bound

    @staticmethod
    def _iter_tools(
        tools: BaseTool | Iterable[BaseTool] | Mapping[str, BaseTool] | None,
    ) -> Iterator[BaseTool]:
        if tools is None:
            return
        if isinstance(tools, BaseTool):
            yield tools
            return
        if isinstance(tools, Mapping):
            values: Iterable[Any] = tools.values()
        elif isinstance(tools, Iterable):
            values = tools
        else:
            raise TypeError(f"tools must be BaseTool or iterable, got {type(tools).__name__}")
        for item in values:
            if not isinstance(item, BaseTool):
                raise TypeError(f"tool must be BaseTool, got {type(item).__name__}")
            yield item

    def _add(self, item: BaseTool) -> None:
        existing = self.tools.get(item.name)
        if existing is not None and existing is not item:
            raise ValueError(f"tool name conflict in toolset {self.name!r}: {item.name!r}")
        self.tools[item.name] = item

    @staticmethod
    def _normalize_names(names: Iterable[str] | str | None) -> set[str] | None:
        if names is None:
            return None
        if isinstance(names, str):
            return {names}
        return set(names)

    def all(self) -> list[BaseTool]:
        """Return the tools selected by include/exclude."""
        return list(self.selected.values())

    def available(self) -> list[BaseTool]:
        """Return every tool defined by the ToolSet, before selection."""
        return list(self.tools.values())

    def get(self, name: str) -> BaseTool | None:
        return self.selected.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self]

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self.selected.values())

    def __len__(self) -> int:
        return len(self.selected)

    def __contains__(self, name: str) -> bool:
        return name in self.selected

    def __repr__(self) -> str:
        names = ", ".join(self.selected)
        return f"<{type(self).__name__} name={self.name!r} tools=[{names}]>"
