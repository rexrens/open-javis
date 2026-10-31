"""``workspace/*`` and ``directoryPicker/*`` endpoints.

The host owns the workspace registry: the javis working directory plus any
directory the user registers through the browser's directory picker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from javis.app.web.endpoints._util import endpoint_args
from javis.app.web.protocol import RemoteError
from javis.app.web.registry import EndpointRegistry, StreamSink
from javis.app.web.session_runtime import SessionRuntime

MAX_DIRECTORY_ENTRIES = 200


def _crumb(name: str, path: str) -> dict[str, Any]:
    return {"name": name, "path": path, "hidden": False}


def _listing_for(runtime: SessionRuntime, requested: str | None) -> dict[str, Any]:
    """One directory level plus its ancestry, as the browse picker reads it."""
    target = Path(requested).expanduser() if requested else Path(runtime.cwd)
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise RemoteError("workspace/invalid-path", str(exc), {"path": str(target)}) from exc
    if not resolved.is_dir():
        raise RemoteError(
            "workspace/invalid-path",
            f"{resolved} is not a directory",
            {"path": str(resolved)},
        )
    crumbs = [_crumb(part.name or part.anchor, str(part)) for part in [*resolved.parents][::-1]]
    crumbs.append(_crumb(resolved.name or str(resolved), str(resolved)))
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        children = sorted(
            (child for child in resolved.iterdir() if child.is_dir()),
            key=lambda child: child.name.lower(),
        )
    except OSError as exc:
        raise RemoteError("workspace/invalid-path", str(exc), {"path": str(resolved)}) from exc
    if len(children) > MAX_DIRECTORY_ENTRIES:
        truncated = True
        children = children[:MAX_DIRECTORY_ENTRIES]
    for child in children:
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "hidden": child.name.startswith("."),
            }
        )
    return {
        "path": str(resolved),
        "home": str(Path.home()),
        "crumbs": crumbs,
        "entries": entries,
        "truncated": truncated,
    }


def register(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Wire the ``workspace`` namespace onto the registry."""

    @registry.stream("workspace/follow")
    async def _follow(args: dict[str, Any], sink: StreamSink) -> None:
        del args
        await runtime.ensure_default_session()
        await sink.send(
            {
                "type": "baseline",
                "value": {
                    "items": runtime.workspace_rows(),
                    "archivedSessionIds": [],
                },
            }
        )
        await asyncio.Event().wait()

    @registry.unary("workspace/create")
    async def _create(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        path = request.get("path")
        if not isinstance(path, str) or path == "":
            raise RemoteError("gateway/bad-request", "workspace path must be a non-empty string")
        try:
            row, created = runtime.create_workspace(path)
        except NotADirectoryError as exc:
            raise RemoteError(
                "workspace/invalid-path",
                f"{path} is not a directory",
                {"path": path},
            ) from exc
        return {"workspace": row, "created": created}

    @registry.unary("directoryPicker/list")
    async def _list(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        return _listing_for(runtime, path if isinstance(path, str) else None)

    @registry.unary("directoryPicker/createDirectory")
    async def _create_directory(args: dict[str, Any]) -> str:
        path = args.get("path")
        name = args.get("name")
        if not isinstance(path, str) or not isinstance(name, str) or "/" in name:
            raise RemoteError(
                "gateway/bad-request", "createDirectory needs a path and a plain name"
            )
        target = Path(path).expanduser() / name
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise RemoteError(
                "workspace/invalid-path", str(exc), {"path": str(target)}
            ) from exc
        return str(target)
