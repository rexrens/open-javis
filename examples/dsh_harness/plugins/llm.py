"""Plugin: the model provider (service ``"llm"``).

Provides a :class:`~mock_llm.MockLLM` with a scripted response
sequence. The scenario is picked from the entry config (``cordis.yml``
``config.scenario``) or the ``HARNESS_DEMO_SCENARIO`` environment variable
(``demo/cli.py`` sets it per run). Swapping in a real adapter (OpenAI
compat, DeepSeek, Ollama, …) is a one-file change: implement the
``harness.llm.LLM`` seam and provide it here.
"""

import os as _os

from mock_llm import MockLLM, scenario_script
from pydantic import BaseModel

name = "llm"


class Config(BaseModel):
    #: One of ``text`` / ``tools`` / ``retry`` / ``steer``; ``None`` falls
    #: back to ``$HARNESS_DEMO_SCENARIO`` then ``text``.
    scenario: str | None = None
    model: str = "mock-mini"


def apply(ctx, config):
    scenario = config.scenario or _os.environ.get("HARNESS_DEMO_SCENARIO", "text")
    llm = MockLLM(scenario_script(scenario), model=config.model)
    ctx.provide("llm", llm)
