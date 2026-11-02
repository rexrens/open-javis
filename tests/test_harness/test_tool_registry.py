"""Tests for the tool registry."""

from __future__ import annotations

from typing import ClassVar

import pytest

from javis.tools import (
    TOOL_REGISTRY,
    ToolRegistry,
    all_tools,
    get_tool,
    register_tool,
    unregister_tool,
)
from javis.tools.base import Tool


class TestEchoTool(Tool):
    name = "test_echo"
    description = "Echo text back"
    parameters: ClassVar[dict[str, str]] = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return kwargs.get("text", "")


class BashOverrideTool(Tool):
    name = "bash"
    description = "override the built-in bash tool"
    parameters: ClassVar[dict[str, str]] = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "override"


@pytest.fixture(autouse=True)
def restore_registry():
    """Restore the default registry after each test (P0-D isolation)."""
    before = {tool.name: tool for tool in TOOL_REGISTRY.all()}
    yield
    after = {tool.name: tool for tool in TOOL_REGISTRY.all()}
    for name, tool in before.items():
        if after.get(name) is not tool:
            TOOL_REGISTRY.unregister(name)
            TOOL_REGISTRY.register(tool)
    for name in after:
        if name not in before:
            TOOL_REGISTRY.unregister(name)


def test_builtin_tools_registered():
    names = {t.name for t in all_tools()}
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "agent"} <= names


def test_register_and_get():
    register_tool(TestEchoTool())
    assert get_tool("test_echo") is not None
    assert get_tool("test_echo").execute(text="hi") == "hi"


def test_get_unknown_returns_none():
    assert get_tool("definitely-not-a-tool") is None


def test_register_idempotent():
    register_tool(TestEchoTool())
    before = len(all_tools())
    register_tool(TestEchoTool())
    assert len(all_tools()) == before
    assert sum(1 for t in all_tools() if t.name == "test_echo") == 1


def test_unregister_tool():
    register_tool(TestEchoTool())
    assert get_tool("test_echo") is not None
    unregister_tool("test_echo")
    assert get_tool("test_echo") is None
    unregister_tool("test_echo")  # idempotent — missing name is silently ignored


def test_tool_registry_class_register_returns_disposer():
    registry = ToolRegistry()
    tool = TestEchoTool()
    cancel = registry.register(tool)
    assert registry.get("test_echo") is tool
    assert registry.all() == [tool]
    cancel()
    assert registry.get("test_echo") is None
    cancel()  # idempotent


def test_disposer_restores_previous_entry():
    first = TestEchoTool()
    cancel_first = register_tool(first)
    second = TestEchoTool()
    cancel_second = register_tool(second)
    assert get_tool("test_echo") is second
    cancel_second()
    assert get_tool("test_echo") is first
    cancel_first()
    assert get_tool("test_echo") is None


def test_disposer_restores_overwritten_builtin():
    original = get_tool("bash")
    assert original is not None
    cancel = register_tool(BashOverrideTool())
    assert get_tool("bash") is not original
    cancel()
    assert get_tool("bash") is original
