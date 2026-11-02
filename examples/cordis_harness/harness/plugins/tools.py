"""Provide ``ctx.tools``: the tool registry and execution pipeline."""

from __future__ import annotations

from harness.tools import ToolsService

name = "tools"


def apply(ctx):
    ctx.provide("tools", ToolsService(ctx))
