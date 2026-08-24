"""Tests for the plugin loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from javis.plugins.context import EventBus, ServiceRegistry
from javis.plugins.instance import PluginState
from javis.plugins.loader import (
    _load_module,
    discover_plugin_files,
    extract_plugins,
    load_plugins,
    plugin_dirs,
)
from javis.plugins.registry import PluginRegistry

FIXTURES = Path(__file__).parent / "fixtures"


def test_discover_scans_py_files_and_dirs(tmp_path):
    (tmp_path / "a.py").write_text("def apply(ctx, config): pass\n")
    (tmp_path / "bdir").mkdir()
    (tmp_path / "bdir" / "__init__.py").write_text("def apply(ctx, config): pass\n")
    found = [name for _path, name in discover_plugin_files([tmp_path])]
    assert sorted(found) == ["a", "bdir"]


def test_discover_later_dir_wins_on_name_collision(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "dup.py").write_text("def apply(ctx, config): pass\n")
    other = tmp_path / "other"
    other.mkdir()
    (other / "dup.py").write_text("def apply(ctx, config): pass\n")
    # discover_plugin_files yields (path, name) pairs; invert for name -> path.
    found = {name: path for path, name in discover_plugin_files([tmp_path / "p", other])}
    assert Path(found["dup"]) == other / "dup.py"


def test_extract_apply_function():
    module = _load("simple_apply")
    specs = extract_plugins(module, "simple_apply")
    assert len(specs) == 1
    assert specs[0].name == "simple_apply"
    assert specs[0].config_model is None
    assert specs[0].inject == []


def test_extract_declarative():
    module = _load("declarative")
    specs = extract_plugins(module, "declarative")
    assert specs[0].name == "decl-plugin"
    assert specs[0].config_model is not None
    assert specs[0].inject == ["tools"]


def test_extract_object_form():
    module = _load("object_form")
    specs = extract_plugins(module, "object_form")
    assert specs[0].name == "obj-plugin"
    assert specs[0].config_model is not None


def test_extract_multi():
    module = _load("multi")
    specs = extract_plugins(module, "multi")
    assert [s.name for s in specs] == ["multi-a", "multi-b"]


def _load(module_name):
    import importlib.util

    path = FIXTURES / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"_fixture_{module_name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_module_reuses_already_imported_file(tmp_path, monkeypatch):
    """The loader must reuse a module already imported under another name,
    otherwise classes defined in the plugin would exist twice and typed
    ``service.get`` isinstance checks would fail."""
    import importlib.util

    path = tmp_path / "shared.py"
    path.write_text("class Service:\n    pass\n")
    spec = importlib.util.spec_from_file_location("host_imported_shared", path)
    host_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host_mod)
    # Normal imports register the module in sys.modules; mirror that.
    monkeypatch.setitem(sys.modules, "host_imported_shared", host_mod)

    loaded = _load_module(path, "javis_plugin_shared")

    assert loaded is host_mod
    assert loaded.Service is host_mod.Service


@pytest.fixture
def reg():
    services = ServiceRegistry()
    bus = EventBus()

    def _build(name, config):
        from javis.plugins.context import PluginContext

        return PluginContext(
            name=name, config=config, services=services, bus=bus, javis_config=None
        )

    return PluginRegistry(services=services, bus=bus, ctx_builder=_build)


@pytest.mark.asyncio
async def test_load_plugins_injects_config_and_activates(reg):
    # decl-plugin declares inject=["tools"]; provide the service before activating
    reg.services.provide("tools", type("T", (), {"register_tool": lambda self, t: None})())
    reg.services.provide("commands", type("C", (), {"register": lambda self, c: None})())
    reg.services.provide("engines", type("E", (), {"register_engine": lambda self, n, f: None})())

    plugins_cfg = {
        "decl-plugin": {"enabled": True, "config": {"greeting": "from-cfg"}},
        "obj-plugin": {"enabled": True, "config": {"n": 7}},
    }
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    await reg.activate_all()
    assert reg.get("decl-plugin").config.greeting == "from-cfg"
    assert reg.get("obj-plugin").config.n == 7
    assert reg.get("simple_apply").config is None
    assert reg.get("decl-plugin").state is PluginState.ACTIVE  # inject=["tools"] satisfied


@pytest.mark.asyncio
async def test_load_plugins_disabled_skipped(reg):
    plugins_cfg = {"simple_apply": {"enabled": False}}
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    assert reg.get("simple_apply") is None


@pytest.mark.asyncio
async def test_load_plugins_non_dict_config_does_not_crash(reg, caplog):
    # A bare bool (natural enable/disable spelling) and a non-dict `config`
    # must be tolerated, never raising AttributeError out of load_plugins.
    plugins_cfg = {
        "simple_apply": False,
        "decl-plugin": {"enabled": True, "config": "not-a-dict"},
    }
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    # Non-dict entries are treated as empty ({} => enabled, no config).
    assert reg.get("simple_apply") is not None
    assert reg.get("decl-plugin") is not None
    assert "treating as empty" in caplog.text


@pytest.mark.asyncio
async def test_load_plugins_isolates_bad_syntax(reg, caplog):
    plugins_cfg = {}
    await load_plugins(reg, [FIXTURES], plugins_cfg)
    # bad_syntax.py must not exist as a plugin; others still loaded
    assert reg.get("bad_syntax") is None
    assert reg.get("simple_apply") is not None


@pytest.mark.asyncio
async def test_plugin_dirs_global_then_project(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVIS_WORKSPACE", str(tmp_path / "ws"))

    project = tmp_path / "proj" / ".javis"
    project.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "proj")
    dirs = plugin_dirs(cwd=str(tmp_path / "proj"))
    assert len(dirs) >= 1
    assert dirs[0].name == "plugins"
