"""Session persistence for javis.

Stores JSON snapshots under ``~/.javis/sessions/``:
    - ``latest.json``         — most recent session
    - ``session-{sid}.json``  — one file per session
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.services.session_storage import (
    _persistable_tool_metadata,
    _sanitize_snapshot_payload,
)
from openharness.utils.fs import atomic_write_text

from javis.workspace import get_sessions_dir


def _session_dir(workspace: str | Path | None = None) -> Path:
    path = get_sessions_dir(workspace)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_key_token(session_key: str) -> str:
    return hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]


def _session_key_latest_path(workspace: str | Path | None, session_key: str) -> Path:
    return _session_dir(workspace) / f"latest-{_session_key_token(session_key)}.json"


def save_session_snapshot(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist the latest javis session snapshot and return its path."""
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

    payload = {
        "app": "javis",
        "session_id": sid,
        "cwd": str(Path(cwd).resolve()),
        "model": model,
        "system_prompt": system_prompt,
        "messages": [m.model_dump(mode="json") for m in messages],
        "usage": usage.model_dump(),
        "tool_metadata": _persistable_tool_metadata(tool_metadata),
        "created_at": now,
        "summary": summary,
        "message_count": len(messages),
    }
    data = json.dumps(payload, indent=2) + "\n"
    latest_path = _session_dir(workspace) / "latest.json"
    atomic_write_text(latest_path, data)
    session_path = _session_dir(workspace) / f"session-{sid}.json"
    atomic_write_text(session_path, data)
    return latest_path


def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
    path = _session_dir(workspace) / "latest.json"
    if not path.exists():
        return None
    return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))


def list_snapshots(workspace: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    session_dir = _session_dir(workspace)
    sessions: list[dict[str, Any]] = []
    for path in sorted(session_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sessions.append(
            {
                "session_id": data.get("session_id", path.stem.replace("session-", "")),
                "summary": data.get("summary", ""),
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            }
        )
        if len(sessions) >= limit:
            break
    return sessions


def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
    path = _session_dir(workspace) / f"session-{session_id}.json"
    if path.exists():
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    latest = load_latest(workspace)
    if latest and (latest.get("session_id") == session_id or session_id == "latest"):
        return latest
    return None


def export_session_markdown(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    messages: list[ConversationMessage],
) -> Path:
    del cwd  # unused — kept for parity with SessionBackend.export_markdown
    path = _session_dir(workspace) / "transcript.md"
    parts = ["# javis Session Transcript"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path


class JavisSessionBackend:
    """Session backend rooted in ``~/.javis/sessions``."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = workspace

    def get_session_dir(self, cwd: str | Path) -> Path:
        del cwd  # javis sessions are workspace-global, not per-cwd
        return _session_dir(self._workspace)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return save_session_snapshot(
            cwd=cwd,
            workspace=self._workspace,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        del cwd
        return load_latest(self._workspace)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        del cwd
        return list_snapshots(self._workspace, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        del cwd
        return load_by_id(self._workspace, session_id)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        return export_session_markdown(cwd=cwd, workspace=self._workspace, messages=messages)


__all__ = [
    "JavisSessionBackend",
    "export_session_markdown",
    "list_snapshots",
    "load_by_id",
    "load_latest",
    "save_session_snapshot",
]
