"""javis contracts: the stable protocol layer shared by every package.

Only pure definitions live here — no runtime logic, no side effects:

- ``engine.py`` — the ``AgentEngine`` interface (the only seam; an engine
  object owns history + usage and yields ``AgentEvent`` streams)
- ``types.py`` — the ``AgentEvent`` stream (text deltas, tool calls, errors…)
- ``messages.py`` — the ``ConversationMessage`` model and sanitization
- ``usage.py`` — the ``UsageSnapshot`` token/cost record

Everything above (host, session, engines, corecoder) may depend on this
package; it depends on nothing within javis.
"""

from javis.contracts.engine import AgentEngine
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentStatus,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.contracts.usage import UsageSnapshot

__all__ = [
    "AgentEngine",
    "AgentError",
    "AgentEvent",
    "AgentStatus",
    "AgentTextDelta",
    "AgentToolCallResult",
    "AgentToolCallStart",
    "AgentTurnEnd",
    "ConversationMessage",
    "ImageBlock",
    "TextBlock",
    "ToolResultBlock",
    "UsageSnapshot",
]
