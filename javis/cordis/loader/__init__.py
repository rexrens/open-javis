"""Composition loader: mount every entry of a ``cordis.yml`` as a plugin.

Port of the ``@deepseek-ai/cordis-plugin-loader`` behavior used by
deepseek-harness: the loader is itself a plugin mounted on the root context;
it reads a YAML composition (list of entries with ``id``/``name``/``config``/
``disabled``/``inject``/``provide``/``group``/``isolate``), resolves each entry
name to a Python module (relative path or dotted package name), and mounts it
under the current context. Loading order is dependency-driven, not positional.

``!!js`` expression tags are out of scope (JavaScript-specific); all values
are literal.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..service import Service
from .entry import Entry, parse_entries

if TYPE_CHECKING:
    from ..context import Context
    from ..fiber import Fiber


def load_module(name: str, base_url: str | None) -> tuple[Any, str | None]:
    """Resolve and import a plugin module by specifier.

    Returns ``(module, resolved_path)``; ``resolved_path`` is ``None`` for
    dotted package names (not file-based). Every call produces a fresh module
    object compiled directly from the current source — the bytecode cache is
    deliberately bypassed so HMR reloads always get the newest code (the
    importlib ``__pycache__`` validation compares *integer* source mtimes,
    which can falsely match for fast successive edits). The ``sys.modules``
    name is derived from the resolved path, so reloads replace the previous
    entry instead of leaking one module per reload.
    """
    if "/" in name or name.endswith(".py") or name.startswith("."):
        path = name if os.path.isabs(name) else os.path.join(base_url or os.getcwd(), name)
        path = os.path.normpath(path)
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
        module_name = f"_dshlike_plugin_{digest}"
        module = types.ModuleType(module_name)
        module.__file__ = path
        module.__name__ = module_name
        module.__package__ = None
        sys.modules[module_name] = module
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        code = compile(source, path, "exec")
        exec(code, module.__dict__)
        return module, path
    return importlib.import_module(name), None


class ModulePlugin:
    """Object plugin wrapping a loaded plugin module's exports."""

    def __init__(self, module: Any, name: str | None = None):
        self.apply = module.apply
        self.name = name if name is not None else getattr(module, "name", None)
        self.inject = getattr(module, "inject", None)
        self.Config = getattr(module, "Config", None)
        self.provide = getattr(module, "provide", None)
        self._module = module


