"""Tool plugin for the plugin-harness example.

Shows the standard tool-plugin shape: build a ``Tool``, register it on the
``tools`` service, hand the disposer to ``ctx.effect`` so unloading restores
the previous registry entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from javis.contracts.services import HOST_SERVICE, TOOLS_SERVICE
from javis.contracts.tools import Tool


class WorkspaceNoteTool(Tool):
    """Append a line to ``notes.txt`` in the javis workspace."""

    name = "workspace_note"
    description = "Append a line to notes.txt in the javis workspace."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The text to append",
            }
        },
        "required": ["content"],
    }

    def __init__(self, notes_path: str) -> None:
        self._notes_path = Path(notes_path)

    def execute(self, content: str, **kwargs: Any) -> str:
        del kwargs
        self._notes_path.parent.mkdir(parents=True, exist_ok=True)
        with self._notes_path.open("a", encoding="utf-8") as fh:
            fh.write(content.rstrip() + "\n")
        return f"appended to {self._notes_path}"


def apply(ctx: Any) -> None:
    host = ctx.get(HOST_SERVICE)
    tools = ctx.get(TOOLS_SERVICE)
    tool = WorkspaceNoteTool(str(Path(host.workspace) / "notes.txt"))
    # register() returns a disposer; effect wires it to plugin unload
    ctx.effect(lambda: tools.register(tool))
