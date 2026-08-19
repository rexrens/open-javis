"""javis configuration — pydantic-validated, layered (global < project).

Design: spec/config.md (v2).

- ``config.json`` holds non-secret settings; secrets go to env vars / ``.env``
- Layering: built-in defaults < ``~/.javis/config.json`` < ``<project>/.javis/config.json``
  (deep-merged, later wins on conflicts) < CLI/env
- Unknown keys are tolerated (warned) so plugins can register their own
  top-level namespaces later.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from javis.session.workspace import find_project_javis_dir, get_workspace_root

log = logging.getLogger(__name__)

DEFAULT_ENGINE = "corecoder"
CONFIG_FILENAME = "config.json"


def _to_camel(name: str) -> str:
    """snake_case -> camelCase (config.json uses camelCase, models use snake_case)."""
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


# ---------------------------------------------------------------------------
# pydantic models
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    context_window: int = 128_000
    max_tokens: int = 8192


class ProviderConfig(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    base_url: str
    api: Literal["openai-completions", "openai", "anthropic", "litellm"] = "openai-completions"
    api_key_env: str | None = None
    api_key: str | None = None  # legacy compat; prefer env vars / .env
    models: list[ModelConfig] = Field(default_factory=list)


class AppearanceConfig(BaseModel):
    theme: str = "default"
    output_style: str = "default"


class SessionConfig(BaseModel):
    max_turns: int | None = 32
    permission_mode: Literal["default", "plan", "full_auto"] = "default"
    fast_mode: bool = False


class EditorConfig(BaseModel):
    vim_enabled: bool = False


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"


class PathRule(BaseModel):
    pattern: str
    allow: bool = False


class PermissionConfig(BaseModel):
    mode: Literal["default", "plan", "full_auto"] = "default"
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    path_rules: list[PathRule] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)


class JavisConfig(BaseModel):
    """Top-level validated configuration."""

    model_config = ConfigDict(extra="allow")  # tolerate plugin namespaces

    engine: str = DEFAULT_ENGINE
    provider: str | None = None
    model: str | None = None
    fallback_provider: str | None = None  # reserved — not implemented yet
    fallback_model: str | None = None  # reserved — not implemented yet
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    appearance: AppearanceConfig = Field(default_factory=AppearanceConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    editor: EditorConfig = Field(default_factory=EditorConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    plugins: dict[str, Any] = Field(default_factory=dict)

    def warn_unknown_keys(self) -> None:
        extra = self.model_extra or {}
        if extra:
            log.warning("config.json: unknown keys ignored: %s", ", ".join(sorted(extra)))


# ---------------------------------------------------------------------------
# defaults & template
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATE: dict[str, Any] = {
    "engine": DEFAULT_ENGINE,
    "provider": "",
    "model": "",
    "providers": {
        "deepseek": {
            "baseUrl": "https://api.deepseek.com",
            "api": "openai-completions",
            "apiKeyEnv": "DEEPSEEK_API_KEY",
            "models": [
                {"id": "deepseek-chat", "contextWindow": 128000, "maxTokens": 8192}
            ],
        }
    },
    "appearance": {"theme": "default", "output_style": "default"},
    "session": {"max_turns": 32, "permission_mode": "default", "fast_mode": False},
    "editor": {"vim_enabled": False},
    "logging": {"level": "info"},
    "permission": {
        "mode": "default",
        "allowed_tools": [],
        "denied_tools": [],
        "path_rules": [],
        "denied_commands": [],
    },
    "plugins": {},
}


# ---------------------------------------------------------------------------
# merge & load
# ---------------------------------------------------------------------------


def deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge: override wins on scalars, dicts merge, lists replace."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_default_config(workspace: str | Path | None = None) -> Path:
    """Create ``<workspace>/config.json`` with the default template if missing."""
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    path = root / CONFIG_FILENAME
    if not path.exists():
        path.write_text(
            json.dumps(DEFAULT_TEMPLATE, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Created default config at %s", path)
    return path


def load_config(
    cwd: str | Path | None = None,
    workspace: str | Path | None = None,
) -> JavisConfig:
    """Load global + project config.json, deep-merge, validate.

    Raises ``ValueError`` on JSON syntax errors or a non-object root.
    """
    ensure_default_config(workspace)
    global_root = get_workspace_root(workspace)
    merged: dict[str, Any] = {}

    global_path = global_root / CONFIG_FILENAME
    if global_path.exists():
        merged = _read_json_object(global_path)

    project_dir = find_project_javis_dir(cwd)
    if project_dir is not None and project_dir.resolve() != global_root.resolve():
        merged = deep_merge(merged, _read_json_object(project_dir / CONFIG_FILENAME))

    config = JavisConfig.model_validate(merged)
    config.warn_unknown_keys()
    return config


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004 — keeps JSON family errors under ValueError
            f"{path} must contain a JSON object, got {type(data).__name__}"
        )
    return data


# ---------------------------------------------------------------------------
# resolution helpers
# ---------------------------------------------------------------------------


def resolve_engine_name(
    cli: str | None = None,
    config: JavisConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active engine name: CLI > env JAVIS_ENGINE > config > default."""
    env = env if env is not None else os.environ
    if cli:
        return cli
    if env.get("JAVIS_ENGINE"):
        return env["JAVIS_ENGINE"]
    if config is not None and config.engine:
        return config.engine
    return DEFAULT_ENGINE


def resolve_provider_and_model(
    config: JavisConfig,
    cli_model: str | None = None,
) -> tuple[str, str]:
    """Resolve ``(provider_name, model_id)`` from config + CLI override.

    - provider: explicit ``config.provider``, else the first configured provider
    - model: ``cli_model`` > ``config.model`` > provider's first model
    """
    provider = config.provider
    if not provider:
        if not config.providers:
            raise ValueError("No providers configured in config.json")
        provider = next(iter(config.providers))
    if provider not in config.providers:
        available = ", ".join(sorted(config.providers)) or "(none)"
        raise ValueError(f"Unknown provider {provider!r}; available: {available}")

    model = cli_model or config.model
    if not model:
        models = config.providers[provider].models
        if not models:
            raise ValueError(f"Provider {provider!r} has no models configured")
        model = models[0].id
    return provider, model


__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_ENGINE",
    "DEFAULT_TEMPLATE",
    "AppearanceConfig",
    "EditorConfig",
    "JavisConfig",
    "LoggingConfig",
    "ModelConfig",
    "PathRule",
    "PermissionConfig",
    "ProviderConfig",
    "SessionConfig",
    "deep_merge",
    "ensure_default_config",
    "load_config",
    "resolve_engine_name",
    "resolve_provider_and_model",
]
