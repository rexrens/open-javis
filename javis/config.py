"""javis configuration — engine selection from config.json, env and CLI.

Priority: CLI --engine > env JAVIS_ENGINE > config.json "engine" > default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from javis.workspace import get_workspace_root

DEFAULT_ENGINE = "corecoder"

CONFIG_FILENAME = "config.json"


def load_config(workspace: str | Path | None = None) -> dict:
    """Read <workspace>/config.json. Missing or corrupt file -> {}."""
    config_path = get_workspace_root(workspace) / CONFIG_FILENAME
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_engine_name(
    cli: str | None = None,
    config: dict | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active engine name by priority: CLI > env > config > default."""
    env = env if env is not None else os.environ
    config = config or {}
    if cli:
        return cli
    if env.get("JAVIS_ENGINE"):
        return env["JAVIS_ENGINE"]
    if config.get("engine"):
        return str(config["engine"])
    return DEFAULT_ENGINE


__all__ = ["CONFIG_FILENAME", "DEFAULT_ENGINE", "load_config", "resolve_engine_name"]
