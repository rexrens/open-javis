"""Tests for javis configuration (spec/config.md v2): pydantic models,
layered loading (global < project), deep merge, defaults, and resolution."""

from __future__ import annotations

import json

import pytest

from javis.session.config import (
    DEFAULT_ENGINE,
    JavisConfig,
    deep_merge,
    ensure_default_config,
    load_config,
    resolve_provider_and_model,
)
from javis.session.workspace import get_workspace_root


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    (tmp_path / "javis-workspace").mkdir(exist_ok=True)
    return tmp_path


# --- defaults & template ---


def test_default_engine_is_corecoder():
    assert DEFAULT_ENGINE == "corecoder"


def test_ensure_default_config_creates_template(isolated_env):
    path = ensure_default_config()
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "engine" not in data  # engine selector removed; fixed to builtin corecoder
    assert "providers" in data
    assert "appearance" in data
    assert "session" in data
    assert "permission" in data
    assert "plugins" in data


def test_ensure_default_config_idempotent(isolated_env):
    ensure_default_config()
    path = ensure_default_config()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "engine" not in data


# --- load & validate ---


def test_load_config_empty_workspace_creates_default(isolated_env):
    cfg = load_config()
    assert cfg.providers  # template ships a deepseek provider
    assert cfg.session.max_turns == 32


def test_load_config_parses_providers(isolated_env):
    (get_workspace_root() / "config.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-4o",
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.openai.com/v1",
                        "api": "openai",
                        "models": [
                            {"id": "gpt-4o", "contextWindow": 128000, "maxTokens": 16384}
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.providers["openai"].base_url == "https://api.openai.com/v1"
    assert cfg.providers["openai"].models[0].max_tokens == 16384


def test_load_config_invalid_json_raises(isolated_env):
    (get_workspace_root() / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_config()


def test_load_config_non_object_root_raises(isolated_env):
    (get_workspace_root() / "config.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_config()


def test_unknown_keys_tolerated(isolated_env):
    (get_workspace_root() / "config.json").write_text(
        json.dumps({"my-plugin": {"x": 1}}), encoding="utf-8"
    )
    cfg = load_config()
    assert cfg.model_extra == {"my-plugin": {"x": 1}}


def test_invalid_field_value_rejected(isolated_env):
    (get_workspace_root() / "config.json").write_text(
        json.dumps({"session": {"permission_mode": "bogus"}}), encoding="utf-8"
    )
    with pytest.raises(Exception):
        load_config()


# --- deep merge ---


def test_deep_merge_scalars_override():
    merged = deep_merge({"a": 1, "b": {"x": 1}}, {"a": 2, "b": {"y": 2}})
    assert merged == {"a": 2, "b": {"x": 1, "y": 2}}


def test_deep_merge_lists_replace():
    merged = deep_merge({"tools": ["a"]}, {"tools": ["b", "c"]})
    assert merged["tools"] == ["b", "c"]


def test_project_config_overrides_global(isolated_env, tmp_path):
    global_cfg = get_workspace_root()
    (global_cfg / "config.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "appearance": {"theme": "default"},
            }
        ),
        encoding="utf-8",
    )
    # project-level config in a subdir under cwd
    project_dir = tmp_path / "proj" / ".javis"
    project_dir.mkdir(parents=True)
    (project_dir / "config.json").write_text(
        json.dumps({"appearance": {"theme": "dark"}}), encoding="utf-8"
    )
    cfg = load_config(cwd=str(tmp_path / "proj"))
    assert cfg.provider == "deepseek"  # from global
    assert cfg.model == "deepseek-chat"  # from global
    assert cfg.appearance.theme == "dark"  # project overrides global


# --- resolution ---


def test_resolve_provider_defaults_to_first():
    config = JavisConfig(providers={"a": {"base_url": "http://a", "models": [{"id": "a1"}]}})
    provider, model = resolve_provider_and_model(config)
    assert (provider, model) == ("a", "a1")


def test_resolve_provider_explicit_model():
    config = JavisConfig(
        provider="b",
        model="b2",
        providers={"b": {"base_url": "http://b", "models": [{"id": "b1"}, {"id": "b2"}]}},
    )
    provider, model = resolve_provider_and_model(config)
    assert (provider, model) == ("b", "b2")


def test_resolve_provider_cli_model_wins():
    config = JavisConfig(
        provider="b",
        model="b2",
        providers={"b": {"base_url": "http://b", "models": [{"id": "b1"}]}},
    )
    provider, model = resolve_provider_and_model(config, cli_model="b1")
    assert model == "b1"


def test_resolve_provider_unknown_raises():
    config = JavisConfig(provider="nope", providers={"a": {"base_url": "http://a"}})
    with pytest.raises(ValueError, match="Unknown provider 'nope'"):
        resolve_provider_and_model(config)


def test_resolve_provider_no_providers_raises():
    with pytest.raises(ValueError, match="No providers"):
        resolve_provider_and_model(JavisConfig())
