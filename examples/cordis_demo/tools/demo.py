"""Demo driver for the tools chapter: three scenarios through the pipeline.

Mounted last (its ``inject`` waits for the ``tools`` service), so both the
core and the policy listeners are already registered when the demos run —
the same consumer pattern as chapter 3.
"""

from __future__ import annotations

name = "tools-demo"
inject = ["tools"]


def apply(ctx):
    call_tool = ctx.get("tools")
    call_tool("read", {"path": "notes.txt"})
    call_tool("shell", {"cmd": "ls"})
    call_tool("weather", {"city": "shanghai"})
