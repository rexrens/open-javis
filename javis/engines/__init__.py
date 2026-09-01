"""javis engines — built-in engine implementations.

The single engine seam is the ``AgentEngine`` contract (javis.contracts);
``javis.engines.harness.HarnessEngine`` is the built-in implementation — a
dsh-style loop (phase state machine, inbox, session event log,
exclusive/parallel tool scheduling) integrated with the real javis system
(real LLM providers, real tools, config, session persistence, permissions).
Future engine replacements implement ``AgentEngine`` directly.
"""
