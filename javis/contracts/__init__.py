"""javis contracts: the stable protocol layer shared by every package.

Only pure definitions live here — no runtime logic, no side effects:

- ``harness.py`` — the ``Harness`` interface (the only seam; a harness
  object owns history + usage and yields ``AgentEvent`` streams)
- ``host.py`` — the ``HostContext`` runtime facts the host injects as the
  ``host`` service (cwd / session_id / tool_metadata / CLI overrides)
- ``services.py`` — stable service names for the plugin system's typed
  service contracts (``tools`` / ``commands`` / ``config`` / ``host`` /
  ``harness``)
- ``tools.py`` — the ``Tool`` interface and ``ToolRegistry`` (the typed
  ``tools`` service)
- ``types.py`` — the ``AgentEvent`` stream (text deltas, tool calls, errors…)
- ``messages.py`` — the ``ConversationMessage`` model and sanitization
- ``usage.py`` — the ``UsageSnapshot`` token/cost record

Everything above (host, session, engines) may depend on this
package; it depends on nothing within javis.
"""

from javis.contracts.harness import Harness
from javis.contracts.host import HostContext
from javis.contracts.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.contracts.services import (
    AGENT_LOOP_SERVICE,
    AGENT_TOOLS_SERVICE,
    COMMANDS_SERVICE,
    CONFIG_SERVICE,
    HARNESS_SERVICE,
    HOST_SERVICE,
    LLM_SERVICE,
    SYSTEM_PROMPT_SERVICE,
    TOOLS_SERVICE,
)
from javis.contracts.tools import Tool, ToolRegistry
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
    "AGENT_LOOP_SERVICE",
    "AGENT_TOOLS_SERVICE",
    "COMMANDS_SERVICE",
    "CONFIG_SERVICE",
    "HARNESS_SERVICE",
    "HOST_SERVICE",
    "LLM_SERVICE",
    "SYSTEM_PROMPT_SERVICE",
    "TOOLS_SERVICE",
    "AgentError",
    "AgentEvent",
    "AgentStatus",
    "AgentTextDelta",
    "AgentToolCallResult",
    "AgentToolCallStart",
    "AgentTurnEnd",
    "ConversationMessage",
    "Harness",
    "HostContext",
    "ImageBlock",
    "TextBlock",
    "Tool",
    "ToolRegistry",
    "ToolResultBlock",
    "UsageSnapshot",
]
