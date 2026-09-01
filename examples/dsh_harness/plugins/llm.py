"""Plugin: the model provider (service ``"llm"`` = a ``LlmRuntime`` registry).

Composes the dsh-style LLM service the way dsh does: the plugin provides a
:class:`~javis.llm.LlmRuntime` (adapter registry; auto-registers as the
``"llm"`` service) and registers a :class:`~mock_llm.MockAdapter` under the
``"mock"`` provider route. The scenario is picked from the entry config
(``cordis.yml`` ``config.scenario``) or the ``HARNESS_DEMO_SCENARIO``
environment variable (``cli.py`` sets it per run).

Swapping in a real adapter (OpenAI compat, DeepSeek, Ollama, …) is a
one-file change: implement ``javis.llm.LLMAdapter.stream`` and register it
under its provider route. The adapter instance is also exposed as the
``"mock-adapter"`` service so scenario drivers can attach hooks
(``on_tool_call`` / ``on_call``).
"""

import os as _os

from mock_llm import MockAdapter, scenario_script
from pydantic import BaseModel

from javis.llm import LlmRuntime

name = "llm"


class Config(BaseModel):
    #: One of ``text`` / ``tools`` / ``retry`` / ``steer``; ``None`` falls
    #: back to ``$HARNESS_DEMO_SCENARIO`` then ``text``.
    scenario: str | None = None
    model: str = "mock-mini"


def apply(ctx, config):
    scenario = config.scenario or _os.environ.get("HARNESS_DEMO_SCENARIO", "text")
    adapter = MockAdapter(scenario_script(scenario), model=config.model)
    runtime = LlmRuntime(ctx)  # auto-registers the "llm" service
    runtime.register_adapter(["mock"], adapter)
    ctx.provide("mock-adapter", adapter)
