"""Tests for the engine registry."""

from __future__ import annotations

import pytest

from tests.test_javis.fake_backend import FakeBackend
from javis.engines import (
    EngineRegistry,
    create_agent_backend,
    get_engine_config,
    list_engines,
    register_engine,
)


def _dummy_factory(**kwargs):
    return FakeBackend()


def test_register_and_list():
    register_engine("dummy-test", _dummy_factory)
    assert "dummy-test" in list_engines()


def test_create_agent_backend_by_name():
    register_engine("dummy-test-2", _dummy_factory)
    backend = create_agent_backend("dummy-test-2", cwd="/tmp")
    assert isinstance(backend, FakeBackend)


def test_unknown_engine_raises():
    with pytest.raises(ValueError, match="Unknown engine 'nope'"):
        create_agent_backend("nope", cwd="/tmp")


def test_invalid_engine_name_rejected():
    with pytest.raises(ValueError, match="Invalid engine name"):
        register_engine("bad name!", _dummy_factory)


def test_get_engine_config_extracts_subsection():
    config = {"engine": "corecoder", "engines": {"corecoder": {"model": "x"}}}
    assert get_engine_config("corecoder", config) == {"model": "x"}
    assert get_engine_config("unknown", config) == {}



def test_builtin_corecoder_engine_registered():
    assert "corecoder" in list_engines()


def test_engine_registry_register_returns_disposer():
    reg = EngineRegistry()
    cancel = reg.register("ephemeral", _dummy_factory)
    assert reg.get("ephemeral") is _dummy_factory
    assert isinstance(reg.create("ephemeral", cwd="/tmp"), FakeBackend)
    cancel()
    assert reg.get("ephemeral") is None
    cancel()  # idempotent — second dispose is a no-op


def test_engine_registry_unknown_engine_raises():
    reg = EngineRegistry()
    with pytest.raises(ValueError, match="Unknown engine 'nope'"):
        reg.create("nope", cwd="/tmp")
