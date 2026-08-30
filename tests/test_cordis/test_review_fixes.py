"""Regression tests for the review findings (claims 1-9, 11)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

from javis.cordis import Context, FiberState, Service, ValidationError
from javis.cordis.loader import Loader, load_module


# --- claim 1: config declared with a default / keyword-only is passed --------


async def test_apply_with_default_config_receives_config():
    ctx = Context()
    seen: dict = {}

    def apply(c, config=None):
        seen["config"] = config

    f = ctx.plugin(apply, {"v": 1})
    await f
    assert seen["config"] == {"v": 1}
    assert f.state == FiberState.ACTIVE


async def test_apply_keyword_only_config():
    ctx = Context()
    seen: dict = {}

    def apply(c, *, config):
        seen["config"] = config

    f = ctx.plugin(apply, {"v": 1})
    await f
    assert seen["config"] == {"v": 1}
    assert f.state == FiberState.ACTIVE


async def test_class_plugin_default_config():
    ctx = Context()
    seen: dict = {}

    class P(Service):
        def __init__(self, c, config=None):
            super().__init__(c, "p")
            seen["config"] = config

    f = ctx.plugin(P, {"v": 1})
    await f
    assert seen["config"] == {"v": 1}


async def test_single_param_apply_still_gets_ctx_only():
    ctx = Context()
    seen: dict = {}

    def apply(c):
        seen["ctx"] = c

    f = ctx.plugin(apply, {"v": 1})
    await f
    assert seen["ctx"] is not None


# --- claim 2: root fiber restart/dispose is safe -----------------------------


async def test_root_fiber_restart_is_safe():
    ctx = Context()
    seen = []
    ctx.on("x", lambda: seen.append(1))
    ctx.emit("x")
    assert seen == [1]

    await ctx.fiber.restart()
    assert ctx.fiber.state == FiberState.ACTIVE
    # Root effects (the listener) were unloaded by the restart.
    ctx.emit("x")
    assert seen == [1]


async def test_root_fiber_dispose_is_safe():
    ctx = Context()
    await ctx.fiber.dispose()
    assert ctx.fiber.state == FiberState.ACTIVE


# --- claim 3: async effect setup is awaited on unload ------------------------


async def test_async_effect_setup_awaited_on_early_dispose():
    ctx = Context()
    cleaned = []

    async def execute():
        await asyncio.sleep(0.1)
        return lambda: cleaned.append("cleanup")

    def apply(c):
        c.effect(execute, "async-effect")

    fiber = ctx.plugin(apply)
    await fiber
    await asyncio.sleep(0.02)  # dispose before the 0.1s setup completes
    await fiber.dispose()
    assert cleaned == ["cleanup"]


# --- claim 4: once works for internal/update ---------------------------------


async def test_once_internal_update_delegating_hook():
    ctx = Context()
    calls = []

    def apply(c, config):
        pass

    f = ctx.plugin(apply, {"v": 1})
    await f
    f.ctx.once("internal/update", lambda *a: (calls.append(1), a[2]())[1])
    await f.update({"v": 2})
    await f.update({"v": 3})
    assert calls == [1]


async def test_once_internal_update_vetoing_hook():
    ctx = Context()
    calls = []

    def apply(c, config):
        pass

    f = ctx.plugin(apply, {"v": 1})
    await f
    f.ctx.once("internal/update", lambda *a: (calls.append(1), "vetoed")[1])
    r1 = f.update({"v": 2})
    if hasattr(r1, "__await__"):
        await r1
    r2 = f.update({"v": 3})
    if hasattr(r2, "__await__"):
        await r2
    assert calls == [1]  # once fired exactly once, then removed


# --- claim 5: accessor setters are reachable via ctx.set ---------------------


async def test_accessor_setter_reachable():
    ctx = Context()
    stored: list = []

    def apply(c):
        c.accessor(
            "computed",
            lambda ctx_: stored[0] if stored else 42,
            lambda ctx_, v: (stored.append(v), True)[1],
        )

    fiber = ctx.plugin(apply)
    await fiber
    # Accessors are scoped to the declaring fiber's context.
    assert fiber.ctx.set("computed", 99) is True
    assert stored == [99]
    assert fiber.ctx.get("computed") == 99
    await fiber.dispose()


async def test_accessor_without_setter_raises():
    ctx = Context()

    def apply(c):
        c.accessor("ro", lambda ctx_: 1)

    fiber = ctx.plugin(apply)
    await fiber
    with pytest.raises(RuntimeError):
        fiber.ctx.set("ro", 2)


async def test_mixin_write_path_works():
    ctx = Context()

    class Holder:
        def __init__(self):
            self.value = 1

    def provider(c):
        holder = Holder()
        c.provide("holder", holder)
        c.mixin("holder", ["value"])

    fiber = ctx.plugin(provider)
    await fiber
    # Mixins are per-context accessors, readable on the declaring context.
    assert fiber.ctx.get("value") == 1
    assert fiber.ctx.set("value", 7) is True
    assert fiber.ctx.get("value") == 7
    await fiber.dispose()


# --- claim 6: failed update does not poison _config --------------------------


async def test_update_validation_failure_does_not_pollute_config():
    from pydantic import BaseModel

    class Cfg(BaseModel):
        v: int

    ctx = Context()
    seen = []

    def apply(c, config: Cfg):
        seen.append(config.v)

    apply.Config = Cfg
    f = ctx.plugin(apply, {"v": 1})
    await f
    with pytest.raises(ValidationError):
        f.update({"v": "bad"})
    assert f._config == {"v": 1}  # untouched
    await f.restart()  # still works with the old config
    assert f.state == FiberState.ACTIVE
    assert seen == [1, 1]


# --- claim 7: anon entries are stable across recompose -----------------------


async def test_anon_entry_stable_across_recompose(tmp_path):
    (tmp_path / "a.py").write_text(
        "name = 'a'\ndef apply(ctx):\n    print('a')\n", encoding="utf-8"
    )
    comp = tmp_path / "cordis.yml"
    comp.write_text("- name: './a.py'\n", encoding="utf-8")
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    await ctx.plugin(Loader, {"file": str(comp)})
    loader = ctx.get("loader")
    entry_id = next(iter(loader.fibers()))
    fiber = loader.fibers()[entry_id]

    await loader.recompose()
    assert next(iter(loader.fibers())) == entry_id
    assert loader.fibers()[entry_id] is fiber  # not remounted


async def test_anon_entry_remounts_when_content_changes(tmp_path):
    (tmp_path / "a.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        "    g: str = 'x'\n"
        "def apply(ctx, config):\n"
        "    print(config.g)\n",
        encoding="utf-8",
    )
    comp = tmp_path / "cordis.yml"
    comp.write_text("- name: './a.py'\n  config: {g: one}\n", encoding="utf-8")
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    await ctx.plugin(Loader, {"file": str(comp)})
    loader = ctx.get("loader")
    old_id = next(iter(loader.fibers()))
    old_fiber = loader.fibers()[old_id]

    comp.write_text("- name: './a.py'\n  config: {g: two}\n", encoding="utf-8")
    await loader.recompose()
    new_id = next(iter(loader.fibers()))
    assert new_id != old_id
    assert loader.fibers()[new_id] is not old_fiber


# --- claim 9: sys.modules stays bounded across reloads -----------------------


def test_sys_modules_bounded_across_reloads():
    d = Path(tempfile.mkdtemp())
    (d / "p.py").write_text("def apply(ctx): pass\n", encoding="utf-8")
    before = len([k for k in sys.modules if k.startswith("_dshlike_plugin_")])
    for _ in range(20):
        load_module("./p.py", str(d))
    after = len([k for k in sys.modules if k.startswith("_dshlike_plugin_")])
    assert after == before + 1  # one entry per path, replaced not leaked


# --- claim 11: parallel aggregation is Exception-only ------------------------


async def test_parallel_exception_group_contains_only_exceptions():
    ctx = Context()

    async def bad():
        raise ValueError("boom")

    ctx.on("x", bad)
    with pytest.raises(ExceptionGroup) as excinfo:
        await ctx.parallel("x")
    assert all(isinstance(e, Exception) for e in excinfo.value.exceptions)


async def test_parallel_custom_base_exception_propagates():
    class CustomBase(BaseException):
        pass

    ctx = Context()

    async def bad():
        raise CustomBase("boom")

    ctx.on("x", bad)
    with pytest.raises(CustomBase):
        await ctx.parallel("x")


# --- review follow-up: reload()/recompose() must fully dispose old fibers ---


async def _svc_composition(tmp_path, version):
    (tmp_path / "svc.py").write_text(
        "name = 'svc'\n"
        "def apply(ctx):\n"
        f"    ctx.provide('svc', {version})\n",
        encoding="utf-8",
    )
    comp = tmp_path / "cordis.yml"
    comp.write_text("- id: svc\n  name: './svc.py'\n", encoding="utf-8")
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    await ctx.plugin(Loader, {"file": str(comp)})
    return ctx, comp


async def test_reload_disposes_old_fiber(tmp_path):
    ctx, comp = await _svc_composition(tmp_path, 1)
    loader = ctx.get("loader")
    old = loader.fibers()["svc"]
    assert ctx.get("svc") == 1

    # A code change: the module now provides a different value.
    (tmp_path / "svc.py").write_text(
        "name = 'svc'\n"
        "def apply(ctx):\n"
        "    ctx.provide('svc', 2)\n",
        encoding="utf-8",
    )
    await loader.reload("svc")

    new = loader.fibers()["svc"]
    assert new is not old
    await new  # wait for the new fiber's load to settle
    assert old.state == FiberState.DISPOSED
    assert old.uid is None
    # The new fiber wins the service (no duplicate-provide failure).
    assert new.state == FiberState.ACTIVE
    assert ctx.get("svc") == 2


async def test_recompose_disposes_old_before_mount(tmp_path):
    ctx, comp = await _svc_composition(tmp_path, 1)
    loader = ctx.get("loader")
    old = loader.fibers()["svc"]

    # Structural change (config is not the only difference): same id, but the
    # entry now carries `inject`, forcing a re-mount.
    comp.write_text(
        "- id: svc\n  name: './svc.py'\n  inject: ['never-satisfied']\n",
        encoding="utf-8",
    )
    await loader.recompose()

    new = loader.fibers().get("svc")
    assert new is not None
    assert new is not old
    assert old.state == FiberState.DISPOSED
    # The new fiber did not race the old provider: it waits for its injected
    # service, so no duplicate-provide failure (it would be PENDING, not FAILED).
    assert new.state in (FiberState.PENDING, FiberState.ACTIVE)
