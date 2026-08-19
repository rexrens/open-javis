"""javis workspace layout.

A javis workspace lives at ``~/.javis`` (overridable via ``JAVIS_WORKSPACE``
env var or explicit argument) and holds sessions, memory, skills and logs.
"""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_DIRNAME = ".javis"


def get_workspace_root(workspace: str | Path | None = None) -> Path:
    """Resolve the javis workspace root.

    Resolution order:
        1. Explicit ``workspace`` argument
        2. ``JAVIS_WORKSPACE`` environment variable
        3. ``~/.javis``
    """
    explicit = workspace or os.environ.get("JAVIS_WORKSPACE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / WORKSPACE_DIRNAME).resolve()


def get_memory_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "memory"


def get_skills_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "skills"


def get_plugins_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "plugins"


def get_sessions_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "sessions"


def get_logs_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "logs"


def find_project_javis_dir(cwd: str | Path | None = None) -> Path | None:
    """Walk up from ``cwd`` to the nearest ``.javis/`` directory (project-level).

    Stops at ``$HOME``; returns ``None`` when no project ``.javis/`` exists.
    Note: this may find the global workspace itself when running under ``$HOME``;
    callers that need only project-level config must exclude the global root.
    """
    start = Path(cwd or Path.cwd()).expanduser().resolve()
    home = Path.home().resolve()
    cur = start
    while True:
        candidate = cur / ".javis"
        if candidate.is_dir():
            return candidate
        if cur == home or cur == cur.parent:
            break
        cur = cur.parent
    return None


def ensure_workspace(workspace: str | Path | None = None) -> Path:
    """Create the workspace directory tree if needed and return its root."""
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    for sub in (get_memory_dir, get_skills_dir, get_plugins_dir, get_sessions_dir, get_logs_dir):
        sub(root).mkdir(parents=True, exist_ok=True)
    return root


def initialize_workspace(workspace: str | Path | None = None) -> Path:
    """Create the workspace (idempotent) and return its root."""
    return ensure_workspace(workspace)


def workspace_health(workspace: str | Path | None = None) -> dict[str, bool]:
    """Return presence checks for the key workspace assets."""
    root = get_workspace_root(workspace)
    return {
        "workspace": root.exists(),
        "memory_dir": get_memory_dir(root).exists(),
        "skills_dir": get_skills_dir(root).exists(),
        "plugins_dir": get_plugins_dir(root).exists(),
        "sessions_dir": get_sessions_dir(root).exists(),
        "logs_dir": get_logs_dir(root).exists(),
    }


__all__ = [
    "WORKSPACE_DIRNAME",
    "ensure_workspace",
    "find_project_javis_dir",
    "get_logs_dir",
    "get_memory_dir",
    "get_plugins_dir",
    "get_sessions_dir",
    "get_skills_dir",
    "get_workspace_root",
    "initialize_workspace",
    "workspace_health",
]
