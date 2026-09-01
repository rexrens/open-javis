"""Harness core — the dsh-style agent loop, javis production copy.

Faithful copy of ``examples/dsh_harness`` (the standalone reference demo) with
three javis additions:

- ``session.Session.on_append(seq, type, data)`` — the host engine's event
  bridge hook (the demo copy has no observer).
- ``contracts.AgentLoopConfig.max_steps_per_turn`` (default 20) — the turn
  loop stops after this many tool-call steps, emitting ``agent/limit``
  (dsh has no bound; javis replaces the old ``max_rounds=50`` semantics).
- ``contracts.AgentLoopConfig.history_compressor`` — an optional
  ``(messages) -> messages`` hook applied after ``derive_messages()`` and
  before the next request is built (javis' compression middleware slot).
"""

from . import agent as agent
from . import contracts as contracts
from . import inbox as inbox
from . import llm as llm
from . import session as session
from . import tools as tools

__all__ = ["agent", "contracts", "inbox", "llm", "session", "tools"]
