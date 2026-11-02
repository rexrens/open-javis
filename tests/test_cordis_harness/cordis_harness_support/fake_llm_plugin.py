"""Composition row that registers the scripted adapter for tests.

In process, call :func:`set_script` before mounting. Across a process boundary
(the CLI subprocess test) set ``CDH_FAKE_SCRIPT`` to a JSON list of chunk lists.
"""

from __future__ import annotations

import os

from cordis_harness_support.fake_llm import DEFAULT_STEPS, ScriptedAdapter, steps_from_env

name = "llm-fake"
inject = ["llm"]

#: The scripted steps the next mount will use (empty means "use the env/default").
SCRIPT: list = []

#: The adapter of the last mount, so tests can read the requests it saw.
ADAPTER: ScriptedAdapter | None = None


def set_script(steps: list | None) -> None:
    SCRIPT.clear()
    SCRIPT.extend(steps or [])


def apply(ctx, config=None):
    global ADAPTER
    steps = list(SCRIPT) or steps_from_env(os.environ.get("CDH_FAKE_SCRIPT")) or list(DEFAULT_STEPS)
    ADAPTER = ScriptedAdapter(steps)
    return ctx.get("llm").register_adapter(["fake"], ADAPTER)
