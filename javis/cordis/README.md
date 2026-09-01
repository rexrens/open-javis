dsh-like

A Python re-implementation of the [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
architecture, starting with its plugin engine: a faithful port of
**Cordis** (the `@deepseek-ai/cordis` framework that drives dsh) to Python 3.12.

The engine lives in `src/dshlike/cordis/` (mirroring the harness's vendored
`vendor/cordis`), so a future `dshlike.harness` layer (llm, sessions, agents,
tools, model routing…) can be built directly on top of it.

## Features

- **Context** — root/child dependency containers; `extend` (inherits parent
  attributes) / `isolate` / `intercept` scoping; explicit service access
  (`ctx.get` / `ctx.provide` / `ctx.set` / `ctx.accessor` / `ctx.mixin`).
  `ctx.set(name, value)` routes through an accessor's `set` hook when the
  name is declared as an accessor (the only write path without a proxy).
- **Internal events** — `internal/plugin`, `internal/status`,
  `internal/config`, `internal/update`, `internal/listener`,
  `internal/service`, `internal/dispatch`. Only `internal/service` is
  scope-filtered (as in Cordis); **public events are global** — listeners see
  every emit regardless of isolation scope (also as in Cordis).
- **Dependency-driven loading** — a plugin stays `PENDING` until every
  service in its `inject` list is provided by an ACTIVE fiber; loading order
  is decided by the dependency graph, not by mount order; when a provider
  disappears its dependents unload, and reload when it comes back.
- **Fiber state machine** — `PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED`
  (with `FAILED`), effects with reverse-order/async disposers, `dispose` /
  `restart` / `update(config)`.
- **Five event dispatch modes** — `emit`, `parallel`, `serial`, `bail`,
  `waterfall` (middleware chain with veto semantics).
- **pydantic config validation** — plugins declare a `Config` pydantic model;
  defaults are applied, invalid config fails the fiber with a Cordis-style
  `ValidationError`.
- **Composition loader** — `cordis.yml` entries (`id`/`name`/`config`/
  `disabled`/`inject`/`provide`/`group`/`isolate`); `dshlike run` CLI.
- **HMR** — a polling watcher (debounced) distinguishes **config changes**
  (``fiber.update`` through the ``internal/update`` waterfall, vetoable) from
  **code changes** (dispose + fresh re-mount), and re-composes a changed
  ``cordis.yml`` by entry id.
- **Loader persistence** — ``loader.update_entry(id, config, noSave=False)``
  applies config through the waterfall and writes it back to the composition
  file (``loader.write()``); ``noSave=True`` (and vetoed updates) skip the
  write-back.

## Quick start

```sh
uv sync
uv run pytest          # 85 tests
uv run dshlike run examples/hello/cordis.yml
uv run dshlike run examples/greeter/cordis.yml
uv run dshlike run --wait examples/greeter/cordis.yml   # stay until Ctrl-C
```

## The five core concepts (Cordis primer)

1. **A plugin is an object that implements services.** A function
   `apply(ctx, config)` (with optional `name`/`inject`/`Config` attributes),
   an object with an `apply` method, or a `Service` subclass.
2. **A context is the container of services.** Services occupy stable names
   (`ctx.get('greeter')`); consumers depend on the name, never the provider.
3. **`inject` declares service dependencies.** Plugins wait for all required
   services before loading.
4. **Typed events for communication.** Five dispatch modes, one per event.
5. **Registration is reversible.** Everything registered through
   `ctx.effect()` / `ctx.on()` / `ctx.provide()` / `ctx.plugin()` is torn down
   automatically when its fiber unloads.

## Plugin signatures

Plugin bodies receive the config whenever they declare a config parameter —
positional with or without a default, or keyword-only — and only `ctx`
otherwise (mirroring JavaScript, where the config is always passed and
un-declared arguments are ignored):

```python
def apply(ctx):            # ctx only
def apply(ctx, config):    # (ctx, config)
def apply(ctx, config=None):  # (ctx, config) — defaulted config IS passed
def apply(ctx, *, config):    # ctx, config=config
```

## Example

```python
from dshlike.cordis import Context

ctx = Context()

def greeter(c):
    c.provide('greeter', Greeter())     # register the service
    print('greeter loaded')

def consumer(c):
    print(c.get('greeter').greet('world'))

consumer.inject = ['greeter']           # loads only after greeter is ACTIVE

f1 = ctx.plugin(consumer)               # order does not matter —
f2 = ctx.plugin(greeter)                # the dependency graph decides
await f1
await f2                                # -> greeter loaded / Hello, world!
```

## Composition (`cordis.yml`)

```yaml
- id: greeter
  name: './greeter.py'
- id: consumer
  name: './consumer.py'
  disabled: false
```

- `group:` a nested entry list loaded/unloaded as one unit.
- `isolate: name` gives the group an independent service scope for `name`.
- `insert:` patch payloads are unwrapped (per-row patch addressing by id is a
  DSH-specific loader extension, out of scope).
- `!!js` expression tags are out of scope (JavaScript-specific); values are
  literal.

## Mapping to the Cordis API

Python has no proxy/`this` semantics, so the port uses an **explicit
`ctx.get()` design** instead of JS-style attribute access (`ctx.greeter`):

| Cordis (JS)                  | dsh-like (Python)                          |
|------------------------------|--------------------------------------------|
| `ctx.greeter` / `ctx.get('greeter')` | `ctx.get('greeter')` (no proxy magic) |
| `ctx.plugin(plugin, config)` | `ctx.plugin(plugin, config)` → awaitable Fiber |
| `ctx.inject(deps, cb)`       | `ctx.inject(deps, cb)`                     |
| `ctx.on/once/emit/parallel/serial/bail/waterfall` | same (real methods) |
| `ctx.effect(fn, label)`      | `ctx.effect(fn, label)`                    |
| `ctx.provide/set/accessor/mixin` | same; `set` also routes accessors     |
| `ctx.extend/isolate/intercept` | same                                    |
| `Context.is(x)`              | `Context.is_context(x)` (`is` is a keyword) |
| `[Service.init]` / `[Service.check]` | `init()` / `check(value)` instance methods |
| `[Service.resolveConfig]` | `resolve_config(base, head)` — merges ancestor intercept entries |
| `[Service.extend]` | `extend_service(**props)` — derived per-context instance |
| `[Service.filter]` | `filter(ctx)` — isolation-scope check for this service |
| `[Service.invoke]`           | `__call__` (e.g. `ctx.logger(name)`)       |
| Schemastery schema           | pydantic `BaseModel` as `Config`           |
| `await fiber` / `fiber.await()` | `await fiber` / `fiber.await_()`        |
| `app/exit` (extension)       | emit `ctx.emit('app/exit', code)` to stop the CLI gracefully |

## Deliberate deviations from the vendored implementation

- **Fiber-scoped `internal/update` hooks.** The vendored
  `events.ts` stores non-global `internal/update` listeners on the root
  fiber's hook list (which would never fire for plugin fibers); this port
  implements the *documented* semantics — hooks are scoped to the fiber that
  registered them and run first in `fiber.update()`'s waterfall.
- **Deterministic load ordering.** The vendored `_reload()` awaits a microtask
  checkpoint before running plugin code; Python's equivalent would reorder
  dependent loads past the awaiting caller. The checkpoint is dropped so that
  after `await provider_fiber`, dependents are already loaded (matching the
  observable JS behavior).
- **Accessors are readable via `ctx.get`.** Without a context proxy there is
  no attribute-read path, so `ctx.get('name')` also resolves accessors.
- **`fiber.update()` returns the waterfall result** — a plain value when an
  update hook vetoes, or a coroutine for the default restart. Await only when
  awaitable.
- **`extend()` copies parent attributes** — JavaScript's prototype chain is
  emulated with a copy, and anonymous callables (`<lambda>`) get no display
  name so `fiber.name` inherits the nearest named ancestor.

## Layout

```
src/dshlike/
├── cli.py            # dshlike run [cordis.yml] [--wait]
└── cordis/
    ├── context.py    # Context, extend/isolate/intercept, ctx.* API
    ├── events.py     # 5 dispatch modes, internal/listener interception
    ├── reflect.py    # service store: provide/get/set/accessor/mixin/notify
    ├── fiber.py      # state machine, effects, dispose/restart/update
    ├── registry.py   # plugin()/inject(), runtimes, dependency refresh
    ├── service.py    # Service base class
    ├── scope.py      # isolation label maps
    ├── logger.py     # named logger service
    ├── errors.py     # CordisError / ValidationError
    └── loader/       # YAML composition, group/isolate, HMR watcher
examples/              # tutorial chapters 1-5 as runnable compositions
tests/                 # tutorial 1-6 + scope/update/hmr/coverage/loader (85 tests)
```

## Status & roadmap

- [x] M1 Cordis core (context/events/fiber/registry/reflect/service)
- [x] M2 config (pydantic), loader, CLI, examples
- [x] M3 HMR + diagnostics