"""FastAPI host for the dsh web client: static shell, Remote RPC, and mux streams."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.responses import Response as PlainResponse

from javis.app.web.assets import WebAssets
from javis.app.web.auth import BrowserAuth
from javis.app.web.index import render_index
from javis.app.web.protocol import (
    ProtocolError,
    RemoteError,
    end_frame,
    error_frame,
    error_response,
    item_frame,
    ok_response,
    parse_client_request,
    parse_stream_message,
    request_args,
)
from javis.app.web.registry import EndpointRegistry
from javis.app.web.session_runtime import SessionRuntime

log = logging.getLogger(__name__)

#: Body cap for buffered RPC requests.
MAX_REQUEST_BODY_BYTES = 32 * 1024 * 1024

#: Set to ``1`` to log every served request (diagnosing a browser session).
ACCESS_LOG_ENV = "JAVIS_WEB_ACCESS_LOG"


def _headers_of(request: Request | WebSocket) -> dict[str, str]:
    """Lower-cased header mapping for the trust fence."""
    return {key.lower(): value for key, value in request.headers.items()}


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class _StreamHandle:
    """One open logical stream: its task and cancellation hook."""

    def __init__(self, task: asyncio.Task[None]) -> None:
        self.task = task


def create_app(
    *,
    assets: WebAssets,
    runtime: SessionRuntime,
    auth: BrowserAuth,
    registry: EndpointRegistry,
) -> FastAPI:
    """Build the host application serving one asset directory and runtime."""
    app = FastAPI(title="javis web", docs_url=None, redoc_url=None, openapi_url=None)
    index_html = assets.dist_index.read_text(encoding="utf-8")
    rendered_index = render_index(index_html, assets.injections)
    access_log = os.environ.get(ACCESS_LOG_ENV) == "1"

    if access_log:

        @app.middleware("http")
        async def log_request(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            started = time.perf_counter()
            response: Response = await call_next(request)
            target = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
            log.info(
                "web %s %s -> %s (%.0fms)",
                request.method,
                target[:160],
                response.status_code,
                (time.perf_counter() - started) * 1000,
            )
            return response

    @app.post("/api/{endpoint:path}")
    async def unary(endpoint: str, request: Request) -> Response:
        headers = _headers_of(request)
        rejection = auth.request_rejection(headers)
        if rejection is not None:
            return PlainResponse(
                "unauthorized" if rejection == 401 else "forbidden", status_code=rejection
            )
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            return PlainResponse("payload too large", status_code=413)
        rpc_id = "invalid-request"
        try:
            rpc_id, method, args = parse_client_request(body)
            if method != endpoint:
                raise ProtocolError(f"method {method!r} does not match path {endpoint!r}")
            handler = registry.unary_handler(endpoint)
            value = await handler(args)
        except RemoteError as error:
            return JSONResponse(error_response(rpc_id, error))
        except Exception as exc:
            log.exception("unary endpoint %s failed", endpoint)
            return JSONResponse(
                error_response(
                    rpc_id,
                    RemoteError("gateway/internal", str(exc), {"endpoint": endpoint}),
                )
            )
        return JSONResponse(ok_response(rpc_id, value))

    @app.websocket("/api/remote.mux")
    async def mux(websocket: WebSocket) -> None:
        headers = _headers_of(websocket)
        if auth.request_rejection(headers) is not None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        await websocket.accept()
        send_lock = asyncio.Lock()

        async def send(frame: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(frame)

        class _Sink:
            def __init__(self, stream_id: str) -> None:
                self.stream_id = stream_id

            async def send(self, value: Any) -> None:
                await send(item_frame(self.stream_id, value))

        async def run_stream(stream_id: str, endpoint: str, args: dict[str, Any]) -> None:
            sink = _Sink(stream_id)
            try:
                handler = registry.stream_handler(endpoint)
                await handler(args, sink)
            except (asyncio.CancelledError, WebSocketDisconnect):
                raise
            except RemoteError as error:
                await send(error_frame(stream_id, error))
            except Exception as exc:
                log.exception("stream %s failed", endpoint)
                await send(
                    error_frame(
                        stream_id,
                        RemoteError("gateway/internal", str(exc), {"endpoint": endpoint}),
                    )
                )
            else:
                await send(end_frame(stream_id))

        handles: dict[str, _StreamHandle] = {}
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    kind, stream_id, payload = parse_stream_message(text)
                except RemoteError as error:
                    await send(error_frame("invalid", error))
                    continue
                if kind == "cancel":
                    handle = handles.pop(stream_id, None)
                    if access_log:
                        log.info(
                            "web stream cancel %s (known=%s)",
                            stream_id,
                            handle is not None,
                        )
                    if handle is not None:
                        handle.task.cancel()
                    continue
                endpoint, raw_payload = payload
                if access_log:
                    args = request_args(raw_payload)
                    target = (
                        args.get("request") if isinstance(args.get("request"), dict) else args
                    )
                    address = target.get("address") if isinstance(target, dict) else None
                    session_id = ""
                    if isinstance(address, dict):
                        session_id = str(address.get("sessionId") or "")
                    elif isinstance(target, dict):
                        session_id = str(target.get("sessionId") or "")
                    log.info(
                        "web stream open %s%s",
                        endpoint,
                        f" session={session_id}" if session_id else "",
                    )
                task = asyncio.create_task(
                    run_stream(stream_id, endpoint, request_args(raw_payload))
                )
                handles[stream_id] = _StreamHandle(task)

                def _done(_task: asyncio.Task[None], sid: str = stream_id) -> None:
                    handles.pop(sid, None)

                task.add_done_callback(_done)
        except WebSocketDisconnect:
            pass
        finally:
            for handle in list(handles.values()):
                handle.task.cancel()
            for handle in list(handles.values()):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await handle.task

    @app.get("/{path_name:path}")
    async def static_or_index(path_name: str, request: Request) -> Response:
        pathname = f"/{path_name}"
        if pathname.startswith("/plugins/"):
            # dsh combo URLs are `/plugins/??<ids>&rev=<rev>`: the request path is
            # `/plugins/` and the rest lives in the query string, so the bundle
            # key is the request URL rebuilt verbatim.
            query = request.url.query
            bundle_url = f"{pathname}?{query}" if query else pathname
            plugin = assets.plugin_path(bundle_url)
            if plugin is None:
                return PlainResponse(status_code=404)
            return FileResponse(plugin, media_type=_content_type(plugin))

        target = assets.dist_path(pathname)
        is_index = target is not None and target == assets.dist_index.resolve()
        if target is None or not is_index:
            if target is None:
                return PlainResponse(status_code=404)
            return FileResponse(target, media_type=_content_type(target))
        try:
            decision = auth.index_exchange(_headers_of(request), request.query_params.get("token"))
        except PermissionError as exc:
            return PlainResponse(str(exc), status_code=401 if str(exc) == "unauthorized" else 403)
        if decision == "redirect":
            response = RedirectResponse("/", status_code=302)
            response.headers["set-cookie"] = auth.issue_cookie()
            return response
        return PlainResponse(rendered_index, media_type="text/html")

    return app


async def serve(
    *,
    assets: WebAssets,
    runtime: SessionRuntime,
    auth: BrowserAuth,
    registry: EndpointRegistry,
    port: int,
) -> None:
    """Serve the app with uvicorn until cancelled."""
    import uvicorn

    app = create_app(assets=assets, runtime=runtime, auth=auth, registry=registry)
    config = uvicorn.Config(
        app,
        host=auth.host,
        port=port,
        log_level="warning",
        access_log=os.environ.get(ACCESS_LOG_ENV) == "1",
        ws="websockets",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.aclose()
