"""``session/*`` endpoints: lifecycle, history, prompt, cancel, and streams."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from javis.app.web.dsh_events import PROVIDER
from javis.app.web.endpoints._util import (
    endpoint_args,
    require_str,
    session_id_of,
    text_of_content,
)
from javis.app.web.protocol import RemoteError
from javis.app.web.registry import EndpointRegistry, StreamSink
from javis.app.web.session_runtime import (
    SessionNotFoundError,
    SessionRuntime,
    replay_should_skip,
)

log = logging.getLogger(__name__)


def _debug_enabled() -> bool:
    """Whether the verbose host diagnostics are requested for this process."""
    return os.environ.get("JAVIS_WEB_ACCESS_LOG") == "1"


async def _resolve(runtime: SessionRuntime, args: dict[str, Any]) -> Any:
    """Resolve the addressed session.

    A blank address (the browser's unselected conversation) resolves to the
    host-owned default session; an unknown explicit id keeps the dsh
    ``session/not-found`` code so the client can recover its own way.
    """
    session_id = session_id_of(args)
    if session_id is None:
        return await runtime.ensure_default_session()
    try:
        return await runtime.ensure_session(session_id)
    except SessionNotFoundError as exc:
        raise RemoteError(
            "session/not-found", f"session {session_id} was not found", {"sessionId": session_id}
        ) from exc


def register(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Wire the ``session`` namespace onto the registry."""

    @registry.unary("session/list")
    async def _list(args: dict[str, Any]) -> dict[str, Any]:
        del args
        await runtime.ensure_default_session()
        return {"items": runtime.list_rows()}

    @registry.unary("session/create")
    async def _create(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        session_id = request.get("sessionId")
        cwd = request.get("cwd")
        if not isinstance(cwd, str) or cwd == "":
            workspace_id = request.get("workspaceId")
            cwd = runtime.workspace_path(workspace_id if isinstance(workspace_id, str) else None)
        state = await runtime.create_session(
            session_id=session_id if isinstance(session_id, str) and session_id else None,
            cwd=cwd if isinstance(cwd, str) and cwd else None,
        )
        return {"sessionId": state.session_id}

    @registry.unary("session/fork")
    async def _fork(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        source = await _resolve(runtime, request)
        at_seq = request.get("atSeq")
        forked = await runtime.fork_session(
            source, at_seq=at_seq if isinstance(at_seq, int) else None
        )
        return {"sessionId": forked.session_id}

    @registry.unary("session/prompt")
    async def _prompt(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        state = await _resolve(runtime, request)
        text = text_of_content(request.get("content"))
        request_id = request.get("requestId")
        try:
            await runtime.prompt(
                state, text, request_id=request_id if isinstance(request_id, str) else None
            )
        except RuntimeError as exc:
            raise RemoteError("session/busy", str(exc)) from exc
        return {"accepted": True}

    @registry.unary("session/cancel")
    async def _cancel(args: dict[str, Any]) -> dict[str, Any]:
        state = await _resolve(runtime, endpoint_args(args))
        return {"accepted": await runtime.cancel(state)}

    @registry.unary("session/page")
    async def _page(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        state = await _resolve(runtime, request)
        before = request.get("beforeSeq")
        through = request.get("throughSeq")
        limit = request.get("maxMessages")
        limit = limit if isinstance(limit, int) and limit > 0 else 50
        # A page must END exactly at the cursor the caller asked through:
        # `throughSeq` is that inclusive cut, and `beforeSeq` (when a caller
        # selects strictly older entries) is only a fallback. Returning the live
        # tail instead makes the client journal reject the page as a gap.
        upper = state.seq
        if isinstance(through, int):
            upper = min(upper, through)
        elif isinstance(before, int):
            upper = min(upper, before - 1)
        window = [record for record in state.records if int(record["event"]["seq"]) <= upper]
        if upper < 0:
            window = []
        page = window[-limit:]
        return {"records": page, "hasMore": len(window) > len(page)}

    @registry.unary("session/modelCatalog")
    async def _model_catalog(args: dict[str, Any]) -> dict[str, Any]:
        del args
        model = runtime.current_model()
        return {
            "default": {"provider": PROVIDER, "model": model},
            "routableProviders": [PROVIDER],
            "groups": [
                {
                    "id": PROVIDER,
                    "name": "javis",
                    "models": [{"id": model, "name": model}],
                }
            ],
            "failures": [],
        }

    @registry.unary("session/canOpenWorkspacePath")
    async def _can_open(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"available": False}

    @registry.unary("session/selectModel")
    async def _select_model(args: dict[str, Any]) -> dict[str, Any]:
        request = endpoint_args(args)
        state = await _resolve(runtime, request)
        model = require_str(request, "model")
        provider = request.get("provider")
        if state.bundle is not None:
            state.bundle.engine.set_model(model)
        state.model = model
        result = {
            "selected": {
                "provider": provider if isinstance(provider, str) and provider else PROVIDER,
                "model": model,
            }
        }
        runtime.emit_model_selection(state)
        return result

    @registry.stream("session/follow")
    async def _follow(args: dict[str, Any], sink: StreamSink) -> None:
        request = endpoint_args(args)
        state = await _resolve(runtime, request)
        # Subscribe first, then snapshot: anything appended during the handoff
        # stays in the queue and is filtered below, instead of widening the gap
        # that would make the client journal reopen the stream.
        queue = runtime.subscribe(state)
        sent: dict[str, int] = {}

        def note(frame: dict[str, Any]) -> None:
            key = str(frame.get("type"))
            sent[key] = sent.get(key, 0) + 1

        try:
            snapshot = runtime.snapshot_frame(
                state, assistant_stream=request.get("assistantStream") is True
            )
            if _debug_enabled():
                baseline = snapshot.get("assistantStream")
                log.info(
                    "web follow open %s cursor=%s assistantStream=%s",
                    state.session_id,
                    snapshot["cursor"],
                    json.dumps(baseline, ensure_ascii=False)[:120] if baseline else None,
                )
            await sink.send(snapshot)
            note(snapshot)
            cursor = int(snapshot["cursor"])
            baseline = snapshot.get("assistantStream")
            revision = baseline.get("revision") if isinstance(baseline, dict) else None
            attempt = baseline.get("activeAttempt") if isinstance(baseline, dict) else None
            next_index = int(attempt["nextIndex"]) if isinstance(attempt, dict) else 0
            while True:
                frame = await queue.get()
                if frame is None:
                    return
                if replay_should_skip(
                    frame, cursor=cursor, revision=revision, next_index=next_index
                ):
                    continue
                await sink.send(frame)
                note(frame)
        finally:
            runtime.unsubscribe(state, queue)
            if _debug_enabled():
                log.info("web follow closed %s sent=%s", state.session_id, sent)

    @registry.stream("session/control")
    async def _control(args: dict[str, Any], sink: StreamSink) -> None:
        del args
        await sink.send({"type": "baseline", "value": runtime.control_baseline()})
        queue = runtime.subscribe_control()
        try:
            while True:
                await sink.send(await queue.get())
        finally:
            runtime.unsubscribe_control(queue)
