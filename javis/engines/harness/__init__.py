"""Harness engine — the javis production engine over the dsh-style agent loop.

Replaces ``javis.engines.corecoder`` (2026-09-01). Architecture: the demo's
``examples/dsh_harness`` reference (dsh ``ReactLoopAgent`` — phase state machine,
inbox, session event log, exclusive/parallel tool scheduling, ``agent/*``
waterfalls) copied into ``javis.engines.harness.core`` with three javis
additions (``Session.on_append`` / ``max_steps_per_turn`` guard /
``history_compressor`` hook), integrated with the real javis system:

- **LLM** — ``JavisLLMAdapter`` bridges ``javis.contracts.llm.LLMProvider``
  (``OpenAICompatProvider`` for DeepSeek/Qwen/Kimi/Ollama, ``ScriptedProvider``
  for offline tests) onto the core's streaming seam.
- **Tools** — the seven built-in tools moved to ``javis.engines.tools`` and
  adapted into the core registry (``tool_adapter``); the runtime passes its
  plugin-populated javis registry in, so plugin tools are included.
- **Host** — ``HarnessEngine`` implements ``AgentEngine``: message mirror,
  usage, session save/restore, ``set_permission_checker`` (tools/execute
  middleware), model routing (agent/request middleware), compression
  (tools/post-execute snip + history cap).
"""

from __future__ import annotations

from .build import build
from .compression import HistoryCompressor, make_snip_listener
from .engine import HarnessEngine
from .llm_adapter import JavisLLMAdapter
from .providers import OpenAICompatProvider, ScriptedProvider
from .tool_adapter import adapt_registry, adapt_tool

__version__ = "0.1.0"

__all__ = [
    "HarnessEngine",
    "HistoryCompressor",
    "JavisLLMAdapter",
    "OpenAICompatProvider",
    "ScriptedProvider",
    "__version__",
    "adapt_registry",
    "adapt_tool",
    "build",
    "make_snip_listener",
]
