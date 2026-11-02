"""Chapter 5: pydantic-based config validation (schemastery equivalent)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from javis.cordis import Context, FiberState, ValidationError


class DemoConfig(BaseModel):
    greeting: str = "Hello"
    targets: list[str] = Field(default_factory=lambda: ["world"])


async def test_config_defaults_applied():
    ctx = Context()
    seen: dict = {}

    def apply(c, config):
        seen["config"] = config

    apply.Config = DemoConfig
    fiber = ctx.plugin(apply, {"targets": ["alpha", "beta"]})
    await fiber
    assert seen["config"].greeting == "Hello"
    assert seen["config"].targets == ["alpha", "beta"]


async def test_config_validation_error_fails_fiber():
    ctx = Context()
    seen = []

    def apply(c, config):
        seen.append(config)

    apply.Config = DemoConfig
    fiber = ctx.plugin(apply, {"targets": "not-an-array"})
    with pytest.raises(ValidationError) as excinfo:
        await fiber
    assert "invalid config:" in str(excinfo.value)
    assert "targets" in str(excinfo.value)
    assert fiber.state == FiberState.FAILED
    assert seen == []


async def test_apply_receives_validated_model():
    ctx = Context()
    seen: dict = {}

    def apply(c, config: DemoConfig):
        seen["config"] = config

    apply.Config = DemoConfig
    fiber = ctx.plugin(apply, {})  # fully defaulted
    await fiber
    assert seen["config"].greeting == "Hello"
    assert isinstance(seen["config"], DemoConfig)


async def test_update_revalidates_config():
    ctx = Context()
    seen: list = []

    def apply(c, config):
        seen.append(config.targets)

    apply.Config = DemoConfig
    fiber = ctx.plugin(apply, {"targets": ["a"]})
    await fiber
    assert seen == [["a"]]

    async def updated():
        await fiber.update({"targets": ["b"]})

    await updated()
    assert seen == [["a"], ["b"]]
