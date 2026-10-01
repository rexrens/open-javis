"""``settings/*`` and ``credentials/*`` endpoints.

v1 reports a read-only settings surface: the deployment facts the settings page
needs to render, with no writable namespaces. Writes stay unimplemented so the
page reports them instead of silently discarding a change.
"""

from __future__ import annotations

from typing import Any

from javis.app.web.protocol import RemoteError
from javis.app.web.registry import EndpointRegistry
from javis.app.web.session_runtime import SessionRuntime


def register(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Wire the configuration namespaces onto the registry."""
    del runtime

    @registry.unary("settings/describe")
    async def _describe(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"writable": False, "hasDocument": False, "namespaces": []}

    @registry.unary("settings/mutate")
    async def _mutate(args: dict[str, Any]) -> dict[str, Any]:
        namespace = args.get("ns")
        raise RemoteError(
            "gateway/unknown-endpoint",
            "settings writes are not implemented by this javis build yet",
            {"namespace": namespace},
        )

    @registry.unary("credentials/describe")
    async def _credentials(args: dict[str, Any]) -> dict[str, Any]:
        refs = args.get("refs")
        names = [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []
        return {name: {"present": False} for name in names}
