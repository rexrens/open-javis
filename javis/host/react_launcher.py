"""Launch the React terminal frontend.

Forked from openharness.ui.react_launcher. Resolves the frontend directory
(bundled or dev), installs npm deps on first run, and spawns ``tsx src/index.tsx``
with ``OPENHARNESS_FRONTEND_CONFIG`` set so the frontend knows how to start
the backend.

The env var name is kept as ``OPENHARNESS_FRONTEND_CONFIG`` because the
existing TypeScript frontend reads that exact key — renaming it would require
a frontend change.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path


def _resolve_theme() -> str:
    return "default"


def _resolve_npm() -> str:
    return shutil.which("npm") or "npm"


def _resolve_tsx(frontend_dir: Path) -> tuple[str, ...]:
    """Resolve the tsx command, preferring the local binary for TTY safety."""
    bin_dir = frontend_dir / "node_modules" / ".bin"
    if sys.platform == "win32":
        for name in ("tsx.cmd", "tsx.ps1", "tsx"):
            candidate = bin_dir / name
            if candidate.exists():
                return (str(candidate),)
    else:
        candidate = bin_dir / "tsx"
        if candidate.exists():
            return (str(candidate),)

    global_tsx = shutil.which("tsx")
    if global_tsx:
        return (global_tsx,)

    return (_resolve_npm(), "exec", "--", "tsx")


def _get_frontend_dir() -> Path:
    """Return the React terminal frontend directory.

    Checks in order:
        1. Bundled inside the installed package (pip install): javis/_frontend/
        2. Development repo layout: <repo>/frontend/terminal/
    """
    # 1. Bundled inside package
    pkg_root = Path(__file__).resolve().parents[1]
    pkg_frontend = pkg_root / "_frontend"
    if (pkg_frontend / "package.json").exists():
        return pkg_frontend

    # 2. Development repo: <repo>/frontend/terminal/
    # __file__ = <repo>/javis/host/react_launcher.py
    # parents[0] = javis/host/, parents[1] = javis/, parents[2] = <repo>/
    repo_root = Path(__file__).resolve().parents[2]
    dev_frontend = repo_root / "frontend" / "terminal"
    if (dev_frontend / "package.json").exists():
        return dev_frontend

    return pkg_frontend  # will error with clear message downstream


def _build_backend_command(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Return the command the React frontend will spawn to start the backend."""
    command = [sys.executable, "-m", "javis", "--backend-only"]
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace:
        command.extend(["--workspace", str(workspace)])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    return command


async def launch_react_tui(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
) -> int:
    """Launch the React terminal frontend."""
    frontend_dir = _get_frontend_dir()
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"React terminal frontend is missing: {package_json}")

    npm = _resolve_npm()
    if not (frontend_dir / "node_modules").exists():
        install = await asyncio.create_subprocess_exec(
            npm,
            "install",
            "--no-fund",
            "--no-audit",
            cwd=str(frontend_dir),
        )
        if await install.wait() != 0:
            raise RuntimeError("Failed to install React terminal frontend dependencies")

    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    env = os.environ.copy()
    env["OPENHARNESS_FRONTEND_CONFIG"] = json.dumps(
        {
            "backend_command": _build_backend_command(
                cwd=cwd_path,
                workspace=workspace,
                model=model,
                max_turns=max_turns,
            ),
            "initial_prompt": None,
            "theme": _resolve_theme(),
        }
    )
    tsx_cmd = _resolve_tsx(frontend_dir)
    process = await asyncio.create_subprocess_exec(
        *tsx_cmd,
        "src/index.tsx",
        cwd=str(frontend_dir),
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )
    return await process.wait()


__all__ = ["launch_react_tui"]
