"""Credential resolution — env vars > ~/.javis/.env > project .env > legacy apiKey.

Design: spec/config.md (v2), section "密钥解析优先级".

Only two ``.env`` locations are ever read (never arbitrary cwd files):
    - ``~/.javis/.env``          (user-level)
    - ``<project>/.javis/.env``  (project-level, walked up from cwd)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from javis.session.workspace import find_project_javis_dir, get_workspace_root

log = logging.getLogger(__name__)

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal dotenv parser: ``KEY=VALUE`` lines, ``#`` comments, no expansion."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def env_var_name(provider: str) -> str:
    """``deepseek`` -> ``DEEPSEEK_API_KEY``; non-alnum chars become underscores."""
    name = re.sub(r"[^A-Za-z0-9]", "_", provider).upper()
    return f"{name}_API_KEY"


def _user_env_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / ".env"


def _project_env_path(cwd: str | Path | None = None) -> Path | None:
    project_dir = find_project_javis_dir(cwd)
    if project_dir is None:
        return None
    return project_dir / ".env"


def resolve_api_key(
    provider: str,
    api_key_env: str | None = None,
    config_api_key: str | None = None,
    *,
    workspace: str | Path | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    """Resolve an API key by priority:

    1. process env vars — ``apiKeyEnv``, else ``<PROVIDER>_API_KEY``, then
       ``CORECODER_API_KEY`` as a global fallback
    2. ``~/.javis/.env`` then ``<project>/.javis/.env`` (same var names)
    3. legacy ``config.json`` ``apiKey`` (warned)
    """
    names: list[str] = []
    for candidate in (api_key_env, env_var_name(provider), "CORECODER_API_KEY"):
        if candidate and candidate not in names:
            names.append(candidate)

    for name in names:
        value = os.environ.get(name)
        if value:
            return value

    for env_path in (_user_env_path(workspace), _project_env_path(cwd)):
        if env_path is None:
            continue
        data = parse_env_file(env_path)
        for name in names:
            value = data.get(name)
            if value:
                return value

    if config_api_key:
        log.warning(
            "config.json apiKey used for provider %r — prefer env vars or .env", provider
        )
        return config_api_key
    return None


__all__ = ["env_var_name", "parse_env_file", "resolve_api_key"]
