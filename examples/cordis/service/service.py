"""Tutorial chapter 7: ``Service`` subclasses — constructor auto-registration.

The greeter chapter provided a service by calling ``ctx.provide`` from a
hand-rolled class. The idiomatic Cordis way is to subclass
:class:`~javis.cordis.Service`: ``super().__init__(ctx, name)`` registers the
instance immediately (``ctx.reflect.provide``) and removes it again when the
owning fiber unloads — no explicit ``provide`` call, no cleanup code.

A service can also declare:

- ``init()`` — instance hook run after construction (may return an effect);
- ``check(value)`` — availability predicate consulted before dependents load;
- ``filter(ctx)`` — whether a child context belongs to this service's scope
  (used by ``isolate`` — see chapter 8).

API shown: ``Service`` · ``super().__init__(ctx, name)`` · ``ctx.inject`` ·
``ctx.get``.
"""

from __future__ import annotations

from javis.cordis import Service

name = "service-demo"


class CounterService(Service):
    """A per-instance counter exposed as the ``counter`` service."""

    def __init__(self, ctx):
        super().__init__(ctx, "counter")
        self.count = 0

    def bump(self, by: int = 1) -> int:
        self.count += by
        return self.count

    def init(self):
        print("[service] CounterService.init() ran after construction")
        return None  # may return an effect disposer instead  # noqa: RET501, PLR1711


class Consumer:
    """Consumes the counter service; stays PENDING until it is ACTIVE."""

    def __init__(self, ctx):
        print(f"[consumer] counter starts at {ctx.get('counter').count}")

    def tick(self, ctx) -> None:
        for _ in range(3):
            print(f"[consumer] tick → count={ctx.get('counter').bump()}")


def apply(ctx):
    # Constructing the Service registers it under the name "counter".
    ctx.plugin(CounterService)

    # inject waits for "counter" to be ACTIVE, then builds the consumer.
    def on_ready():
        consumer = Consumer(ctx)
        consumer.tick(ctx)

    ctx.inject(["counter"], on_ready)
    print("[service-demo] apply finished — consumer ran via inject")
