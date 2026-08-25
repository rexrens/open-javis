"""Tests for the tool registry."""

from __future__ import annotations

from javis.engines.corecoder.tools import ToolRegistry, all_tools, get_tool, register_tool, unregister_tool
from javis.engines.corecoder.tools.base import Tool


class TestEchoTool(Tool):
    name = "test_echo"
    description = "Echo text back"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, **kwargs) -> str:
        return kwargs.get("text", "")


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


def test_tool_registry_register_returns_disposer():
    reg = ToolRegistry()
    tool = TestEchoTool()
    cancel = reg.register(tool)
    assert reg.get("test_echo") is tool
    cancel()
    assert reg.get("test_echo") is None
    cancel()  # idempotent — second dispose is a no-op


def test_tool_registry_seed_and_snapshot():
    reg = ToolRegistry(seed=(TestEchoTool(),))
    assert reg.get("test_echo") is not None
    names = {t.name for t in reg.all()}
    assert "test_echo" in names
    # snapshot is a fresh list
    assert reg.all() is not reg.all()
