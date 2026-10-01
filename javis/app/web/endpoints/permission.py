"""``permissionPresets/*`` endpoints.

v1 runs every tool call without asking, so the catalog publishes exactly one
preset. Publishing a single option keeps the composer's selector honest instead
of offering presets whose approval card does not exist yet.
"""

from __future__ import annotations

from typing import Any

from javis.app.web.registry import EndpointRegistry
from javis.app.web.session_runtime import SessionRuntime

AUTO_PRESET = {
    "value": "auto",
    "name": "Auto",
    "description": "Run every tool call without asking (frozen for this javis build).",
}


def register(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Wire the permission catalog onto the registry."""
    del runtime

    @registry.unary("permissionPresets/catalog")
    async def _catalog(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"options": [dict(AUTO_PRESET)]}
