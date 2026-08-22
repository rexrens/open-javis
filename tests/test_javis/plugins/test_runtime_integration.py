"""End-to-end: plugins loaded through build_javis_runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from javis.host.runtime import build_javis_runtime
from tests.test_javis.fake_backend import FakeBackend

PLUGIN_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_build_loads_plugins_from_plugin_dirs(isolated_env, monkeypatch):
    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert bundle.plugins is not None
    names = {p["name"] for p in bundle.plugins.list_plugins()}
    assert {"simple_apply", "decl-plugin", "obj-plugin", "multi-a", "multi-b"} <= names


@pytest.mark.asyncio
async def test_plugin_command_registered_into_bundle(isolated_env, monkeypatch):
    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert bundle.commands.lookup("/plughello") is not None  # plugin command
    assert bundle.commands.lookup("/help") is not None  # built-ins still present


@pytest.mark.asyncio
async def test_plugin_tool_registered_into_corecoder(isolated_env, monkeypatch):
    from corecoder.tools import get_tool

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert get_tool("plug_tool") is not None


@pytest.mark.asyncio
async def test_close_runtime_runs_disposers(isolated_env, monkeypatch):
    from javis.host.runtime import close_runtime

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    await close_runtime(bundle)
    for p in bundle.plugins.list_plugins():
        assert p["state"].value == "disposed"


@pytest.mark.asyncio
async def test_close_runtime_unregisters_plugin_tools(isolated_env, monkeypatch):
    from corecoder.tools import get_tool
    from javis.host.runtime import close_runtime

    monkeypatch.setattr("javis.host.runtime._build_plugin_dirs", lambda **kw: [PLUGIN_DIR])
    bundle = await build_javis_runtime(cwd=str(isolated_env), agent_backend=FakeBackend())
    assert get_tool("plug_tool") is not None
    await close_runtime(bundle)
    assert get_tool("plug_tool") is None
