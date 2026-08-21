"""Local-directory plugin loader.

Directory sources are an ordered list (global, then project) — the profile
layer is reserved: a future profile only inserts one more directory.
Each plugin is a ``.py`` file or a directory with ``__init__.py``.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from javis.plugins.instance import PluginInstance
from javis.plugins.registry import LoadReport, PluginRegistry
from javis.session.workspace import find_project_javis_dir, get_workspace_root

log = logging.getLogger(__name__)


@dataclass
class PluginSpec:
    name: str
    apply: Callable[..., Any]
    config_model: type | None
    inject: list[str]


def plugin_dirs(cwd: str | None = None, workspace: str | Path | None = None) -> list[Path]:
    """Ordered plugin directory sources: global, then project. Profile layer reserved."""
    root = get_workspace_root(workspace)
    dirs = [Path(root) / "plugins"]
    project_dir = find_project_javis_dir(cwd)
    if project_dir is not None and project_dir.resolve() != Path(root).resolve():
        dirs.append(project_dir / "plugins")
    return dirs


def discover_plugin_files(dirs: list[Path]) -> list[tuple[Path, str]]:
    """Return [(path, plugin_name)] in dir order; same-name later dir wins."""
    found: dict[str, Path] = {}
    for directory in dirs:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                found[entry.stem] = entry
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                found[entry.name] = entry
    return [(path, name) for name, path in found.items()]


def extract_plugins(module: Any, fallback_name: str) -> list[PluginSpec]:
    """Extract plugin specs from a loaded module (four forms, see spec §4)."""
    specs: list[PluginSpec] = []

    # Form ④: __plugins__ list wins if present.
    if getattr(module, "__plugins__", None):
        for entry in module.__plugins__:
            specs.append(
                PluginSpec(
                    name=str(getattr(entry, "name", fallback_name)),
                    apply=entry.apply,
                    config_model=getattr(entry, "Config", None),
                    inject=list(getattr(entry, "inject", [])),
                )
            )
        return specs

    apply_fn = getattr(module, "apply", None)
    if apply_fn is None:
        # Form ③: module-level plugin dict.
        plugin_obj = getattr(module, "plugin", None)
        if isinstance(plugin_obj, dict):
            apply_fn = plugin_obj.get("apply")
            if apply_fn is not None:
                specs.append(
                    PluginSpec(
                        name=str(plugin_obj.get("name", fallback_name)),
                        apply=apply_fn,
                        config_model=plugin_obj.get("Config"),
                        inject=list(plugin_obj.get("inject", [])),
                    )
                )
        return specs

    # Forms ① and ②: module-level apply (+ optional Config/inject/name).
    specs.append(
        PluginSpec(
            name=str(getattr(module, "name", fallback_name)),
            apply=apply_fn,
            config_model=getattr(module, "Config", None),
            inject=list(getattr(module, "inject", [])),
        )
    )
    return specs


def _load_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


async def load_plugins(
    registry: PluginRegistry,
    dirs: list[Path],
    plugins_cfg: dict[str, Any],
) -> LoadReport:
    """Discover, import and register plugin instances. Never raises.

    ``plugins_cfg`` is the ``config.plugins`` dict: ``{name: {enabled, config}}``.
    Import failures are isolated per plugin (logged, skipped).
    """
    report = LoadReport()
    for path, name in discover_plugin_files(dirs):
        try:
            module = _load_module(path, f"javis_plugin_{name}")
            specs = extract_plugins(module, name)
        # BLE001: import failures are arbitrary (syntax, missing deps, …) and must
        # be isolated per plugin — a broken file never takes the loader down.
        except Exception as exc:  # noqa: BLE001
            log.warning("Plugin %r failed to load from %s: %s", name, path, exc)
            report.skipped.append(name)
            report.errors[name] = str(exc)
            continue
        for spec in specs:
            # All config keys are the declared plugin name (spec.name).
            entry_cfg = plugins_cfg.get(spec.name, {})
            if not entry_cfg.get("enabled", True):
                report.skipped.append(spec.name)
                continue
            raw_config = dict(entry_cfg.get("config", {}))
            instance = PluginInstance(
                name=spec.name,
                apply_fn=spec.apply,
                config_model=spec.config_model,
                inject=spec.inject,
                raw_config=raw_config,
                ctx_builder=registry.ctx_builder,
                services=registry.services,
            )
            registry.add(instance)
    return report
