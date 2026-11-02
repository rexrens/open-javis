"""Shared fixtures for the javis test suite."""

from __future__ import annotations

import pytest

from javis.contracts.harness import Harness
from tests.test_javis.fake_backend import FakeEngine


@pytest.fixture
def fake_engine_factory(monkeypatch):
    """Route the ``harness`` composition row's construction to a test double.

    The built-in harness is built by ``javis.harness.plugins.harness``; the
    row loads through the dotted module name, so patching the module-level
    ``build_harness`` swaps the harness for every composition in the suite
    (including the default one).

    Usage::

        engine = fake_engine_factory()                 # plain FakeEngine
        engine = fake_engine_factory(RecordingEngine())  # custom double
        bundle = await build_runtime(cwd=..., ...)
    """

    def _patch(engine: Harness | None = None) -> FakeEngine:
        impl = engine if engine is not None else FakeEngine()
        monkeypatch.setattr(
            "javis.harness.plugins.harness.build_harness", lambda *_a, **_k: impl
        )
        return impl

    return _patch
