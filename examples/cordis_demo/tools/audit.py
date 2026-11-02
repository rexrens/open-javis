"""Always-on handler for the tools chapter: audit.

Registers the ``tools/pre-execute`` listener that records every attempt.
Mounted right after ``tool.py`` — before ``policies.py`` — so it sits at
the outermost ring of the chain and even denied calls are logged:

    [audit, permission]
"""

from __future__ import annotations

name = "tool-audit"


def apply(ctx):
    def audit(payload, next):
        """pre-execute: record every attempt, then hand over to the chain."""
        print(f"[audit] {payload['tool']} requested args={payload.get('args')!r}")
        return next()

    ctx.on("tools/pre-execute", audit)
