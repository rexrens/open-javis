"""``AgentToolView``: the loop-facing live view over the host tool registry."""

from __future__ import annotations

from typing import Any, ClassVar

from javis.contracts.tools import Tool as JavisTool
from javis.contracts.tools import ToolRegistry as JavisToolRegistry
from javis.cordis import Context
from javis.harness.tool_adapter import AgentToolView


class LateTool(JavisTool):
    name = "late_tool"
    description = "registered after the view exists"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return "late"


def test_view_sees_tools_registered_after_it_is_built():
    host = JavisToolRegistry()
    ctx = Context()
    view = AgentToolView(host, ctx)

    assert view.get("late_tool") is None
    assert view.schemas() == []

    host.register(LateTool())

    assert view.get("late_tool") is not None
    assert [schema.name for schema in view.schemas()] == ["late_tool"]
    assert [tool.name for tool in view.all()] == ["late_tool"]
    assert view.execution_mode("late_tool").kind == "parallel"


def test_view_register_forwards_to_the_host_registry():
    host = JavisToolRegistry()
    ctx = Context()
    view = AgentToolView(host, ctx)

    view.register(LateTool())

    assert host.get("late_tool") is not None
    assert [schema.name for schema in view.schemas()] == ["late_tool"]
