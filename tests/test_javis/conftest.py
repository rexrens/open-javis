"""Shared fixtures for the javis test suite."""

from __future__ import annotations

import pytest

from javis.contracts.engine import AgentEngine
from tests.test_javis.fake_backend import FakeEngine


@pytest.fixture
def fake_engine_factory(monkeypatch):
    """Route ``build_runtime``'s engine construction to a test double.

    Replaces the old ``engine=FakeEngine()`` injection parameter: the runtime
    no longer accepts an engine, so tests patch the ``_build_default_engine``
    seam instead. Future plugin-provided engines will plug in at the same
    seam (``ctx.provide("engine", impl)``).

    Usage::

        engine = fake_engine_factory()                 # plain FakeEngine
        engine = fake_engine_factory(RecordingEngine())  # custom double
        bundle = await build_runtime(cwd=..., ...)
    """

    def _patch(engine: AgentEngine | None = None) -> FakeEngine:
        impl = engine if engine is not None else FakeEngine()
        monkeypatch.setattr("javis.host.runtime._build_default_engine", lambda **_: impl)
        return impl

    return _patch
