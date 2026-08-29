"""HMR (module/composition watching) and diagnostics."""

from __future__ import annotations

import asyncio
import os

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader


async def _load_with_hmr(tmp_path, yml_text: str):
    comp = tmp_path / "cordis.yml"
    (tmp_path / "hmr.py").write_text(
        "from javis.cordis.loader.hmr import Hmr\n"
        "Config = Hmr.Config\n"
        "apply = Hmr\n",
        encoding="utf-8",
    )
    # Append the HMR entry (fast interval for tests) to the composition.
    comp.write_text(
        yml_text + "- id: hmr\n  name: './hmr.py'\n  config:\n    interval: 0.1\n",
        encoding="utf-8",
    )
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    fiber = ctx.plugin(Loader, {"file": str(comp)})
    await fiber
    # wait for settle
    for _ in range(50):
        in_flight = [
            f.inertia for r in ctx.registry.values() for f in list(r.fibers) if f.inertia
        ]
        if not in_flight:
            break
        await asyncio.gather(*in_flight, return_exceptions=True)
        await asyncio.sleep(0)
    else:
        raise AssertionError("loader did not settle")
    await asyncio.sleep(0.3)  # let the HMR watcher take its first snapshot
    return ctx


async def test_hmr_reloads_changed_module(tmp_path):
    plugin_file = tmp_path / "hello.py"
    plugin_file.write_text(
        "name = 'hello'\ndef apply(ctx):\n    print('hello v1')\n",
        encoding="utf-8",
    )
    ctx = await _load_with_hmr(tmp_path, "- id: hello\n  name: './hello.py'\n")
    loader = ctx.get("loader")
    fiber = loader.fibers()["hello"]
    assert fiber.state == FiberState.ACTIVE

    # Rewrite the module and let the watcher notice.
    plugin_file.write_text(
        "name = 'hello'\ndef apply(ctx):\n    print('hello v2')\n",
        encoding="utf-8",
    )
    # Force the mtime to change (some filesystems have coarse timestamps).
    stamp = os.path.getmtime(plugin_file) + 2
    os.utime(plugin_file, (stamp, stamp))
    await asyncio.sleep(1.0)

    new_fiber = loader.fibers().get("hello")
    assert new_fiber is not None
    assert new_fiber is not fiber  # a fresh fiber was mounted
    assert new_fiber.state == FiberState.ACTIVE
    # fresh module object: new apply function
    assert new_fiber.runtime.callback is not fiber.runtime.callback


async def test_hmr_recomposes_on_composition_change(tmp_path):
    (tmp_path / "a.py").write_text(
        "name = 'a'\ndef apply(ctx):\n    print('a')\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text(
        "name = 'b'\ndef apply(ctx):\n    print('b')\n", encoding="utf-8"
    )
    comp = tmp_path / "cordis.yml"
    hmr_entry = "- id: hmr\n  name: './hmr.py'\n  config:\n    interval: 0.1\n"
    comp.write_text("- id: a\n  name: './a.py'\n" + hmr_entry, encoding="utf-8")
    ctx = await _load_with_hmr(tmp_path, "- id: a\n  name: './a.py'\n")
    loader = ctx.get("loader")
    assert "a" in loader.fibers()
    assert "hmr" in loader.fibers()

    # Replace a with b in the composition.
    comp.write_text("- id: b\n  name: './b.py'\n" + hmr_entry, encoding="utf-8")
    stamp = os.path.getmtime(comp) + 2
    os.utime(comp, (stamp, stamp))
    await asyncio.sleep(1.6)

    assert "a" not in loader.fibers()
    assert "b" in loader.fibers()
    assert "hmr" in loader.fibers()
    assert loader.fibers()["b"].state == FiberState.ACTIVE


async def test_hmr_config_only_change_updates_in_place(tmp_path):
    """A config-only composition change goes through `fiber.update` (the same
    fiber survives and the internal/update waterfall may veto it)."""
    (tmp_path / "demo.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        "    greeting: str = 'Hello'\n"
        "def apply(ctx, config):\n"
        "    print(f'apply {config.greeting}')\n",
        encoding="utf-8",
    )
    comp = tmp_path / "cordis.yml"
    ctx = await _load_with_hmr(
        tmp_path, "- id: demo\n  name: './demo.py'\n  config: {greeting: Hello}\n"
    )
    loader = ctx.get("loader")
    fiber = loader.fibers()["demo"]
    assert fiber.state == FiberState.ACTIVE
    assert fiber.config.greeting == "Hello"

    hmr_entry = "- id: hmr\n  name: './hmr.py'\n  config:\n    interval: 0.1\n"
    comp.write_text(
        "- id: demo\n  name: './demo.py'\n  config: {greeting: Hi}\n" + hmr_entry,
        encoding="utf-8",
    )
    stamp = os.path.getmtime(comp) + 2
    os.utime(comp, (stamp, stamp))
    await asyncio.sleep(1.6)

    new_fiber = loader.fibers()["demo"]
    assert new_fiber is fiber  # NOT re-mounted — updated in place
    assert new_fiber.config.greeting == "Hi"


async def test_hmr_debounce_coalesces_rapid_rewrites(tmp_path):
    calls = tmp_path / "calls.txt"
    plugin_file = tmp_path / "hello.py"

    def write(tag):
        plugin_file.write_text(
            "name = 'hello'\n"
            "def apply(ctx):\n"
            f"    with open({str(calls)!r}, 'a') as fh:\n"
            f"        fh.write({tag!r} + '\\n')\n",
            encoding="utf-8",
        )
        stamp = os.path.getmtime(plugin_file) + 2
        os.utime(plugin_file, (stamp, stamp))

    write("v1")
    await _load_with_hmr(tmp_path, "- id: hello\n  name: './hello.py'\n")
    # Two rapid rewrites inside the debounce window: only the final code
    # should be applied (one coalesced reload, not two).
    write("v2")
    await asyncio.sleep(0.05)
    write("v3")
    await asyncio.sleep(1.6)

    lines = calls.read_text().splitlines()
    assert lines == ["v1", "v3"]


async def test_registry_diagnostics_pending():
    ctx = Context()

    def needs(c):
        pass

    needs.inject = ["missing-service"]
    fiber = ctx.plugin(needs)
    await asyncio.sleep(0.05)
    assert fiber.state == FiberState.PENDING
    assert fiber in ctx.registry.pending()
    assert any(f is fiber for f in ctx.registry.fibers())


async def test_diagnose_example(tmp_path):
    """The tutorial-06 diagnose pattern finds PENDING fibers."""
    ctx = Context()

    def needs(c):
        pass

    needs.inject = ["timer"]
    fiber = ctx.plugin(needs)
    await asyncio.sleep(0.05)
    assert fiber.state == FiberState.PENDING

    found = []
    for runtime in ctx.registry.values():
        for f in runtime.fibers:
            if f.state == FiberState.PENDING:
                found.append(f"{f.name} is PENDING")
    assert found == ["needs is PENDING"]
