"""javis engines — built-in engine implementations.

The single engine seam is the ``AgentEngine`` contract (javis.contracts);
``javis.engines.harness.HarnessEngine`` is the built-in implementation — a
dsh-style loop (phase state machine, inbox, session event log,
exclusive/parallel tool scheduling) integrated with the real javis system
(real LLM providers, real tools, config, session persistence, permissions).

Engine-internal layout: the loop itself lives in ``javis.dsh`` (shared with
the reference demo), ``javis.llm.providers`` holds the provider
implementations, and ``javis.tools`` the built-in tool registry. Future
engine replacements implement ``AgentEngine`` directly.
"""
