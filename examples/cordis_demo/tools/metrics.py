"""Always-on handler for the tools chapter: metrics.

Times the whole ``tools/execute`` chain, retries included. Mounted before
``policies.py`` so it wraps the retry policy around the builtin:

    [metrics, retry, builtin]
"""

from __future__ import annotations

import time

name = "tool-metrics"


def apply(ctx):
    def metrics(payload, next):
        """execute: time the whole chain, retries included."""
        start = time.perf_counter()
        result = next()
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
        return result

    ctx.on("tools/execute", metrics)
