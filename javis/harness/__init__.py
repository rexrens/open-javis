"""javis.harness — the harness: dsh-style agent loop + javis integration.

Loop core (naming aligned with the dsh reference):
- ``agent.py`` — ``AgentLoop`` phase state machine (idle / maintenance / running)
- ``inbox.py`` — next-turn / next-step inbox with splice logging
- ``session.py`` — session event log + ``derive_messages``
- ``stream.py`` — loop-side stream assembly (``normalized_stream`` /
  ``BlockAssembler``)
- ``tools.py`` — exclusive/parallel tool scheduling, ``concludes_turn``, abort
  synthesis; reads the ``agentTools`` service
- ``types.py`` — dsh-aligned data contracts (blocks / chunks / finish / events /
  config / the ``LLM`` protocol)

Three javis additions over the plain dsh port:
- ``session.Session.on_append(seq, type, data)`` — the harness's event bridge hook
- ``types.AgentLoopConfig.max_steps_per_turn`` (default 20) — turn loop guard,
  emits ``agent/limit`` (replaces the old ``max_rounds=50`` semantics)
- ``types.AgentLoopConfig.history_compressor`` — optional ``(messages) -> messages``
  hook applied after ``derive_messages()`` (the compression middleware slot)

Javis integration shell:
- ``harness.py`` — ``Harness`` implements ``javis.contracts.harness.Harness``
  (message mirror, usage, session save/restore, permission/request/limit
  middleware); every service it drives comes from the root context
- ``plugins/`` — the composition rows that assemble it (``llm`` /
  ``agent_tools`` / ``system_prompt`` / ``agent_loop`` / ``snip`` / ``harness``)
- ``tool_adapter.py`` — adapts javis ``Tool`` → core ``Tool`` + the
  ``AgentToolView`` live view
- ``prompt.py`` — prompt assembly (sections + tool schemas)
- ``compression.py`` — history compression middleware (snip + cap)

LLM providers live in ``javis.llm`` (LlmRuntime adapter registry + adapters),
built-in tools in ``javis.tools`` — host-level services, not harness internals.
"""

from __future__ import annotations

from . import agent as agent
from . import inbox as inbox
from . import plugins as plugins
from . import session as session
from . import stream as stream
from . import tools as tools
from . import types as types
from .compression import HistoryCompressor, make_snip_listener
from .harness import Harness
from .tool_adapter import AgentToolView, adapt_tool

__version__ = "0.1.0"

__all__ = [
    "AgentToolView",
    "Harness",
    "HistoryCompressor",
    "__version__",
    "adapt_tool",
    "agent",
    "inbox",
    "make_snip_listener",
    "plugins",
    "session",
    "stream",
    "tools",
    "types",
]
