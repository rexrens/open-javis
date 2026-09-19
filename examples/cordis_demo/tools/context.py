"""Always-on handler for the tools chapter: attach context.

``tools/post-execute``: enriches the result with a how-the-call-went
summary (tool, attempts, elapsed). Mounted after ``truncate.py`` — the
summary is attached to the full content, then truncate cuts it down:

    [truncate, context]
"""

from __future__ import annotations

name = "tool-context"


def apply(ctx):
    def attach_context(payload, next):
        """post-execute: enrich — summarize how the call went."""
        result = next()
        result["context"] = (
            f"tool={result['tool']} attempts={result.get('attempts', 1)}"
            f" elapsed={result.get('elapsed_ms')}ms"
        )
        return result

    ctx.on("tools/post-execute", attach_context)
