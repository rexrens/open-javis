"""Web UI backend: a dsh-compatible browser host driven by the javis harness.

The browser client is DeepSeek Harness' own web shell. This package serves its
prebuilt assets, injects the boot manifest, and answers the Remote surface the
client calls, mapping javis ``AgentEvent`` streams onto dsh Session events.
"""

from __future__ import annotations

__all__ = ["assets", "auth", "dsh_events", "index", "protocol", "registry", "server"]
