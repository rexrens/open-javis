"""Fiber restart / update and the internal/update waterfall (HMR hooks)."""

from __future__ import annotations

from javis.cordis import Context, FiberState


async def test_restart_runs_apply_again():
    ctx = Context()
    seen = []

    def apply(c):
        seen.append("load")

    fiber = ctx.plugin(apply)
    await fiber
    assert seen == ["load"]
    await fiber.restart()
    assert seen == ["load", "load"]
    assert fiber.state == FiberState.ACTIVE


async def test_update_applies_new_config():
    ctx = Context()
    seen = []

    def apply(c, config):
        seen.append(config["v"] if isinstance(config, dict) else config.v)

    fiber = ctx.plugin(apply, {"v": 1})
    await fiber
    assert seen == [1]
    await fiber.update({"v": 2})
    assert seen == [1, 2]


async def test_update_hook_can_veto():
    ctx = Context()
    seen = []

    def apply(c, config):
        seen.append(config["v"] if isinstance(config, dict) else config.v)

    fiber = ctx.plugin(apply, {"v": 1})
    await fiber
    assert seen == [1]

    def veto(config, noSave, next):
        return "vetoed"  # not calling next() vetoes the restart

    fiber.ctx.on("internal/update", veto)
    # update() returns the waterfall result: a plain value (vetoed) or a
    # coroutine (the default restart); await when awaitable.
    result = fiber.update({"v": 2})
    if hasattr(result, "__await__"):
        result = await result
    assert result == "vetoed"
    assert seen == [1]  # unchanged
    assert fiber.state == FiberState.ACTIVE


async def test_update_hook_can_observe_and_delegate():
    ctx = Context()
    seen = []
    observed = []

    def apply(c, config):
        seen.append(config["v"] if isinstance(config, dict) else config.v)

    fiber = ctx.plugin(apply, {"v": 1})
    await fiber

    def observer(config, noSave, next):
        observed.append(config["v"])
        return next()  # observing hooks must delegate

    fiber.ctx.on("internal/update", observer)
    await fiber.update({"v": 3})
    assert observed == [3]
    assert seen == [1, 3]


async def test_update_hook_removed_with_fiber():
    ctx = Context()
    seen = []
    calls = []

    def apply(c, config):
        seen.append(config["v"] if isinstance(config, dict) else config.v)

    fiber = ctx.plugin(apply, {"v": 1})
    await fiber

    def hook(config, noSave, next):
        calls.append(1)
        return next()

    fiber.ctx.on("internal/update", hook)
    await fiber.update({"v": 2})
    assert calls == [1]
    # Hook dies with its fiber.
    await fiber.dispose()
    assert calls == [1]
