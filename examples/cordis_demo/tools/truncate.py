"""Always-on handler for the tools chapter: truncate.

``tools/post-execute``: rewrites long results to a digest. Mounted before
``context.py``, so it cuts the content after the context summary has
already been attached to the full result:

    [truncate, context]
"""

from __future__ import annotations

name = "tool-truncate"


def apply(ctx):
    def truncate(payload, next):
        """post-execute: rewrite — cut long content to a digest."""
        result = next()
        content = result.get("content", "")
        if len(content) > 48:
            result["content"] = content[:45] + "..."
            result["truncated"] = True
        return result

    ctx.on("tools/post-execute", truncate)
