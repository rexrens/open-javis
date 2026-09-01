"""Tests for credential resolution (spec/config.md v2):
env vars > ~/.javis/.env > project .env > legacy apiKey."""

from __future__ import annotations

import pytest

from javis.session.credentials import env_var_name, parse_env_file, resolve_api_key
from javis.session.workspace import get_workspace_root


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    (tmp_path / "javis-workspace").mkdir(exist_ok=True)
    # strip credential env vars so tests are deterministic
    for var in ("DEEPSEEK_API_KEY", "CORECODER_API_KEY", "OPENAI_API_KEY", "VLLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_env_var_name():
    assert env_var_name("deepseek") == "DEEPSEEK_API_KEY"
    assert env_var_name("my-vllm") == "MY_VLLM_API_KEY"
    assert env_var_name("openai") == "OPENAI_API_KEY"


def test_parse_env_file_basic(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nDEEPSEEK_API_KEY=sk-abc\nexport OPENAI_API_KEY=sk-xyz\n", encoding="utf-8")
    data = parse_env_file(env)
    assert data == {"DEEPSEEK_API_KEY": "sk-abc", "OPENAI_API_KEY": "sk-xyz"}


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert parse_env_file(tmp_path / "nope.env") == {}


def test_resolve_priority_env_var_wins(isolated_env, monkeypatch):
    (get_workspace_root() / ".env").write_text("DEEPSEEK_API_KEY=from-envfile\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-process")
    assert resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(isolated_env)) == "from-process"


def test_resolve_from_user_env_file(isolated_env):
    (get_workspace_root() / ".env").write_text("DEEPSEEK_API_KEY=sk-user\n", encoding="utf-8")
    assert resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(isolated_env)) == "sk-user"


def test_resolve_from_project_env_file(isolated_env, tmp_path):
    project_dir = tmp_path / "proj" / ".javis"
    project_dir.mkdir(parents=True)
    (project_dir / ".env").write_text("DEEPSEEK_API_KEY=sk-project\n", encoding="utf-8")
    assert (
        resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(tmp_path / "proj"))
        == "sk-project"
    )


def test_resolve_global_env_beats_project_env(isolated_env, tmp_path):
    (get_workspace_root() / ".env").write_text("DEEPSEEK_API_KEY=sk-user\n", encoding="utf-8")
    project_dir = tmp_path / "proj" / ".javis"
    project_dir.mkdir(parents=True)
    (project_dir / ".env").write_text("DEEPSEEK_API_KEY=sk-project\n", encoding="utf-8")
    assert (
        resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(tmp_path / "proj"))
        == "sk-user"
    )


def test_resolve_global_fallback_provider_key(isolated_env, monkeypatch):
    monkeypatch.setenv("CORECODER_API_KEY", "sk-fallback")
    assert resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(isolated_env)) == "sk-fallback"


def test_resolve_explicit_api_key_env_wins_over_inference(isolated_env, monkeypatch):
    monkeypatch.setenv("MY_SPECIAL_KEY", "sk-special")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-inferred")
    assert (
        resolve_api_key("deepseek", "MY_SPECIAL_KEY", workspace=get_workspace_root(), cwd=str(isolated_env))
        == "sk-special"
    )


def test_resolve_legacy_config_api_key_last(isolated_env):
    assert (
        resolve_api_key(
            "deepseek",
            config_api_key="sk-legacy",
            workspace=get_workspace_root(),
            cwd=str(isolated_env),
        )
        == "sk-legacy"
    )


def test_resolve_none_when_nothing_configured(isolated_env):
    assert resolve_api_key("deepseek", workspace=get_workspace_root(), cwd=str(isolated_env)) is None
