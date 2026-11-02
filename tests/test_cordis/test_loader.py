"""Chapter 6: composition loading — cordis.yml entries, disabled, group,
isolate, and the loader lifecycle."""

from __future__ import annotations

import asyncio

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader, parse_entries


async def _load(tmp_path, yml_text: str):
    comp = tmp_path / "cordis.yml"
    comp.write_text(yml_text, encoding="utf-8")
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    fiber = ctx.plugin(Loader, {"file": str(comp)})
    await fiber
    # wait for entry fibers to settle
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
    return ctx


async def test_composition_order_independent(tmp_path):
    (tmp_path / "greeter.py").write_text(
        "name = 'greeter'\n"
        "def apply(ctx):\n"
        "    ctx.provide('greeter', object())\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "name = 'consumer'\n"
        "inject = ['greeter']\n"
        "def apply(ctx):\n"
        "    assert ctx.get('greeter') is not None\n"
        "    print('consumer loaded')\n",
        encoding="utf-8",
    )
    # consumer listed first — order must not matter
    ctx = await _load(tmp_path, "- name: './consumer.py'\n- name: './greeter.py'\n")
    assert ctx.get("greeter") is not None


async def test_disabled_entry_not_mounted(tmp_path):
    (tmp_path / "hello.py").write_text(
        "def apply(ctx):\n    print('hello')\n", encoding="utf-8"
    )
    ctx = await _load(
        tmp_path,
        "- id: hello\n  name: './hello.py'\n  disabled: true\n",
    )
    loader = ctx.get("loader")
    assert "hello" in loader.entries()
    assert "hello" not in loader.fibers()


async def test_entry_config_validation(tmp_path):
    from javis.cordis import ValidationError

    (tmp_path / "demo.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        "    greeting: str = 'Hello'\n"
        "def apply(ctx, config):\n"
        "    print(config.greeting)\n",
        encoding="utf-8",
    )
    # The fiber FAILS (and the error is logged); the launcher exits 1 when any
    # fiber is FAILED — mirroring the tutorial's behavior.
    ctx = await _load(
        tmp_path,
        "- name: './demo.py'\n  config:\n    greeting: [1, 2]\n",
    )
    loader = ctx.get("loader")
    fiber = next(iter(loader.fibers().values()))
    assert fiber.state == FiberState.FAILED
    assert isinstance(fiber._error, ValidationError)
    assert "invalid config:" in str(fiber._error)


async def test_group_mounts_as_unit(tmp_path):
    (tmp_path / "a.py").write_text(
        "name = 'a'\ndef apply(ctx):\n    ctx.provide('a', 1)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "name = 'b'\ninject = ['a']\ndef apply(ctx):\n    print('b sees a:', ctx.get('a'))\n",
        encoding="utf-8",
    )
    yml = (
        "- id: group1\n  group:\n"
        "    - name: './a.py'\n"
        "    - name: './b.py'\n"
    )
    ctx = await _load(tmp_path, yml)
    assert ctx.get("a") == 1


async def test_isolate_independent_scopes(tmp_path):
    """Two groups each provide their own `greeter`; neither sees the other."""

    def write_greeter(path: str, text: str):
        (tmp_path / path).write_text(
            "name = 'greeter-provider'\n"
            "def apply(ctx):\n"
            f"    ctx.provide('greeter', {text!r})\n",
            encoding="utf-8",
        )

    write_greeter("g1.py", "one")
    write_greeter("g2.py", "two")
    (tmp_path / "show1.py").write_text(
        "name = 'show1'\ninject = ['greeter']\n"
        "def apply(ctx):\n    print('group1 greeter:', ctx.get('greeter'))\n",
        encoding="utf-8",
    )
    (tmp_path / "show2.py").write_text(
        "name = 'show2'\ninject = ['greeter']\n"
        "def apply(ctx):\n    print('group2 greeter:', ctx.get('greeter'))\n",
        encoding="utf-8",
    )
    yml = (
        "- id: g1\n  isolate: greeter\n  group:\n"
        "    - name: './g1.py'\n"
        "    - name: './show1.py'\n"
        "- id: g2\n  isolate: greeter\n  group:\n"
        "    - name: './g2.py'\n"
        "    - name: './show2.py'\n"
    )
    ctx = await _load(tmp_path, yml)
    # The two isolated scopes each hold their own implementation.
    root_greeter = ctx.get("greeter")  # no provider at root scope
    assert root_greeter is None
    loader = ctx.get("loader")
    # Every group fiber loaded (no PENDING due to cross-group leakage).
    for fiber in loader.fibers().values():
        assert fiber.state == FiberState.ACTIVE


