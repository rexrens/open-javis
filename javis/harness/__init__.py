"""javis.harness — the harness: dsh-style agent loop + javis integration.

Single source shared by the production engine and the reference demo
(``examples/dsh_harness``) — previously split across an architecture layer and
an integration shell; merged into this one package 2026-09-01.

Architecture layer (the dsh port — naming aligned with the dsh reference):
- ``agent.py`` — ``ReactLoopAgent`` phase state machine (idle / maintenance / running)
- ``inbox.py`` — next-turn / next-step inbox with splice logging
- ``session.py`` — session event log + ``derive_messages``
- ``llm.py`` — LLM seam (``prepare_call`` / ``normalized_stream`` / ``BlockAssembler``)
- ``tools.py`` — exclusive/parallel tool scheduling, ``concludes_turn``, abort synthesis
- ``types.py`` — dsh-aligned data contracts (blocks / chunks / finish / events / config)

Three javis additions over the plain dsh port:
- ``session.Session.on_append(seq, type, data)`` — the host engine's event bridge hook
- ``types.AgentLoopConfig.max_steps_per_turn`` (default 20) — turn loop guard,
  emits ``agent/limit`` (replaces the old ``max_rounds=50`` semantics)
- ``types.AgentLoopConfig.history_compressor`` — optional ``(messages) -> messages``
  hook applied after ``derive_messages()`` (the compression middleware slot)

Javis integration shell:
- ``engine.py`` — ``HarnessEngine`` implements ``javis.contracts.AgentEngine``
  (message mirror, usage, session save/restore, permission/request/compression
  middleware wired onto the ``agent/*`` / ``tools/*`` waterfalls); its private
  loop context provides the ``llm`` service as a ``javis.llm.LlmRuntime``
  (adapter registry) — the LLM layer itself lives in ``javis.llm``
- ``build.py`` — engine construction (``javis_tools`` = plugin tools bridge)
- ``tool_adapter.py`` — adapts javis ``Tool`` → dsh ``Tool``
- ``prompt.py`` — prompt assembly (sections + tool schemas)
- ``compression.py`` — history compression middleware (snip + cap)

LLM layer lives in ``javis.llm`` (LlmRuntime adapter registry + adapters),
built-in tools in ``javis.tools`` — host-level services, not engine internals.
"""

from __future__ import annotations

from . import agent as agent
from . import inbox as inbox
from . import llm as llm
from . import session as session
from . import tools as tools
from . import types as types
from .build import build
from .compression import HistoryCompressor, make_snip_listener
from .engine import HarnessEngine
from .tool_adapter import adapt_registry, adapt_tool

__version__ = "0.1.0"

__all__ = [
    "HarnessEngine",
    "HistoryCompressor",
    "__version__",
    "adapt_registry",
    "adapt_tool",
    "agent",
    "build",
    "inbox",
    "llm",
    "make_snip_listener",
    "session",
    "tools",
    "types",
]
