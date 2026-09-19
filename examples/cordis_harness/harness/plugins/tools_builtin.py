"""Register the built-in ``bash`` / ``read_file`` / ``write_file`` tools."""

from __future__ import annotations

from pydantic import BaseModel

from harness import builtins

name = "tools-builtin"
inject = ["tools"]


class Config(BaseModel):
    """Limits applied to every built-in tool call."""

    bashTimeoutMs: int = builtins.DEFAULT_BASH_TIMEOUT_MS
    maxOutputChars: int = builtins.DEFAULT_MAX_OUTPUT_CHARS
    maxReadBytes: int = builtins.DEFAULT_MAX_READ_BYTES


def apply(ctx, config: Config):
    tools = ctx.get("tools")
    return [
        tools.register(definition)
        for definition in builtins.definitions(
            timeout_ms=config.bashTimeoutMs,
            max_output_chars=config.maxOutputChars,
            max_bytes=config.maxReadBytes,
        )
    ]
