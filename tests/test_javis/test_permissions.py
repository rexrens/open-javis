"""Tests for the permission gate: decide_permission + modal ask channel."""

from __future__ import annotations

import asyncio

import pytest

from javis.host.backend_host import (
    _BackendHostConfig,
    _JavisBackendHost,
    decide_permission,
)
from javis.host.runtime import build_javis_runtime
from tests.test_javis.fake_backend import FakeEngine


# --- pure decision logic ---


def test_decide_full_auto_allows_everything():
    for tool in ("write_file", "edit_file", "bash", "read_file", "grep", "agent"):
        assert decide_permission("full_auto", tool) == "allow"


def test_decide_plan_blocks_writes_only():
    for tool in ("write_file", "edit_file", "bash"):
        assert decide_permission("plan", tool) == "deny"
    for tool in ("read_file", "glob", "grep", "agent"):
        assert decide_permission("plan", tool) == "allow"


def test_decide_default_asks_for_writes():
    for tool in ("write_file", "edit_file", "bash"):
        assert decide_permission("default", tool) == "ask"
    for tool in ("read_file", "glob", "grep", "agent"):
        assert decide_permission("default", tool) == "allow"


# --- host wiring + modal channel ---


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "javis-workspace"))
    (tmp_path / "javis-workspace").mkdir(exist_ok=True)
    return tmp_path


async def _make_host(isolated_env) -> tuple[_JavisBackendHost, list]:
    bundle = await build_javis_runtime(
        cwd=str(isolated_env),
        engine=FakeEngine(),
        model="test-model",
    )
    host = _JavisBackendHost(
        bundle=bundle,
        config=_BackendHostConfig(cwd=str(isolated_env)),
    )
    events: list = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    return host, events


class _FakeCoreAgent:
    def __init__(self) -> None:
        self.permission_checker = None


class _BackendWithAgent:
    def __init__(self) -> None:
        self.agent = _FakeCoreAgent()


@pytest.mark.asyncio
async def test_inject_wires_permission_checker(isolated_env):
    backend = _BackendWithAgent()
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), engine=backend, model="m"
    )
    host = _JavisBackendHost(bundle=bundle, config=_BackendHostConfig(cwd=str(isolated_env)))
    host._inject_permission_checker()
    assert backend.agent.permission_checker is not None
    assert callable(backend.agent.permission_checker)


@pytest.mark.asyncio
async def test_inject_skips_backend_without_agent(isolated_env):
    host, _ = await _make_host(isolated_env)
    host._inject_permission_checker()  # must not raise for FakeEngine
    assert getattr(host._bundle.engine, "agent", None) is None


@pytest.mark.asyncio
async def test_check_permission_default_asks_then_allows(isolated_env):
    host, events = await _make_host(isolated_env)
    task = asyncio.create_task(host._check_permission("write_file", {"path": "/x"}))
    await asyncio.sleep(0)  # let it emit the modal request
    modal_events = [e for e in events if e.type == "modal_request"]
    assert modal_events, "expected a modal_request for a write tool in default mode"
    rid = modal_events[-1].modal["request_id"]
    assert modal_events[-1].modal["kind"] == "permission"
    assert modal_events[-1].modal["tool_name"] == "write_file"

    host._permission_requests[rid].set_result(True)  # user allows
    assert await task == "allow"


@pytest.mark.asyncio
async def test_check_permission_default_denies_when_user_rejects(isolated_env):
    host, events = await _make_host(isolated_env)
    task = asyncio.create_task(host._check_permission("bash", {"command": "rm -rf /"}))
    await asyncio.sleep(0)
    modal_events = [e for e in events if e.type == "modal_request"]
    assert modal_events
    rid = modal_events[-1].modal["request_id"]

    host._permission_requests[rid].set_result(False)  # user rejects
    assert await task == "deny"


@pytest.mark.asyncio
async def test_check_permission_read_tools_do_not_ask(isolated_env):
    host, events = await _make_host(isolated_env)
    assert await host._check_permission("read_file", {"file_path": "/x"}) == "allow"
    assert not [e for e in events if e.type == "modal_request"]


@pytest.mark.asyncio
async def test_check_permission_plan_denies_without_asking(isolated_env):
    host, _ = await _make_host(isolated_env)
    host._bundle.app_state.set(permission_mode="plan")
    assert await host._check_permission("write_file", {}) == "deny"
    assert await host._check_permission("read_file", {}) == "allow"


@pytest.mark.asyncio
async def test_check_permission_full_auto_allows_without_asking(isolated_env):
    host, events = await _make_host(isolated_env)
    host._bundle.app_state.set(permission_mode="full_auto")
    assert await host._check_permission("write_file", {}) == "allow"
    assert not [e for e in events if e.type == "modal_request"]
