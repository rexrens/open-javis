"""Provide ``ctx.sessions``: durable JSONL session logs."""

from __future__ import annotations

from pydantic import BaseModel

from harness import options
from harness.session import SessionService

name = "sessions"


class Config(BaseModel):
    """Where session logs live (``--session-dir`` overrides this)."""

    directory: str | None = None


def apply(ctx, config: Config):
    directory = options.LAUNCH.get("sessionDir") or config.directory
    ctx.provide("sessions", SessionService(ctx, directory))
