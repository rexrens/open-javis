"""Render dsh index-injection rows into ``index.html``.

This mirrors dsh's ``renderIndexInjections``: head and body rows are grouped in
table order, ``global`` rows go to the head, and a boot-readiness tail settles
``__DSH_BOOT_READY__`` after the last body row.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

_HEAD_RE = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_BODY_RE = re.compile(r"<body(?:\s[^>]*)?>", re.IGNORECASE)

READY_MARKUP = (
    "<script>(globalThis.__DSH_BOOT_READY__ ??= Promise.withResolvers()).resolve()</script>"
)


def _escape_attribute(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_row(row: dict[str, Any]) -> tuple[str, str]:
    """Render one injection row into (placement, markup)."""
    kind = row.get("kind")
    if kind == "global":
        name = json.dumps(row.get("name", "")).replace("<", "\\u003c")
        value = row.get("value")
        encoded = "undefined" if value is None else json.dumps(value).replace("<", "\\u003c")
        return "head", f"<script>globalThis[{name}] = {encoded}</script>"
    if kind == "script":
        return str(row.get("placement", "head")), f"<script>{row.get('text', '')}</script>"
    if kind == "script-src":
        src = _escape_attribute(str(row.get("src", "")))
        return str(row.get("placement", "head")), f'<script src="{src}"></script>'
    if kind == "script-preload":
        src = _escape_attribute(str(row.get("src", "")))
        return "head", f'<link rel="preload" as="script" href="{src}">'
    if kind == "style":
        return "head", f"<style>{row.get('text', '')}</style>"
    if kind == "html":
        return str(row.get("placement", "head")), str(row.get("html", ""))
    raise ValueError(f"unknown index injection row {kind!r}")


def render_index(html: str, injections: Sequence[dict[str, Any]]) -> str:
    """Insert every injection row plus the readiness tail into ``html``."""
    head = ""
    body = ""
    for row in injections:
        placement, markup = _render_row(row)
        if placement == "head":
            head += markup
        else:
            body += markup
    body += READY_MARKUP

    out = html
    if head:
        match = _HEAD_RE.search(out)
        if match is None:
            out = head + out
        else:
            out = out[: match.end()] + head + out[match.end() :]
    if body:
        match = _BODY_RE.search(out)
        if match is None:
            out = out + body
        else:
            out = out[: match.end()] + body + out[match.end() :]
    return out