async def test_parse_entries_patch_insert():
    entries = parse_entries({"insert": [{"name": "x", "id": "x1"}]})
    assert len(entries) == 1
    assert entries[0].id == "x1"


async def test_module_plugin_metadata(tmp_path):
    (tmp_path / "meta.py").write_text(
        "name = 'meta-plugin'\n"
        "inject = ['something']\n"
        "def apply(ctx):\n    pass\n",
        encoding="utf-8",
    )
    ctx = await _load(tmp_path, "- name: './meta.py'\n")
    loader = ctx.get("loader")
    entry_id = next(iter(loader.fibers()))
    fiber = loader.fibers()[entry_id]
    assert fiber.runtime.name == "meta-plugin"
    assert fiber.state == FiberState.PENDING  # 'something' is never provided


# ---------------------------------------------------------------------------
# Persistence: loader.write() / update_entry(noSave)
# ---------------------------------------------------------------------------


async def _load_demo(tmp_path, yml: str):
    (tmp_path / "demo.py").write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        "    greeting: str = 'Hello'\n"
        "def apply(ctx, config):\n"
        "    print(config.greeting)\n",
        encoding="utf-8",
    )
    comp = tmp_path / "cordis.yml"
    comp.write_text(yml, encoding="utf-8")
    ctx = Context()
    ctx.baseUrl = str(tmp_path)
    fiber = ctx.plugin(Loader, {"file": str(comp)})
    await fiber
    return ctx, comp


async def test_loader_update_entry_persists(tmp_path):
    ctx, comp = await _load_demo(
        tmp_path, "- id: demo\n  name: './demo.py'\n  config: {greeting: Hello}\n"
    )
    loader = ctx.get("loader")
    result = loader.update_entry("demo", {"greeting": "Hi"})
    if hasattr(result, "__await__"):
        await result
    await asyncio.sleep(0.2)  # write-back settles after the restart
    assert loader.fibers()["demo"].config.greeting == "Hi"
    assert "Hi" in comp.read_text()


async def test_loader_update_entry_nosave(tmp_path):
    ctx, comp = await _load_demo(tmp_path, "- id: demo\n  name: './demo.py'\n")
    loader = ctx.get("loader")
    result = loader.update_entry("demo", {"greeting": "Hi"}, noSave=True)
    if hasattr(result, "__await__"):
        await result
    await asyncio.sleep(0.2)
    # Applied in memory, but not written back to the file.
    assert loader.fibers()["demo"].config.greeting == "Hi"
    assert "Hi" not in comp.read_text()


async def test_update_veto_prevents_persist(tmp_path):
    ctx, comp = await _load_demo(tmp_path, "- id: demo\n  name: './demo.py'\n")
    loader = ctx.get("loader")
    fiber = loader.fibers()["demo"]

    def veto(config, noSave, next):
        return "vetoed"  # not calling next() vetoes the restart

    fiber.ctx.on("internal/update", veto)
    result = loader.update_entry("demo", {"greeting": "No"})
    if hasattr(result, "__await__"):
        await result
    await asyncio.sleep(0.2)
    assert fiber.config.greeting != "No"
    assert "No" not in comp.read_text()


async def test_loader_write_roundtrip(tmp_path):
    ctx, comp = await _load_demo(
        tmp_path, "- id: demo\n  name: './demo.py'\n  config: {greeting: A}\n"
    )
    loader = ctx.get("loader")
    result = loader.update_entry("demo", {"greeting": "B"})
    if hasattr(result, "__await__"):
        await result
    await asyncio.sleep(0.2)

    # A fresh loader reading the written file sees the persisted config.
    import yaml

    from javis.cordis.loader import parse_entries

    data = yaml.safe_load(comp.read_text())
    entries = parse_entries(data)
    assert entries[0].config == {"greeting": "B"}
    assert entries[0].id == "demo"
