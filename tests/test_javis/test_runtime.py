"""Tests for build_javis_runtime assembly."""

from __future__ import annotations

import pytest

from javis.contracts.engine import AgentEngine
from javis.host.runtime import RuntimeBundle, build_javis_runtime
from javis.session.session_storage import JavisSessionBackend
from tests.test_javis.fake_backend import FakeEngine


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
    bundle = await build_javis_runtime(cwd=str(isolated_env), engine=FakeEngine())
    assert isinstance(bundle, RuntimeBundle)


@pytest.mark.asyncio
async def test_build_javis_runtime_uses_agent_engine(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), engine=FakeEngine())
    assert isinstance(bundle.engine, AgentEngine)
    assert isinstance(bundle.engine, FakeEngine)
    assert bundle.engine.model  # non-empty model resolved from env/config


@pytest.mark.asyncio
async def test_build_javis_runtime_injects_custom_agent(isolated_env):
    bundle = await build_javis_runtime(
        cwd=str(isolated_env),
        engine=FakeEngine(),
        model="test-model",
        system_prompt="test prompt",
    )
    assert bundle.engine.model == "test-model"
    assert bundle.engine.system_prompt == "test prompt"
    assert isinstance(bundle.engine, FakeEngine)


@pytest.mark.asyncio
async def test_build_javis_runtime_session_backend(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), engine=FakeEngine())
    assert isinstance(bundle.session_backend, JavisSessionBackend)


@pytest.mark.asyncio
async def test_build_javis_runtime_preserves_cwd(isolated_env):
    cwd = str(isolated_env / "project")
    bundle = await build_javis_runtime(cwd=cwd, engine=FakeEngine())
    assert bundle.cwd == cwd
    assert bundle.engine.system_prompt  # non-empty default


@pytest.mark.asyncio
async def test_build_javis_runtime_restores_messages(isolated_env):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), engine=FakeEngine(), restore_messages=messages
    )
    assert len(bundle.engine.messages) == 2
    assert bundle.engine.messages[0].role == "user"
    assert bundle.engine.messages[1].role == "assistant"


@pytest.mark.asyncio
async def test_build_javis_runtime_accepts_custom_model(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), model="custom-model", engine=FakeEngine())
    assert bundle.engine.model == "custom-model"


@pytest.mark.asyncio
async def test_build_javis_runtime_includes_commands(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), engine=FakeEngine())
    command_names = {cmd.name for cmd in bundle.commands.list_commands()}
    assert "help" in command_names
    assert "exit" in command_names
    assert "clear" in command_names
    assert "theme" in command_names
    assert "turns" in command_names
    assert "permissions" in command_names


@pytest.mark.asyncio
async def test_build_javis_runtime_default_engine_is_corecoder(isolated_env, monkeypatch):
    from javis.engines.corecoder.agent import Agent
    from javis.engines.corecoder.engine import CoreCoderEngine

    # The installed openai SDK refuses to construct a client without a
    # non-empty api_key (it validates credentials eagerly). The fixture already
    # stripped proxy vars; supply a dummy key so AsyncOpenAI can build.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    bundle = await build_javis_runtime(cwd=str(isolated_env))
    assert isinstance(bundle.engine, CoreCoderEngine)
    assert isinstance(bundle.engine.agent, Agent)


@pytest.mark.asyncio
async def test_print_mode_treats_slash_prompt_as_user_message(
    isolated_env, monkeypatch, capsys
):
    """Print mode is a plain prompt: ``/version`` must not dispatch as a command."""
    from javis.commands.registry import create_default_command_registry
    from javis.host.runtime import run_javis_print_mode
    from javis.session.state import AppState, AppStateStore

    bundle = RuntimeBundle(
        engine=FakeEngine(),
        cwd=str(isolated_env),
        app_state=AppStateStore(AppState(model="test-model", cwd=str(isolated_env))),
        commands=create_default_command_registry(),
        session_backend=JavisSessionBackend(isolated_env / "javis-workspace"),
        session_id="print-test",
    )

    async def _fake_build(**kwargs: object) -> RuntimeBundle:
        return bundle

    async def _noop(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr("javis.host.runtime.build_javis_runtime", _fake_build)
    monkeypatch.setattr("javis.host.runtime.start_runtime", _noop)
    monkeypatch.setattr("javis.host.runtime.close_runtime", _noop)

    exit_code = await run_javis_print_mode(prompt="/version", cwd=str(isolated_env))

    assert exit_code == 0
    # The registered /version command was NOT dispatched: it reached the engine
    # as a plain user message.
    assert bundle.engine.messages
    assert bundle.engine.messages[0].role == "user"
    assert bundle.engine.messages[0].text == "/version"
    assert "fake reply to: /version" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_build_javis_runtime_restore_loads_messages(isolated_env):
    class RecordingEngine(FakeEngine):
        def __init__(self) -> None:
            super().__init__()
            self.load_calls = 0

        def load_messages(self, messages) -> None:
            super().load_messages(messages)
            self.load_calls += 1

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    engine = RecordingEngine()
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), engine=engine, restore_messages=messages
    )
    assert engine.load_calls == 1
    assert len(bundle.engine.messages) == 2
