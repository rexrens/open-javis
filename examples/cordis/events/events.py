"""Tutorial chapter 6: the five event dispatch modes.

One listener set, five delivery modes — the whole point of the Cordis event
bus. Dispatch modes (see ``javis/cordis/events.py`` for the reference table)::

    mode        awaited?   order                   returns?
    emit        no         registration order      no
    parallel    yes        all concurrently        no
    serial      yes        registration order      first bail value
    bail        no         registration order      first bail value
    waterfall   yes        middleware chain        rewritten payload

How to read this chapter's output:

- ``emit`` — three listeners fire in registration order, fire-and-forget.
- ``parallel`` — two *async* listeners start together (``asyncio.gather``);
  their sleeps interleave, total wall time ≈ the longest sleep, not the sum.
- ``serial`` — async listeners run one after another; the chain stops at the
  first bail value (any non-``None``/``False`` return, javis' ``is_bailed``).
- ``bail`` — sync fire-and-forget: the first listener returning a non-None
  value wins and the remaining listeners are skipped.
- ``waterfall`` — listeners receive ``(payload, next)``; calling ``next()``
  continues the chain (finally the built-in default), returning a value
  rewrites the payload, *not* calling ``next()`` vetoes the rest.

API shown: ``ctx.on`` · ``ctx.emit`` · ``ctx.parallel`` · ``ctx.serial`` ·
``ctx.bail`` · ``ctx.waterfall``.
"""

from __future__ import annotations

import asyncio

name = "events-demo"


async def apply(ctx):
    """Async so the parallel/serial demos can await their dispatch."""
    # -- emit: fire-and-forget, registration order ----------------------------
    def emit_a():
        print("[emit] listener A")

    def emit_b():
        print("[emit] listener B")

    ctx.on("demo/emit", emit_a)
    ctx.on("demo/emit", emit_b)
    print("→ ctx.emit('demo/emit')")
    ctx.emit("demo/emit")

    # -- parallel: awaited, all concurrently ----------------------------------
    async def slow_1():
        await asyncio.sleep(0.1)
        print("[parallel] slow listener 1 done")

    async def slow_2():
        await asyncio.sleep(0.1)
        print("[parallel] slow listener 2 done")

    ctx.on("demo/parallel", slow_1)
    ctx.on("demo/parallel", slow_2)
    print("→ await ctx.parallel('demo/parallel')  (two 0.1s listeners)")
    start = asyncio.get_event_loop().time()
    await ctx.parallel("demo/parallel")
    print(f"[parallel] both finished in {asyncio.get_event_loop().time() - start:.2f}s (~0.1s, not 0.2s)")

    # -- serial: awaited, in order, first bail value stops the chain ----------
    async def serial_a():
        print("[serial] listener A")
        return None  # not a bail value — chain continues  # noqa: RET501, PLR1711

    async def serial_b():
        print("[serial] listener B — returns a non-None value → chain stops")
        return "stopped at B"

    async def serial_c():
        print("[serial] listener C — never reached")

    ctx.on("demo/serial", serial_a)
    ctx.on("demo/serial", serial_b)
    ctx.on("demo/serial", serial_c)
    print("→ await ctx.serial('demo/serial')")
    await ctx.serial("demo/serial")

    # -- bail: sync fire-and-forget, first non-None return wins ---------------
    def bail_a():
        print("[bail] listener A — returns None, chain continues")
        return None  # noqa: RET501, PLR1711

    def bail_b():
        print("[bail] listener B — returns 'winner', later listeners skipped")
        return "winner"

    def bail_c():
        print("[bail] listener C — never reached")

    ctx.on("demo/bail", bail_a)
    ctx.on("demo/bail", bail_b)
    ctx.on("demo/bail", bail_c)
    print("→ ctx.bail('demo/bail')")
    result = ctx.bail("demo/bail")
    print(f"[bail] result: {result!r}")

    # -- waterfall: middleware chain around the innermost default -------------
    def wf_add_tag(payload, next):
        print(f"[waterfall] listener 1 sees {payload!r}")
        payload = next()  # call next to continue the chain
        return f"{payload} +tag-from-listener-1"

    def wf_default(payload, _next):
        print(f"[waterfall] default sees {payload!r}")
        return f"{payload} +default"

    ctx.on("demo/waterfall", wf_add_tag)
    print("→ ctx.waterfall('demo/waterfall', 'payload', wf_default)")
    final = ctx.waterfall("demo/waterfall", "payload", wf_default)
    print(f"[waterfall] final: {final!r}")

    # -- veto: a waterfall listener that does NOT call next() -----------------
    def wf_veto(_payload, _next):
        print("[waterfall] listener vetoes — never calls next(), chain stops")

    ctx.on("demo/waterfall-veto", wf_veto)
    print("→ ctx.waterfall('demo/waterfall-veto', 'x', default)")
    vetoed = ctx.waterfall("demo/waterfall-veto", "x", lambda p, n: f"{p}+default")
    print(f"[waterfall] vetoed result: {vetoed!r} (the default never ran)")
