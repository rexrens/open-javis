"""Tests for build_javis_runtime assembly."""

from __future__ import annotations

import pytest

from tests.test_javis.fake_backend import FakeBackend
from javis.host.query_engine import QueryEngine
from javis.host.runtime import RuntimeBundle, build_javis_runtime
from javis.session.session_storage import JavisSessionBackend


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    # A local socks proxy in the environment breaks httpx client construction
    # (socksio not installed); drop proxy vars so OpenAI clients can build.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_build_javis_runtime_returns_bundle(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert isinstance(bundle, RuntimeBundle)


@pytest.mark.asyncio
async def test_build_javis_runtime_uses_query_engine(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert isinstance(bundle.engine, QueryEngine)
    assert isinstance(bundle.engine._agent, FakeBackend)
    assert bundle.engine.model  # non-empty model resolved from env/config


@pytest.mark.asyncio
async def test_build_javis_runtime_injects_custom_agent(isolated_env):
    bundle = await build_javis_runtime(
        cwd=str(isolated_env),
        agent_backend=FakeBackend(),
        model="test-model",
        system_prompt="test prompt",
    )
    assert bundle.engine.model == "test-model"
    assert bundle.engine.system_prompt == "test prompt"
    assert isinstance(bundle.engine._agent, FakeBackend)


@pytest.mark.asyncio
async def test_build_javis_runtime_session_backend(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert isinstance(bundle.session_backend, JavisSessionBackend)


@pytest.mark.asyncio
async def test_build_javis_runtime_preserves_cwd(isolated_env):
    cwd = str(isolated_env / "project")
    bundle = await build_javis_runtime(cwd=cwd, agent_backend=FakeBackend())
    assert bundle.cwd == cwd
    assert bundle.engine.system_prompt  # non-empty default


@pytest.mark.asyncio
async def test_build_javis_runtime_restores_messages(isolated_env):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), agent_backend=FakeBackend(), restore_messages=messages
    )
    assert len(bundle.engine.messages) == 2
    assert bundle.engine.messages[0].role == "user"
    assert bundle.engine.messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_build_javis_runtime_accepts_custom_model(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), model="custom-model", agent_backend=FakeBackend())
    assert bundle.engine.model == "custom-model"


@pytest.mark.asyncio
async def test_build_javis_runtime_includes_commands(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    command_names = {cmd.name for cmd in bundle.commands.list_commands()}
    assert "help" in command_names
    assert "exit" in command_names
    assert "clear" in command_names


@pytest.mark.asyncio
async def test_build_javis_runtime_default_engine_is_corecoder(isolated_env, monkeypatch):
    from javis.engines.corecoder.backend import CoreCoderBackend

    # The installed openai SDK refuses to construct a client without a
    # non-empty api_key (it validates credentials eagerly). The fixture already
    # stripped proxy vars; supply a dummy key so AsyncOpenAI can build.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    bundle = await build_javis_runtime(cwd=str(isolated_env), engine="corecoder")
    assert isinstance(bundle.engine._agent, CoreCoderBackend)



@pytest.mark.asyncio
async def test_build_javis_runtime_engine_and_backend_mutually_exclusive(isolated_env):
    with pytest.raises(ValueError, match="either engine= or agent_backend="):
        await build_javis_runtime(cwd=str(isolated_env), engine="corecoder", agent_backend=FakeBackend())


@pytest.mark.asyncio
async def test_build_javis_runtime_unknown_engine_raises(isolated_env):
    with pytest.raises(ValueError, match="Unknown engine 'nope'"):
        await build_javis_runtime(cwd=str(isolated_env), engine="nope")


@pytest.mark.asyncio
async def test_build_javis_runtime_restore_calls_backend_load_history(isolated_env):
    class RecordingBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.history_calls = 0

        def load_history(self, messages) -> None:
            self.history_calls += 1

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    backend = RecordingBackend()
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), agent_backend=backend, restore_messages=messages
    )
    assert backend.history_calls == 1
    assert len(bundle.engine.messages) == 2
