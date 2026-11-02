"""The built-in tools: ``bash``, ``read_file`` and ``write_file``.

Kept free of Cordis imports so they can be unit-tested directly; the
``harness.plugins.tools_builtin`` plugin registers them on ``ctx.tools``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .tools import ToolDefinition, ToolOutcome

DEFAULT_BASH_TIMEOUT_MS = 60_000
DEFAULT_MAX_OUTPUT_CHARS = 20_000
DEFAULT_MAX_READ_BYTES = 200_000


def resolve_path(cwd: str, path: str) -> Path:
    """Resolve ``path`` against the session working directory."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else Path(cwd) / candidate


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… truncated {len(text) - limit} more characters"


async def run_bash(
    args: dict[str, Any],
    cwd: str,
    *,
    timeout_ms: int = DEFAULT_BASH_TIMEOUT_MS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> ToolOutcome:
    """Run one shell command with merged stdout/stderr and a timeout."""
    command = args["command"]
    timeout_s = (args.get("timeout_ms") or timeout_ms) / 1000
    try:
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return ToolOutcome(f"cannot start bash: {error}", is_error=True)

    try:
        raw, _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return ToolOutcome(f"command timed out after {timeout_s:g}s: {command}", is_error=True)

    text = truncate(raw.decode("utf-8", "replace"), max_output_chars).strip("\n")
    code = process.returncode if process.returncode is not None else 0
    body = text if text else "(no output)"
    return ToolOutcome(f"{body}\n[exit code: {code}]", is_error=code != 0)


def run_read_file(args: dict[str, Any], cwd: str, *, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> ToolOutcome:
    """Read a file (optionally a 1-based line window)."""
    path = resolve_path(cwd, args["path"])
    if not path.is_file():
        return ToolOutcome(f"no such file: {path}", is_error=True)
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as error:
        return ToolOutcome(f"cannot read {path}: {error}", is_error=True)

    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", "replace")
    lines = text.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = args.get("limit")
    window = lines[offset - 1 :]
    if limit:
        window = window[: int(limit)]
    body = "\n".join(window)
    if truncated:
        body += f"\n… file truncated at {max_bytes} bytes"
    if not body:
        return ToolOutcome("(empty file or offset past end of file)")
    return ToolOutcome(body)


def run_write_file(args: dict[str, Any], cwd: str) -> ToolOutcome:
    """Write a file, creating parent directories."""
    path = resolve_path(cwd, args["path"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = args["content"].encode("utf-8")
        path.write_bytes(data)
    except OSError as error:
        return ToolOutcome(f"cannot write {path}: {error}", is_error=True)
    return ToolOutcome(f"wrote {len(data)} bytes to {path}")


def bash_definition(**options: Any) -> ToolDefinition:
    return ToolDefinition(
        name="bash",
        description=(
            "Run a shell command in the session working directory and return its combined "
            "stdout/stderr plus the exit code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command line passed to `bash -lc`."},
                "timeout_ms": {"type": "integer", "description": "Optional timeout override in milliseconds."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        execute=lambda args, cwd: run_bash(args, cwd, **options),
        approval=True,
    )


def read_file_definition(**options: Any) -> ToolDefinition:
    return ToolDefinition(
        name="read_file",
        description="Read a text file from disk, optionally a window of lines.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path or a path relative to the working directory."},
                "offset": {"type": "integer", "description": "1-based first line to read."},
                "limit": {"type": "integer", "description": "Maximum number of lines to read."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        execute=lambda args, cwd: run_read_file(args, cwd, **options),
        approval=False,
    )


def write_file_definition(**options: Any) -> ToolDefinition:
    return ToolDefinition(
        name="write_file",
        description="Write (or overwrite) a file with the given content, creating parent directories.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path or a path relative to the working directory."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        execute=run_write_file,
        approval=True,
    )


def definitions(**options: Any) -> list[ToolDefinition]:
    """Build the three built-in tool definitions."""
    bash_options = {
        key: value
        for key, value in options.items()
        if key in ("timeout_ms", "max_output_chars") and value is not None
    }
    read_options = {key: value for key, value in options.items() if key == "max_bytes" and value is not None}
    return [
        bash_definition(**bash_options),
        read_file_definition(**read_options),
        write_file_definition(),
    ]
