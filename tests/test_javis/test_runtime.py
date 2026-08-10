"""Tests for build_javis_runtime assembly."""

from __future__ import annotations

import pytest

from openharness.ui.runtime import RuntimeBundle

from javis.engine.mock_engine import MockEngine
from javis.runtime import MockApiClient, build_javis_runtime
from javis.session_storage import JavisSessionBackend


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    return tmp_path


@pytest.mark.asyncio
async def test_build_javis_runtime_returns_bundle(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env))
    assert isinstance(bundle, RuntimeBundle)


@pytest.mark.asyncio
async def test_build_javis_runtime_uses_mock_engine(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env))
    assert isinstance(bundle.engine, MockEngine)
    assert bundle.engine.model == "javis-mock"


@pytest.mark.asyncio
async def test_build_javis_runtime_uses_mock_api_client(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env))
    assert isinstance(bundle.api_client, MockApiClient)
    assert bundle.external_api_client is True


@pytest.mark.asyncio
async def test_build_javis_runtime_session_backend(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env))
    assert isinstance(bundle.session_backend, JavisSessionBackend)


@pytest.mark.asyncio
async def test_build_javis_runtime_preserves_cwd(isolated_env):
    cwd = str(isolated_env / "project")
    bundle = await build_javis_runtime(cwd=cwd)
    assert bundle.cwd == cwd
    assert bundle.engine.system_prompt  # non-empty default


@pytest.mark.asyncio
async def test_build_javis_runtime_restores_messages(isolated_env):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    bundle = await build_javis_runtime(cwd=str(isolated_env), restore_messages=messages)
    assert len(bundle.engine.messages) == 2
    assert bundle.engine.messages[0].role == "user"
    assert bundle.engine.messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_build_javis_runtime_accepts_custom_model(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), model="custom-model")
    assert bundle.engine.model == "custom-model"


@pytest.mark.asyncio
async def test_build_javis_runtime_includes_commands(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env))
    command_names = {cmd.name for cmd in bundle.commands.list_commands()}
    assert "help" in command_names
    assert "exit" in command_names
    assert "clear" in command_names
