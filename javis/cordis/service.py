"""Base class for services exposed as ``ctx.<name>``.

Port of ``vendor/cordis/src/service.ts``. Subclasses call
``super().__init__(ctx, name)`` from their constructor; the service is
registered immediately (``ctx.reflect.provide``) and automatically removed
with the owning fiber.

Mirrors of the Cordis ``Service`` symbol hooks:

- ``init()`` — instance method run after construction (``Service.init``).
- ``check(value)`` — availability predicate consulted before dependents load
  (``Service.check``).
- ``resolve_config(base, head)`` — merge intercept config from ancestor
  contexts with optional base/head values (``Service.resolveConfig``).
- ``filter(ctx)`` — isolation-scope filter for this service (``Service.filter``).
- ``extend_service(**props)`` — derive a per-context instance copy
  (``Service.extend``).
- Callable services: define ``__call__`` on the subclass (``Service.invoke``);
  the base class stays a plain object, as in Cordis.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import Context


class Service:
    """Base class for services that expose a named API on ``ctx``."""

    #: Static ``provide`` field: default service name when not passed to
    #: ``super().__init__``.
    provide: str | None = None

    def __init__(self, ctx: "Context", name: str | None = None):
        name = name if name is not None else type(self).provide
        if not name:
            raise TypeError(f"{type(self).__name__} requires a service name")
        self.ctx = ctx
        self.name = name
        self.ctx.reflect.provide(ctx, name, self, self.check)

    def init(self) -> Any:
        """Run after construction (class plugins); may return an effect."""
        return None

    def check(self, value: Any) -> bool:
        """Availability predicate consulted before dependents may load."""
        return True

    def filter(self, ctx: "Context") -> bool:
        """Isolation-scope filter: is ``ctx`` in this service's scope?"""
        return ctx._isolate.get(self.name) == self.ctx._isolate.get(self.name)

    def resolve_config(self, base: Any | None = None, head: Any | None = None) -> Any:
        """Merge intercept config from ancestors with optional base and head.

        Entries added closer to the root apply first; ``base`` is prepended and
        ``head`` appended. Uses ``Config.merge`` when the service declares one,
        otherwise a shallow merge (``Object.assign({}, ...configs)`` in Cordis).
        """
        configs = list(self.ctx._intercept.entries(self.name))
        if base is not None:
            configs.insert(0, base)
        if head is not None:
            configs.append(head)
        merge = getattr(getattr(type(self), "Config", None), "merge", None)
        if callable(merge):
            return merge(*configs)
        merged: dict[str, Any] = {}
        for config in configs:
            if isinstance(config, dict):
                merged.update(config)
        return merged

    def extend_service(self, **props: Any) -> "Service":
        """Derive a per-context instance copy with extra properties."""
        instance = copy.copy(self)
        for key, value in props.items():
            setattr(instance, key, value)
        return instance

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
