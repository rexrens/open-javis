"""The Cordis-style plugin engine (port of ``@deepseek-ai/cordis``).

Public surface (mirrors the Cordis API used by deepseek-harness, adapted to an
explicit ``ctx.get()`` design — no proxy magic):

- :class:`Context` — root/child contexts, service store access
  (``get``/``provide``/``set``/``accessor``/``mixin``), ``extend``/
  ``isolate``/``intercept``, events, plugin loading, effects
- :class:`EventsService` — ``on``/``once`` and the five dispatch modes
  (``emit``/``parallel``/``serial``/``bail``/``waterfall``)
- :class:`Service` — base class for named services exposed on ``ctx``
- :class:`Fiber` — one loaded plugin instance (state machine, effects)
- :class:`RegistryService` — ``plugin``/``inject`` with dependency-driven loading
- :mod:`loader` — YAML composition (``cordis.yml``) loading
"""

from .context import Context
from .errors import CordisError, ValidationError
from .events import EventsService
from .fiber import EffectMeta, Fiber, FiberState, resolve_config
from .loader import Loader
from .loader.hmr import Hmr
from .reflect import Impl, ReflectService
from .registry import RegistryService, Runtime, resolve_inject
from .service import Service

__all__ = [
    "Context",
    "CordisError",
    "EffectMeta",
    "EventsService",
    "Fiber",
    "FiberState",
    "Impl",
    "Hmr",
    "Loader",
    "ReflectService",
    "RegistryService",
    "Runtime",
    "Service",
    "ValidationError",
    "resolve_config",
    "resolve_inject",
]
