"""Shared argument helpers for endpoint handlers."""

from __future__ import annotations

from typing import Any

from javis.app.web.protocol import RemoteError

#: Host parameter names that carry a Remote method's single request object.
REQUEST_PARAMETERS = ("request", "_request")


def endpoint_args(args: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the request object dsh projects into the wire ``args`` map.

    The wire form is keyed by Host parameter name, so a method declared as
    ``prompt(request)`` arrives as ``{"request": {...}}`` while one declared as
    ``describe(ns)`` stays flat. Endpoints read their fields from the unwrapped
    object.
    """
    for name in REQUEST_PARAMETERS:
        inner = args.get(name)
        if isinstance(inner, dict):
            return dict(inner)
    return dict(args)


def require_str(args: dict[str, Any], key: str) -> str:
    """Read a required non-empty string argument."""
    value = args.get(key)
    if not isinstance(value, str) or value == "":
        raise RemoteError("gateway/bad-request", f"argument {key!r} must be a non-empty string")
    return value


def session_id_of(args: dict[str, Any]) -> str | None:
    """Read the addressed session id from a SessionAddress or a flat field.

    Returns None when the client addresses no session yet — the browser opens
    a blank conversation with an empty identity and expects the host to own the
    first real session.
    """
    address = args.get("address")
    if isinstance(address, dict):
        session_id = address.get("sessionId")
        if isinstance(session_id, str) and session_id:
            return session_id
    session_id = args.get("sessionId")
    return session_id if isinstance(session_id, str) and session_id else None


def text_of_content(content: Any) -> str:
    """Join the text parts of one browser prompt content array."""
    if not isinstance(content, list):
        raise RemoteError("gateway/bad-request", "prompt content must be an array")
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            parts.append(part["text"])
    text = "".join(parts)
    if text.strip() == "":
        raise RemoteError("gateway/bad-request", "prompt content carries no text")
    return text
