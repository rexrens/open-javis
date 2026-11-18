from __future__ import annotations

import sys
from pathlib import Path

import pytest

from javis.agent.tools.base import BaseTool, ToolCallResult, ToolExecutionContext, ToolRegister
from javis.agent.tools.buildin.workspace import WorkspaceToolSet
from javis.agent.tools.function import tool
from javis.agent.tools.toolset import ToolSet


class DemoToolSet(ToolSet):
    name = "demo"

    @tool
    def echo(self, text: str) -> str:
        """Echo text."""
        return text

    @tool
    def upper(self, text: str) -> str:
        """Uppercase text."""
        return text.upper()

    @tool
    def reverse(self, text: str) -> str:
        """Reverse text."""
        return text[::-1]


class PingTool(BaseTool):
    name = "ping"
    description = "Return pong."

    async def _run(
        self,
        context: ToolExecutionContext,
        args: None,
    ) -> ToolCallResult:
        return ToolCallResult(output="pong")


def test_toolset_discovers_decorated_methods() -> None:
    toolset = DemoToolSet()

    assert [item.name for item in toolset] == ["echo", "upper", "reverse"]


def test_toolset_include_only_selected_tools() -> None:
    toolset = DemoToolSet(include={"echo", "reverse"})

    assert [item.name for item in toolset] == ["echo", "reverse"]


def test_toolset_exclude_removes_tools() -> None:
    toolset = DemoToolSet(exclude={"upper"})

    assert [item.name for item in toolset] == ["echo", "reverse"]


def test_toolset_exclude_wins_over_include() -> None:
    toolset = DemoToolSet(include={"echo", "upper"}, exclude={"upper"})

    assert [item.name for item in toolset] == ["echo"]


@pytest.mark.parametrize("option", ["include", "exclude"])
def test_toolset_rejects_unknown_selection_names(option: str) -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        DemoToolSet(**{option: {"missing"}})


def test_toolset_accepts_explicit_tools() -> None:
    toolset = DemoToolSet(tools=PingTool())

    assert [item.name for item in toolset] == ["echo", "upper", "reverse", "ping"]


def test_toolset_can_be_registered_as_a_group() -> None:
    registry = ToolRegister()
    registry.register(DemoToolSet(include={"echo", "upper"}))

    assert [item.name for item in registry] == ["echo", "upper"]
    assert [item["function"]["name"] for item in registry.schemas()] == ["echo", "upper"]


@pytest.mark.asyncio
async def test_workspace_toolset_reads_and_writes_files(tmp_path: Path) -> None:
    toolset = WorkspaceToolSet(root=tmp_path)
    context = ToolExecutionContext(cwd=tmp_path)

    write_tool = toolset.get("write_file")
    read_tool = toolset.get("read_file")
    assert write_tool is not None
    assert read_tool is not None

    write_result = await write_tool(
        context,
        {"path": "notes/hello.txt", "content": "hello workspace"},
    )
    read_result = await read_tool(context, {"path": "notes/hello.txt"})

    assert write_result.is_error is False
    assert read_result == ToolCallResult(output="hello workspace")
    assert (tmp_path / "notes" / "hello.txt").read_text(encoding="utf-8") == "hello workspace"


@pytest.mark.asyncio
async def test_workspace_toolset_rejects_paths_outside_root(tmp_path: Path) -> None:
    toolset = WorkspaceToolSet(root=tmp_path)
    read_tool = toolset.get("read_file")
    assert read_tool is not None

    result = await read_tool(
        ToolExecutionContext(cwd=tmp_path),
        {"path": "../outside.txt"},
    )

    assert result.is_error is True
    assert "outside workspace" in result.output


@pytest.mark.asyncio
async def test_workspace_toolset_runs_commands_in_root(tmp_path: Path) -> None:
    toolset = WorkspaceToolSet(root=tmp_path, include={"run_command"})
    run_tool = toolset.get("run_command")
    assert run_tool is not None

    result = await run_tool(
        ToolExecutionContext(cwd=tmp_path),
        {
            "args": [sys.executable, "-c", "import os; print(os.getcwd()); print('ok')"],
            "tail": 10,
            "timeout": 10,
        },
    )

    assert result.is_error is False
    assert result.output.splitlines() == [str(tmp_path), "ok"]
