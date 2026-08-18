"""javis core: agent event model, backend protocol and the engine shell.

This is the protocol layer — the contracts between the javis shell and any
agent backend:

- ``types.py`` — the ``AgentEvent`` stream (text deltas, tool calls, errors…)
- ``protocol.py`` — the ``AgentBackend`` interface (the only seam)
- ``query_engine.py`` — ``QueryEngine``, the reference engine shell that owns
  conversation history and delegates turns to any ``AgentBackend``

Concrete backends (currently corecoder) live in ``javis.engines.*`` and must not
be imported from here — this package stays free of implementation details.
"""

from javis.core.query_engine import QueryEngine
from javis.core.protocol import AgentBackend
from javis.core.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)

__all__ = [
    "AgentBackend",
    "AgentContext",
    "AgentError",
    "AgentEvent",
    "AgentStatus",
    "AgentTextDelta",
    "AgentToolCallResult",
    "AgentToolCallStart",
    "AgentTurnEnd",
    "QueryEngine",
]
