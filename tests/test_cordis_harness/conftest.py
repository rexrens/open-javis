"""Shared fixtures: a fake-provider composition mounted on a real context."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

#: This test directory (for ``support``) and the example under test (for ``harness``).
TEST_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "cordis_harness"
for entry in (str(EXAMPLE_DIR), str(TEST_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from harness import options  # noqa: E402
from harness.boot import load_composition  # noqa: E402
from cordis_harness_support import fake_llm_plugin  # noqa: E402

#: Compositions name the scripted adapter as a dotted module: the loader then
#: reuses the module object these tests configured via ``set_script`` (a file
#: specifier would be recompiled into a fresh module with no script).
FAKE_PLUGIN = "cordis_harness_support.fake_llm_plugin"


@pytest.fixture(autouse=True)
def clean_launch_options():
    """Launch options are process-wide: keep them out of unrelated tests."""
    options.LAUNCH.clear()
    fake_llm_plugin.set_script(None)
    yield
    options.LAUNCH.clear()
    fake_llm_plugin.set_script(None)


@pytest.fixture
def make_composition(tmp_path):
    """Build a ``cordis.yml`` mounting the harness with a scripted provider."""

    def build(
        *,
        disabled: tuple[str, ...] = (),
        extra_rows: list[dict] | None = None,
        cli: bool = False,
        builtin_tools: bool = True,
        model: str = "fake-model",
        max_steps: int = 4,
        session_dir: Path | None = None,
        name: str = "cordis.yml",
    ) -> Path:
        rows: list[dict] = [
            {"id": "llm", "name": "harness.plugins.llm"},
            {"id": "llm-fake", "name": FAKE_PLUGIN},
            {
                "id": "sessions",
                "name": "harness.plugins.session",
                "config": {"directory": str(session_dir or tmp_path / "sessions")},
            },
            {"id": "tools", "name": "harness.plugins.tools"},
        ]
        if builtin_tools:
            rows.append({"id": "tools-builtin", "name": "harness.plugins.tools_builtin"})
        rows.append({"id": "system-prompt", "name": "harness.plugins.system_prompt"})
        rows.append(
            {
                "id": "agent",
                "name": "harness.plugins.agent",
                "config": {"provider": "fake", "model": model, "maxSteps": max_steps},
            }
        )
        if cli:
            rows.append({"id": "cli", "name": "harness.plugins.cli"})
        if extra_rows:
            rows.extend(extra_rows)
        for row in rows:
            if row["id"] in disabled:
                row["disabled"] = True

        path = tmp_path / name
        path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
        return path

    return build


@pytest.fixture
async def mount(make_composition):
    """Mount a built composition and return ``(ctx, loader_fiber)``.

    Agents own background tasks, so the fixture disposes them after each test
    instead of letting loops leak into the next one.
    """
    contexts: list = []

    async def _mount(**kwargs):
        ctx, fiber = await load_composition(make_composition(**kwargs))
        contexts.append(ctx)
        return ctx, fiber

    yield _mount
    for ctx in contexts:
        agents = ctx.get("agents")
        if agents is not None:
            await agents.aclose()
