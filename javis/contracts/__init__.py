"""javis contracts: the stable protocol layer shared by every package.

Only pure definitions live here — no runtime logic, no side effects:

- ``protocol.py`` — the ``AgentBackend`` interface (the only seam)
- ``types.py`` — the ``AgentEvent`` stream (text deltas, tool calls, errors…)
- ``messages.py`` — the ``ConversationMessage`` model and sanitization
- ``usage.py`` — the ``UsageSnapshot`` token/cost record

Everything above (host, session, engines, corecoder) may depend on this
package; it depends on nothing within javis.
"""

from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.protocol import AgentBackend
from javis.contracts.types import (
    AgentContext,
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
    "AgentBackend",
    "AgentContext",
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
