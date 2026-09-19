"""External policy listeners for the tools chapter.

Mounted after the always-on handler plugins (``audit.py`` / ``metrics.py``
/ ``truncate.py`` / ``context.py``, and before ``demo.py``), so its
listeners sit inside the handlers and outside the innermost default in
each chain:

- ``tools/pre-execute`` — [audit, permission]: the allowlist veto comes
  last, so audit still records denied attempts.
- ``tools/execute`` — [metrics, retry]: retry wraps the builtin (each
  ``next()`` is one attempt); metrics times all attempts together.

The pipeline in ``tool.py`` never changes — behavior composes from
listeners, which is the whole point of the three-waterfall design.
"""

from __future__ import annotations

name = "tool-policies"

ALLOWED = frozenset({"read", "shell"})  # weather is not allowed


def apply(ctx):
    def permission(payload, next):
        """pre-execute: veto tools outside the allowlist (no ``next()``)."""
        if payload["tool"] not in ALLOWED:
            return {
                "ok": False,
                "tool": payload["tool"],
                "error": f"{payload['tool']!r} is not in the allowlist {sorted(ALLOWED)}",
            }
        return next()

    ctx.on("tools/pre-execute", permission)

    def retry(payload, next):
        """execute: re-invoke the chain on TRANSIENT failure (max 3 attempts)."""
        attempts = 0
        while True:
            attempts += 1
            result = next()
            if result.get("code") != "TRANSIENT" or attempts >= 3:
                result["attempts"] = attempts
                return result

    ctx.on("tools/execute", retry)