class Loader(Service):
    """Loads a ``cordis.yml`` composition; installed as the ``loader`` service."""

    class Config(BaseModel):
        file: str

    def __init__(self, ctx: "Context", config: Any):
        super().__init__(ctx, "loader")
        self.file = config.file
        self.base_url = str(Path(self.file).resolve().parent)
        self._entries: dict[str, Entry] = {}  # entry id -> entry
        self._entry_fibers: dict[str, Any] = {}  # entry id -> fiber
        self._entry_paths: dict[str, str | None] = {}  # entry id -> module path
        self._entry_groups: dict[str, str] = {}  # member entry id -> group id
        self._module_cache: dict[str, Any] = {}  # resolved path -> module
        self._anon = 0
        # Persistence hook: write a config change back to the composition file
        # unless the update carried `noSave=True` or was vetoed.
        self.ctx.on("internal/update", self._persist_hook, {"global": True})
        self._load_entries()

    # -- persistence --------------------------------------------------------

    def _persist_hook(self, config: Any, noSave: bool, next: Any) -> Any:
        result = next()
        if noSave:
            return result
        # The waterfall receives the *resolved* config, and the innermost
        # restart sets `fiber.config` to that exact object synchronously
        # before yielding — so the updated fiber can be found immediately, no
        # polling needed.
        fiber = self._find_updated_fiber(config)
        if fiber is not None:
            asyncio.ensure_future(self._persist_after(fiber, config))
        return result

    def _find_updated_fiber(self, config: Any) -> Any:
        for fiber in self._entry_fibers.values():
            if fiber.config is config or fiber._config is config:
                return fiber
        return None

    async def _persist_after(self, fiber: Any, config: Any) -> None:
        try:
            while fiber.inertia is not None:
                await fiber.inertia
        except BaseException:
            return
        # Only persist when the update was actually applied. A veto or a
        # failed restart leaves `fiber.config`/`_error` untouched. Note: the
        # restart re-resolves the config into a *new* model instance, so this
        # is a value comparison, not identity.
        if fiber._error is not None or fiber.config != config:
            return
        for entry_id, f in self._entry_fibers.items():
            if f is fiber:
                entry = self._entries.get(entry_id)
                if entry is not None:
                    entry.config = fiber._config
                    self.write()
                break

    def write(self) -> None:
        """Persist the current composition back to the YAML file."""
        import yaml  # type: ignore[import-untyped]

        payload = [self._entry_to_dict(entry) for entry in self._entries.values()]
        with open(self.file, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, default_flow_style=False, sort_keys=False)

    @staticmethod
    def _entry_to_dict(entry: Entry) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if entry.id:
            data["id"] = entry.id
        if entry.name:
            data["name"] = entry.name
        if entry.config:
            data["config"] = entry.config
        if entry.disabled:
            data["disabled"] = True
        if entry.inject is not None:
            data["inject"] = entry.inject
        if entry.provide is not None:
            data["provide"] = entry.provide
        if entry.isolate:
            data["isolate"] = entry.isolate
        if entry.group:
            data["group"] = [Loader._entry_to_dict(g) for g in entry.group]
        return data

    def update_entry(self, entry_id: str, config: Any, noSave: bool = False) -> Any:
        """Apply a new config to an entry's fiber and (unless ``noSave``)
        persist it back to the composition file.

        Returns ``fiber.update``'s waterfall result; the write-back happens
        asynchronously after the restart settles and only if the update was
        applied (not vetoed).
        """
        fiber = self._entry_fibers.get(entry_id)
        if fiber is None:
            return None
        return fiber.update(config, noSave)

    # -- loading ------------------------------------------------------------

    def _load_entries(self) -> None:
        data = self._read_composition()
        entries = parse_entries(data)
        self._entries.clear()
        for entry in entries:
            self._entries[entry.effective_id(self._next_anon())] = entry
        self._mount_all()

    def _next_anon(self) -> int:
        self._anon += 1
        return self._anon

    def _read_composition(self) -> Any:
        import yaml

        with open(self.file, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _mount_all(self) -> None:
        for entry_id, entry in list(self._entries.items()):
            if entry_id in self._entry_fibers:
                continue
            if entry.disabled:
                continue
            self._entry_fibers[entry_id] = self._mount(self.ctx, entry, entry_id)

    def _mount(self, ctx: "Context", entry: Entry, entry_id: str) -> "Fiber":
        if entry.group:
            group_plugin = {
                "name": f"group:{entry_id}",
                "apply": lambda c: self._mount_group(c, entry.group, entry_id),
            }
            mount_ctx = ctx.isolate(entry.isolate) if entry.isolate else ctx
            return mount_ctx.plugin(group_plugin, {})

        module, path = self._resolve_module(entry.name)
        plugin = ModulePlugin(module, name=getattr(module, "name", None) or entry.name)
        inject = self._merge_inject(getattr(module, "inject", None), entry.inject)
        if inject:
            plugin.inject = inject
        self._entry_paths[entry_id] = path
        mount_ctx = ctx.isolate(entry.isolate) if entry.isolate else ctx
        return mount_ctx.plugin(plugin, entry.config)

    def _mount_guarded(self, entry_id: str, entry: Entry) -> Fiber | None:
        """Mount an entry without losing HMR tracking when the module fails.

        A module that fails to import (e.g. a syntax error mid-edit) must
        still be registered in ``_entry_paths`` so the watcher keeps polling
        it and retries on the next save; otherwise the entry silently drops
        out of HMR and can never recover without a full recompose.
        """
        try:
            return self._mount(self.ctx, entry, entry_id)
        except BaseException as error:
            self.ctx.logger.error(error)
            self._entry_paths[entry_id] = self._expected_module_path(entry)
            return None

    def _expected_module_path(self, entry: Entry) -> str | None:
        """Resolve the path HMR should keep watching for a file-based entry."""
        if entry.group:
            return None
        name = entry.name
        if "/" in name or name.endswith(".py") or name.startswith("."):
            path = name if os.path.isabs(name) else os.path.join(self.base_url or os.getcwd(), name)
            return os.path.normpath(path)
        return None

    def _mount_group(self, ctx: "Context", entries: list[Entry], group_id: str) -> None:
        for entry in entries:
            if entry.disabled:
                continue
            member_id = entry.effective_id(self._next_anon())
            self._entry_groups[member_id] = group_id
            self._entry_fibers[member_id] = self._mount(ctx, entry, member_id)

    def _resolve_module(self, name: str) -> tuple[Any, str | None]:
        module, path = load_module(name, self.base_url)
        if path is not None:
            self._module_cache[path] = module
        return module, path

    @staticmethod
    def _merge_inject(module_inject: Any, entry_inject: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for source in (module_inject, entry_inject):
            if not source:
                continue
            if isinstance(source, (list, tuple, set)):
                for item in source:
                    result[item] = None
            elif isinstance(source, dict):
                result.update(source)
        return result

    # -- reload / recompose (HMR) ------------------------------------------

    def _pop_entry(self, entry_id: str) -> Any:
        """Remove an entry (and its group members) from bookkeeping."""
        fiber = self._entry_fibers.pop(entry_id, None)
        self._entry_paths.pop(entry_id, None)
        for member_id in [k for k, v in self._entry_groups.items() if v == entry_id]:
            self._entry_fibers.pop(member_id, None)
            self._entry_groups.pop(member_id, None)
            self._entry_paths.pop(member_id, None)
        return fiber

    def reload(self, entry_id: str) -> Any:
        """Dispose and re-mount one entry (or its whole group)."""
        if entry_id in self._entry_groups:
            entry_id = self._entry_groups[entry_id]
        entry = self._entries.get(entry_id)
        if entry is None:
            return None
        old = self._pop_entry(entry_id)

        async def _reload() -> None:
            if old is not None:
                try:
                    await old.dispose()  # fully unload the old fiber first
                except BaseException:
                    pass
            if entry_id not in self._entry_fibers and not entry.disabled:
                fiber = self._mount_guarded(entry_id, entry)
                if fiber is not None:
                    self._entry_fibers[entry_id] = fiber

        if old is not None:
            return _reload()
        if not entry.disabled:
            fiber = self._mount_guarded(entry_id, entry)
            if fiber is not None:
                self._entry_fibers[entry_id] = fiber
        return None

    def recompose(self) -> Any:
        """Re-read the composition file and diff entries by id.

        - removed entries are unmounted,
        - **config-only** changes (same name/inject/provide/isolate/group)
          go through ``fiber.update(config, noSave=True)`` — the update hooks
          (and HMR) can veto or replace the restart,
        - structural changes (module, inject, group, isolate, disabled) are
          re-mounted.

        Entries without an explicit ``id`` keep their previous id while their
        content and ordinal position are unchanged, so a persistence write-back
        (which re-reads the file) does not remount unchanged anon entries.
        """
        data = self._read_composition()
        entries = parse_entries(data)
        old_list = list(self._entries.values())
        new: dict[str, Entry] = {}
        for idx, entry in enumerate(entries):
            if entry.id:
                new[entry.id] = entry
                continue
            reused = False
            if idx < len(old_list):
                old = old_list[idx]
                if old.id is None and old == entry:
                    # Same anon content at the same ordinal -> reuse its id.
                    for eid, candidate in self._entries.items():
                        if candidate is old:
                            new[eid] = entry
                            reused = True
                            break
            if not reused:
                new[entry.effective_id(self._next_anon())] = entry

        tasks: list[Any] = []
        # unmount removed or structurally changed top-level entries
        for entry_id in list(self._entry_fibers):
            if entry_id in self._entry_groups:
                continue  # members ride along with their group fiber
            new_entry = new.get(entry_id)
            old_entry = self._entries.get(entry_id)
            if new_entry is not None and old_entry == new_entry:
                continue  # unchanged
            if (
                new_entry is not None
                and old_entry is not None
                and new_entry.group is None
                and _config_only(old_entry, new_entry)
                and entry_id in self._entry_fibers
            ):
                # config-only change: update in place through the waterfall
                fiber = self._entry_fibers[entry_id]
                try:
                    result = fiber.update(new_entry.config, noSave=True)
                except BaseException as error:
                    self.ctx.logger.error(error)
                    continue
                if result is not None:
                    tasks.append(_settle(result))
                continue
            fiber = self._pop_entry(entry_id)
            if fiber is not None:
                tasks.append(_dispose_quietly(fiber))
        self._entries = new

        async def _recompose() -> None:
            # Dispose old fibers (and settle in-place updates) FIRST, so a
            # structural change never overlaps the old and new
            # implementations: two providers of the same service name would
            # otherwise race, and the new fiber could fail with a
            # duplicate-provide error.
            if tasks:
                await asyncio_gather_all(tasks)
            # then mount new entries
            for entry_id, entry in new.items():
                if entry_id in self._entry_fibers or entry.disabled:
                    continue
                fiber = self._mount_guarded(entry_id, entry)
                if fiber is not None:
                    self._entry_fibers[entry_id] = fiber

        return _recompose()

    # -- diagnostics --------------------------------------------------------

    def entries(self) -> dict[str, Entry]:
        return dict(self._entries)

    def fibers(self) -> dict[str, Any]:
        return dict(self._entry_fibers)

    def module_paths(self) -> dict[str, str | None]:
        return dict(self._entry_paths)


async def _dispose_quietly(fiber: Any) -> None:
    """Fully unload a fiber. ``await fiber`` only waits for transitions; the
    actual teardown is ``fiber.dispose()``."""
    try:
        await fiber.dispose()
    except BaseException:
        pass


async def _settle(result: Any) -> None:
    """Await a possibly-awaitable result (update waterfall outcome)."""
    if hasattr(result, "__await__"):
        try:
            await result
        except BaseException:
            pass


async def asyncio_gather_all(tasks: list[Any]) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)


def _config_only(a: Entry, b: Entry) -> bool:
    """Do two entries differ only in their ``config``?"""
    return (
        a.name == b.name
        and a.disabled == b.disabled
        and a.inject == b.inject
        and a.provide == b.provide
        and a.isolate == b.isolate
        and a.group == b.group
    )
