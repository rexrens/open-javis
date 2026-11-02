"""Remote endpoint implementations for the javis web host."""

from __future__ import annotations

from javis.app.web.endpoints import events, permission, session, settings, workspace
from javis.app.web.registry import EndpointRegistry
from javis.app.web.session_runtime import SessionRuntime


def register_all(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Register every v1 endpoint against one session runtime."""
    session.register(registry, runtime)
    workspace.register(registry, runtime)
    settings.register(registry, runtime)
    permission.register(registry, runtime)
    events.register(registry, runtime)


__all__ = ["register_all"]
