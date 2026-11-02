"""The developer workflow: add a plugin to ``cdh`` from your own project.

The reference project lives in ``tests/test_cordis_harness/fixtures/dev-project``: a
``cordis.patch.yml`` next to the plugin files it mounts. These tests prove the
layers merge, that a patched row wins, and that the result actually runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from javis.cordis import Context

from harness import composition
from harness.boot import PACKAGED_CONFIG, failed_fibers, layer_patches, load_layers

TEST_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "cordis_harness"
DEV_PROJECT = TEST_DIR / "fixtures" / "dev-project"


@pytest.fixture
async def layered(tmp_path):
    """Mount the packaged composition with a patch layer over it."""
    contexts: list = []

    async def _mount(project: Path):
        ctx = Context()
        patches = layer_patches(home=tmp_path / "home", project=project)
        fiber, applied = await load_layers(
            ctx,
            PACKAGED_CONFIG,
            patches,
            composed_path=tmp_path / "home" / "composed.yml",
        )
        contexts.append(ctx)
        return ctx, fiber, applied

    yield _mount
    for ctx in contexts:
        agents = ctx.get("agents")
        if agents is not None:
            await agents.aclose()


async def test_a_project_patch_adds_plugins_to_the_packaged_composition(layered):
    ctx, _, applied = await layered(DEV_PROJECT)
    assert [path.name for path in applied] == [composition.PATCH_NAME]

    # The appended row's plugin is live...
    assert "clock" in ctx.get("tools").names()
    assert "time-policy" in ctx.get("systemPrompt").section_names()
    assert ctx.get("llm").list_providers() == ["openai", "echo"]
    # ...and the patched row replaced the packaged config wholesale.
    assert ctx.get("agents").provider == "echo"
    assert ctx.get("agents").max_steps == 4


async def test_the_layers_are_written_to_the_harness_home(layered, tmp_path):
    await layered(DEV_PROJECT)
    generated = tmp_path / "home" / "composed.yml"
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert "# patch:" in text and str(DEV_PROJECT / composition.PATCH_NAME) in text
    rows = composition.read_rows(generated)
    assert [row["id"] for row in rows][-1] == "echo-provider"  # appended, not replaced
    assert all(
        row["name"].startswith(str(DEV_PROJECT)) or not row["name"].startswith("/")
        for row in rows
        if row["id"] in ("clock", "echo-provider")
    )


async def test_project_layer_wins_over_the_home_layer(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (home / composition.PATCH_NAME).write_text(
        yaml.safe_dump([{"id": "agent", "config": {"provider": "home", "model": "home-model"}}]),
        encoding="utf-8",
    )
    (project / composition.PATCH_NAME).write_text(
        yaml.safe_dump([{"id": "agent", "config": {"provider": "project", "model": "project-model"}}]),
        encoding="utf-8",
    )

    ctx = Context()
    patches = layer_patches(home=home, project=project)
    fiber, applied = await load_layers(ctx, PACKAGED_CONFIG, patches, composed_path=home / "composed.yml")
    assert [path.parent for path in applied] == [home, project]
    assert ctx.get("agents").provider == "project"
    assert ctx.get("agents").model == "project-model"
    await ctx.get("agents").aclose()


async def test_no_patch_layers_mounts_the_base_directly(tmp_path):
    ctx = Context()
    fiber, applied = await load_layers(ctx, PACKAGED_CONFIG, [], composed_path=tmp_path / "composed.yml")
    assert applied == []
    assert not (tmp_path / "composed.yml").exists()  # nothing generated without patches
    await ctx.get("agents").aclose()


def run_cdh(args: list[str], cwd: Path, stdin: str = "", home: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    # The example provides ``harness``; this test dir provides the scripted adapter.
    env["PYTHONPATH"] = os.pathsep.join([str(EXAMPLE_DIR), str(TEST_DIR)])
    if home is not None:
        env["HOME"] = str(home)  # keep the developer's real harness home out of it
    return subprocess.run(
        [sys.executable, "-m", "harness", *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_dump_config_shows_every_layer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_cdh(["--dump-config", "--home", str(home)], cwd=DEV_PROJECT, home=home)
    assert result.returncode == 0, result.stderr
    assert "# cdh composition" in result.stdout
    assert f"# base:    {PACKAGED_CONFIG}" in result.stdout
    assert f"# patch:   {DEV_PROJECT / composition.PATCH_NAME}" in result.stdout
    assert "- id: clock" in result.stdout
    # The merged rows carry absolute plugin paths, resolved per layer.
    assert str(DEV_PROJECT / "plugins" / "clock.py") in result.stdout


def test_a_patched_cdh_runs_a_turn_offline(tmp_path):
    """The developer's plugin is not just loaded: their project drives a full turn."""
    home = tmp_path / "home"
    sessions = tmp_path / "sessions"
    home.mkdir()
    result = run_cdh(
        ["--home", str(home), "--session-dir", str(sessions), "--yolo"],
        cwd=DEV_PROJECT,
        stdin="你好\n/tools\n/exit\n",
        home=home,
    )
    assert result.returncode == 0, result.stderr
    assert "[dev] 你好" in result.stdout  # the project's echo adapter answered
    assert "clock (no approval)" in result.stdout  # the project's tool is registered
    assert len(list(sessions.glob("*.jsonl"))) == 1


def test_a_missing_patch_file_fails_loudly(tmp_path):
    result = run_cdh(["--patch", str(tmp_path / "nope.yml")], cwd=tmp_path)
    assert result.returncode == 1
    assert "patch file not found" in result.stderr


def test_no_patches_ignores_the_project_layer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = run_cdh(["--dump-config", "--no-patches", "--home", str(home)], cwd=DEV_PROJECT, home=home)
    assert result.returncode == 0, result.stderr
    assert "- id: clock" not in result.stdout
    assert "# patch:" not in result.stdout


async def test_an_engine_service_class_is_mounted_through_a_wrapper_module(tmp_path):
    """Rows point at modules exporting ``apply``; a Service subclass is wrapped.

    This is how a developer turns an engine-provided class (the HMR watcher
    shipping in ``cordis/loader/hmr.py``) into a composition row.
    """
    plugin = tmp_path / "hmr_plugin.py"
    plugin.write_text(
        "from javis.cordis.loader.hmr import Hmr\n"
        "apply = Hmr\n"
        "inject = ['loader']\n"
        "Config = Hmr.Config\n",
        encoding="utf-8",
    )
    rows = [
        {"id": "llm", "name": "harness.plugins.llm"},
        {"id": "sessions", "name": "harness.plugins.session", "config": {"directory": str(tmp_path / "s")}},
        {"id": "tools", "name": "harness.plugins.tools"},
        {"id": "system-prompt", "name": "harness.plugins.system_prompt"},
        {"id": "agent", "name": "harness.plugins.agent", "config": {"provider": "fake", "model": "m"}},
        {"id": "hmr", "name": "./hmr_plugin.py", "config": {"root": ["."], "interval": 0.2}},
    ]
    path = tmp_path / "cordis.yml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")

    ctx = Context()
    fiber, _ = await load_layers(ctx, path, [], composed_path=tmp_path / "composed.yml")
    assert failed_fibers(ctx) == []
    assert ctx.get("hmr") is not None
    assert ctx.get("loader") is not None
    await fiber.dispose()  # unload the watcher instead of leaking its task
