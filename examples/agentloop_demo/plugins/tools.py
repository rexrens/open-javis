"""Tool registry plugin, built on the real corecoder Tool / ToolRegistry.

The demo tools are plain ``javis.engines.corecoder.tools.Tool`` subclasses
(sync ``execute() -> str``); the plugin registers them into a fresh
``ToolRegistry`` and provides it as the typed ``tools`` service.
``register`` returns a disposer, so unload cleanup is just ``ctx.effect``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from javis.contracts import TOOLS_SERVICE
from javis.engines.corecoder.tools import Tool, ToolRegistry

name = "tools"
inject: list[str] = []
provides = [TOOLS_SERVICE]


class Config(BaseModel):
    workspace_root: str | None = None


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


class DemoReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file's contents with line numbers. "
        "Always read a file before summarizing or editing it."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file"},
            "offset": {"type": "integer", "description": "Start line (1-based)"},
            "limit": {"type": "integer", "description": "Max lines to read"},
        },
        "required": ["file_path"],
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def execute(self, file_path: str, offset: int = 1, limit: int = 2000, **kwargs: Any) -> str:
        path = _resolve(self._root, file_path)
        if not path.exists():
            return f"Error: {file_path} not found"
        if not path.is_file():
            return f"Error: {file_path} is not a file"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        start = max(0, offset - 1)
        chunk = lines[start : start + limit]
        numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(chunk)]
        result = "\n".join(numbered)
        if total > start + limit:
            result += f"\n... ({total} lines total, showing {start + 1}-{start + len(chunk)})"
        return result or "(empty file)"


class DemoListFilesTool(Tool):
    name = "list_files"
    description = "List files in the workspace (skips .git/.venv/__pycache__)."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "max_entries": {
                "type": "integer",
                "description": "Maximum entries to return",
            }
        },
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def execute(self, max_entries: int = 50, **kwargs: Any) -> str:
        skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        entries: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._root):
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            for filename in sorted(filenames):
                rel = Path(dirpath).relative_to(self._root) / filename
                entries.append(str(rel))
                if len(entries) >= max_entries:
                    return "\n".join(entries) + f"\n... ({len(entries)} shown, truncated)"
        return "\n".join(entries) or "(empty directory)"


class DemoBashTool(Tool):
    name = "bash"
    description = (
        "Run a shell command in the demo workspace and return its output. "
        "Demo only: no sandbox."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "timeout": {"type": "number", "description": "Timeout in seconds"},
        },
        "required": ["command"],
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def execute(self, command: str, timeout: float = 30.0, **kwargs: Any) -> str:
        env = dict(os.environ)
        interpreter_dir = str(Path(sys.prefix) / "bin")
        env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.run(
                command,
                shell=True,
                check=False,
                cwd=str(self._root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output[-20000:] if len(output) > 20000 else output or "(no output)"


def apply(ctx: Any, config: Config) -> None:
    root = Path(config.workspace_root or Path(__file__).resolve().parents[1]).resolve()
    registry = ToolRegistry()
    for tool in (
        DemoReadFileTool(root),
        DemoListFilesTool(root),
        DemoBashTool(root),
    ):
        ctx.effect(registry.register(tool))  # disposer → unload removes the tool
    ctx.provide(TOOLS_SERVICE, registry)
