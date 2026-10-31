"""dsh Connection wire protocol: unary envelopes and mux stream frames."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class RemoteError(Exception):
    """One structured endpoint failure carried by the ``ok: false`` envelope."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


class ProtocolError(RemoteError):
    """A malformed request envelope; answered with ``gateway/bad-request``."""

    def __init__(self, message: str) -> None:
        super().__init__("gateway/bad-request", message)


def parse_client_request(body: bytes) -> tuple[str, str, dict[str, Any]]:
    """Parse one ``POST /api/<ns>/<method>`` body into (rpcId, method, args).

    The wire envelope is strict: unknown top-level fields are a bad request, and
    the payload must carry a named ``args`` object.
    """
    try:
        value = json.loads(body or b"{}")
    except ValueError as exc:
        raise ProtocolError(f"invalid JSON body: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("request envelope must be an object")
    if value.get("type") != "client-request":
        raise ProtocolError("request envelope type must be 'client-request'")
    rpc_id = value.get("rpcId")
    method = value.get("method")
    if not isinstance(rpc_id, str) or rpc_id == "":
        raise ProtocolError("request envelope needs a non-empty rpcId")
    if not isinstance(method, str) or method == "":
        raise ProtocolError("request envelope needs a non-empty method")
    payload = value.get("payload")
    args: Any = {}
    if isinstance(payload, dict):
        args = payload.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError("request payload args must be an object")
    return rpc_id, method, dict(args)


def ok_response(rpc_id: str, value: Any) -> dict[str, Any]:
    """Build one success response envelope."""
    return {
        "type": "server-response",
        "rpcId": rpc_id,
        "result": {"ok": True, "value": value},
    }


def error_response(rpc_id: str, error: RemoteError) -> dict[str, Any]:
    """Build one failure response envelope."""
    return {
        "type": "server-response",
        "rpcId": rpc_id,
        "result": {
            "ok": False,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
        },
    }


def item_frame(stream_id: str, value: Any) -> dict[str, Any]:
    """One logical stream item."""
    return {"type": "item", "streamId": stream_id, "value": value}


def end_frame(stream_id: str) -> dict[str, Any]:
    """Terminal frame for one logical stream."""
    return {"type": "end", "streamId": stream_id}


def error_frame(stream_id: str, error: RemoteError) -> dict[str, Any]:
    """Terminal failure frame for one logical stream."""
    return {
        "type": "error",
        "streamId": stream_id,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    }


def parse_stream_message(text: str) -> tuple[str, str, Any]:
    """Parse one browser-to-host mux message into (kind, streamId, payload)."""
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise ProtocolError(f"invalid stream frame JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("stream frame must be an object")
    kind = value.get("type")
    stream_id = value.get("streamId")
    if not isinstance(stream_id, str) or stream_id == "":
        raise ProtocolError("stream frame needs a non-empty streamId")
    if kind == "open":
        endpoint = value.get("endpoint")
        if not isinstance(endpoint, str) or endpoint == "":
            raise ProtocolError("open frame needs a non-empty endpoint")
        return "open", stream_id, (endpoint, value.get("payload"))
    if kind == "cancel":
        return "cancel", stream_id, None
    raise ProtocolError("stream frame type must be 'open' or 'cancel'")


def request_args(payload: Any) -> dict[str, Any]:
    """Read the ``args`` object out of a stream open payload."""
    if isinstance(payload, dict):
        args = payload.get("args", {})
        if isinstance(args, dict):
            return dict(args)
    return {}
