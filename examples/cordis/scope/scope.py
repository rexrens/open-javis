"""Tutorial chapter 8: ``ctx.isolate`` — per-scope service isolation.

Services live in scopes: a child context created with
``ctx.isolate("name")`` gets an *independent* store for that one service —
reads and writes below the child resolve against the new label instead of
the parent's. Passing the **same label** to two ``isolate()`` calls joins
their scopes; ``Service.filter`` decides which contexts belong to a scope.

Use cases: per-user session state, per-workspace configuration, plugin
instances that must not see each other's service.

API shown: ``ctx.isolate`` · ``ctx.extend`` · ``ctx.get``/``ctx.set`` ·
``Service.filter``.
"""

from __future__ import annotations

from javis.cordis import Service

name = "scope-demo"


class SessionService(Service):
    """Per-scope session data, isolated by the ``session`` service name."""

    def __init__(self, ctx, label: str = "session"):
        super().__init__(ctx, "session")
        self.label = label
        self.data: dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        self.data[key] = value

    def filter(self, ctx) -> bool:
        """Only contexts isolated to our scope see this instance.

        The base ``Service.filter`` compares the ctx's isolate label for our
        service name against ours; we keep that behavior.
        """
        return super().filter(ctx)


def apply(ctx):
    # Root scope: one shared session (registering the Service under "session").
    SessionService(ctx)

    # Reads below resolve only once the providing fiber is ACTIVE — do the
    # whole demo inside an inject callback (runs after settle), not inline
    # in apply (strict get would return None for our own un-ACTIVE fiber).
    ctx.inject(["session"], lambda: _demo(ctx))


def _demo(ctx):
    root_session = ctx.get("session")
    root_session.put("user", "alice")

    # Two isolated child scopes: each gets its own session instance.
    alice_ctx = ctx.isolate("session")
    bob_ctx = ctx.isolate("session")

    # Writing below the isolate does not touch the root scope.
    alice_ctx.set("session", SessionService(alice_ctx, label="alice"))
    bob_ctx.set("session", SessionService(bob_ctx, label="bob"))
    root_session.put("root", "value")

    print(f"[scope] root session data: {ctx.get('session').data!r}")
    print(f"[scope] alice session data: {alice_ctx.get('session').data!r}")
    print(f"[scope] bob session data:   {bob_ctx.get('session').data!r}")

    # isolate with the same label joins scopes: same instance seen twice.
    shared_a = ctx.isolate("session", label="shared")
    shared_b = ctx.isolate("session", label="shared")
    shared_a.set("session", SessionService(shared_a, label="shared"))
    print(
        f"[scope] same label → same instance: "
        f"{shared_a.get('session') is shared_b.get('session')}"
    )
