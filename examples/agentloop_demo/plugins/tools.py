"""Tool registry plugin for the dsh-style demo."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from examples.agentloop_demo.contracts import TOOLS_SERVICE, Tool, ToolRegistry

name = "tools"
inject: list[str] = []
provides = [TOOLS_SERVICE]


class Config(BaseModel):
    workspace_root: str | None = None


class DemoToolsService(ToolRegistry):
    def __init__(self, workspace_root: Path) -> None:
        self._tools: dict[str, Tool] = {}
        self._workspace_root = workspace_root

    def register(self, tool: Tool) -> Callable[[], None]:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

        def unregister() -> None:
            self._tools.pop(tool.name, None)

        return unregister

    def snapshot(self) -> list[dict[str, Any]]:
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
        return sorted(schemas, key=lambda schema: schema["function"]["name"])

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}"
        try:
            result = tool.fn(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def read_file(root: Path, file_path: str, offset: int = 1, limit: int = 2000) -> str:
    path = _resolve(root, file_path)
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


def list_files(root: Path, max_entries: int = 50) -> str:
    skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for filename in sorted(filenames):
            rel = Path(dirpath).relative_to(root) / filename
            entries.append(str(rel))
            if len(entries) >= max_entries:
                return "\n".join(entries) + f"\n... ({len(entries)} shown, truncated)"
    return "\n".join(entries) or "(empty directory)"


async def bash(root: Path, command: str, timeout: float = 30.0) -> str:
    env = dict(os.environ)
    interpreter_dir = str(Path(sys.prefix) / "bin")
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"Error: command timed out after {timeout}s"
    output = (stdout + stderr).decode(errors="replace").strip()
    if proc.returncode != 0:
        output += f"\n[exit code: {proc.returncode}]"
    return output[-20000:] if len(output) > 20000 else output or "(no output)"


def apply(ctx: Any, config: Config) -> Any:
    root = Path(config.workspace_root or Path(__file__).resolve().parents[1]).resolve()
    service = DemoToolsService(root)
    service.register(
        Tool(
            name="read_file",
            description=(
                "Read a file's contents with line numbers. "
                "Always read a file before summarizing or editing it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "offset": {"type": "integer", "description": "Start line (1-based)"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
                },
                "required": ["file_path"],
            },
            fn=lambda **kwargs: read_file(root, **kwargs),
        )
    )
    service.register(
        Tool(
            name="list_files",
            description="List files in the workspace (skips .git/.venv/__pycache__).",
            parameters={
                "type": "object",
                "properties": {
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return",
                    }
                },
            },
            fn=lambda **kwargs: list_files(root, **kwargs),
        )
    )
    service.register(
        Tool(
            name="bash",
            description=(
                "Run a shell command in the demo workspace and return its output. "
                "Demo only: no sandbox."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "number", "description": "Timeout in seconds"},
                },
                "required": ["command"],
            },
            fn=lambda **kwargs: bash(root, **kwargs),
        )
    )
    ctx.provide(TOOLS_SERVICE, service)
