"""Tutorial chapter 11: a DSH-style tool call as three waterfalls.

Mirrors how dsh_harness routes tool calls: one call flows through three
``ctx.waterfall`` chains, each answering one question —

- ``tools/pre-execute`` — *can this tool run?* listeners implement
  permissions / approval / audit; veto (don't call ``next()``) to deny.
- ``tools/execute`` — *how does it run?* listeners wrap the built-in tool
  body (the innermost default): time it, retry transient failures, …
- ``tools/post-execute`` — *how is the result used?* listeners rewrite or
  enrich the result before it reaches the caller.

The core plugin (this file) owns the pipeline — the innermost defaults and
the ``call_tool`` service. Each always-on handler lives in its own plugin
(``audit.py`` / ``metrics.py`` / ``truncate.py`` / ``context.py``),
*external* policies in ``policies.py``, and ``demo.py`` drives three
scenarios. Mount order in ``cordis.yml`` (tool → audit → metrics →
truncate → context → policies → demo) plus the demo's ``inject``
guarantee every listener is registered before any call — the pipeline
code never changes, behavior composes from listeners.

How to read the output:

- ``call_tool('read')`` — allowed; the builtin runs; post-execute truncates
  the long content and attaches a context summary.
- ``call_tool('shell')`` — allowed, but the builtin fails TRANSIENT once;
  the retry policy re-invokes the chain and succeeds (``elapsed_ms`` covers
  both attempts, and ``context`` reports ``attempts=2``).
- ``call_tool('weather')`` — denied by the permission policy at
  pre-execute; execute and post-execute never run, yet audit still logged
  the attempt.

API shown: ``ctx.waterfall`` (three chains, one pattern) · ``ctx.on`` ·
``ctx.provide`` (the pipeline as a service, unregistered on dispose).
"""

from __future__ import annotations

name = "tool-core"


def apply(ctx):
    # -- built-in tool bodies (the innermost execute default) ----------------

    shell_calls = {"n": 0}

    def _run_tool(payload, _next):
        """Innermost default of tools/execute: actually run the tool."""
        tool = payload["tool"]
        if tool == "read":
            return {"ok": True, "tool": tool, "content": "line1\n" + "line2 detail\n" * 12}
        if tool == "shell":
            shell_calls["n"] += 1
            if shell_calls["n"] == 1:
                # deterministic transient failure — the retry policy's target
                return {
                    "ok": False,
                    "tool": tool,
                    "code": "TRANSIENT",
                    "error": "shell busy, try again",
                }
            return {"ok": True, "tool": tool, "output": "command finished"}
        raise RuntimeError(f"no builtin for {tool!r}")

    def _pass(payload, _next):
        """Innermost default of pre/post: pass the payload through."""
        return payload

    # -- the pipeline: three waterfalls, one call -----------------------------

    def call_tool(tool, args=None):
        print(f"\n→ call_tool({tool!r}, {args!r})")
        payload = {"tool": tool, "args": args}
        pre = ctx.waterfall("tools/pre-execute", payload, _pass)
        if not pre.get("ok", True):
            print(f"[result] DENIED at pre-execute: {pre.get('error')}")
            return pre
        result = ctx.waterfall("tools/execute", pre, _run_tool)
        result = ctx.waterfall("tools/post-execute", result, _pass)
        print(f"[result] {result}")
        return result

    # Expose the pipeline as a service (disposed with this fiber).
    ctx.provide("tools", call_tool)
