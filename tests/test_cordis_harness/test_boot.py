"""Composition loading: dependency order, disabled rows and module failures."""

from __future__ import annotations

from harness.boot import PACKAGED_CONFIG, collect_fibers, failed_fibers, resolve_config_path


async def test_packaged_composition_loads_with_a_fake_provider(mount):
    ctx, _ = await mount()
    assert failed_fibers(ctx) == []
    assert ctx.get("llm").list_providers() == ["fake"]
    assert [schema.name for schema in ctx.get("tools").schemas()] == ["bash", "read_file", "write_file"]
    assert ctx.get("sessions") is not None
    assert ctx.get("agents").provider == "fake"
    assert ctx.get("systemPrompt").section_names() == ["identity", "environment"]
    # Every fiber declared in the composition loaded dependencies first.
    assert len(collect_fibers(ctx)) >= 7


async def test_dependency_order_is_not_positional(mount):
    """`agent` loads after the services it injects, wherever the rows sit."""
    ctx, _ = await mount()
    agents = ctx.get("agents")
    agent = agents.create(ctx.get("sessions").create(".", "fake", "fake-model"))
    # The agent can only read its dependencies once they are ACTIVE.
    request = agent._request()
    assert request.provider == "fake"
    assert request.tools and request.messages[0]["role"] == "system"


async def test_disabled_row_is_not_mounted(mount):
    ctx, _ = await mount(disabled=("tools-builtin",))
    assert failed_fibers(ctx) == []
    assert ctx.get("tools").names() == []


async def test_unknown_module_fails_a_fiber(mount):
    ctx, _ = await mount(extra_rows=[{"id": "nope", "name": "missing_plugin_module"}])
    failed = failed_fibers(ctx)
    assert failed, "a composition row with an unknown module must not load silently"
    assert "cannot load module" in str(failed[0].error)


def test_config_path_resolution(tmp_path, monkeypatch):
    assert resolve_config_path() == PACKAGED_CONFIG
    local = tmp_path / "cordis.yml"
    local.write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_config_path() == local.resolve()
    explicit = tmp_path / "custom.yml"
    assert resolve_config_path(str(explicit)) == explicit.resolve()


def test_packaged_config_is_shipped_beside_the_plugins():
    assert PACKAGED_CONFIG.is_file()
    assert PACKAGED_CONFIG.read_text(encoding="utf-8").count("harness.plugins.") >= 7
