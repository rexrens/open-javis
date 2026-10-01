"""Launch the dsh web UI backed by the javis harness.

Mirrors ``react_launcher``'s role for the browser surface: resolve the frontend
directory, make sure the assembly output exists, then start the host — either
in-process or as the ``javis web --backend-only`` child process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import signal
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Any

from javis.app.web.assets import REQUIRED_DSH_VERSION, MissingAssetsError, load_assets

log = logging.getLogger(__name__)

#: Default listen port for the web UI.
DEFAULT_WEB_PORT = 8422

#: How long ``launch_web`` waits for the child host to accept connections.
READY_TIMEOUT_SECONDS = 30.0

#: Environment variable carrying the launcher's token to the child host.
TOKEN_ENV = "JAVIS_WEB_TOKEN"


def _get_web_frontend_dir() -> Path:
    """Return the web frontend directory.

    Checks in order:
        1. Bundled inside the installed package (pip install): javis/_frontend_web/
        2. Development repo layout: <repo>/frontend/web/
    """
    pkg_root = Path(__file__).resolve().parents[1]
    pkg_frontend = pkg_root / "_frontend_web"
    if (pkg_frontend / "package.json").exists():
        return pkg_frontend

    repo_root = Path(__file__).resolve().parents[2]
    dev_frontend = repo_root / "frontend" / "web"
    if (dev_frontend / "package.json").exists():
        return dev_frontend

    return pkg_frontend


def _resolve_npm() -> str:
    return shutil.which("npm") or "npm"


async def prepare_assets(
    frontend_dir: Path, *, dsh_root: str | Path | None = None
) -> int:
    """Run the assembly script (``npm run prepare``) for one frontend directory."""
    if dsh_root is None:
        print(
            "javis web: --rebuild-assets needs --dsh-root <deepseek-harness checkout>\n"
            "  (or set DSH_ROOT)",
            file=sys.stderr,
        )
        return 1
    command = [
        _resolve_npm(),
        "run",
        "prepare",
        "--",
        "--dsh-root",
        str(Path(dsh_root).expanduser()),
    ]
    process = await asyncio.create_subprocess_exec(*command, cwd=str(frontend_dir))
    return await process.wait()


def free_port(preferred: int | None) -> int:
    """Return ``preferred`` when it is usable, else an OS-assigned free port."""
    if preferred and preferred > 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", preferred))
            except OSError:
                log.warning("port %s is busy; choosing a free one instead", preferred)
            else:
                return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def wait_for_port(port: int, *, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    """Wait until something accepts connections on the loopback port."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    return False


def _backend_command(
    *,
    cwd: str | None,
    workspace: str | Path | None,
    model: str | None,
    max_turns: int | None,
    plugins: str | Path | None,
    port: int,
) -> list[str]:
    """Command the launcher spawns for the host process."""
    command = [sys.executable, "-m", "javis", "web", "--backend-only", "--port", str(port)]
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace:
        command.extend(["--workspace", str(workspace)])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    if plugins:
        command.extend(["--plugins", str(plugins)])
    return command


async def launch_web(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    plugins: str | Path | None = None,
    port: int | None = None,
    open_browser: bool = True,
    in_process: bool = False,
    rebuild_assets: bool = False,
    dsh_root: str | Path | None = None,
) -> int:
    """Serve the dsh web UI on loopback, opening it in the default browser."""
    frontend_dir = _get_web_frontend_dir()
    if rebuild_assets:
        code = await prepare_assets(frontend_dir, dsh_root=dsh_root)
        if code != 0:
            return code
    try:
        assets = load_assets(frontend_dir)
    except MissingAssetsError as exc:
        print(f"javis web: {exc}", file=sys.stderr)
        return 1
    if assets.dsh_version != REQUIRED_DSH_VERSION:
        print(
            f"javis web: assets were prepared from dsh {assets.dsh_version}, but this javis "
            f"build implements the {REQUIRED_DSH_VERSION} wire contract. Rebuild them with "
            "`javis web --rebuild-assets --dsh-root <checkout>`.",
            file=sys.stderr,
        )
        return 1

    chosen_port = free_port(port)
    # A caller-provided token keeps the launch URL stable across restarts.
    token = os.environ.get(TOKEN_ENV) or secrets.token_urlsafe(24)
    if in_process:
        return await _serve_in_process(
            assets=assets,
            cwd=cwd,
            workspace=workspace,
            model=model,
            max_turns=max_turns,
            plugins=plugins,
            port=chosen_port,
            open_browser=open_browser,
            token=token,
        )

    command = _backend_command(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
        port=chosen_port,
    )
    environment = {**os.environ, TOKEN_ENV: token}
    process = await asyncio.create_subprocess_exec(*command, env=environment)
    if not await wait_for_port(chosen_port):
        process.terminate()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        print("javis web: the host did not start listening in time", file=sys.stderr)
        return 1
    await _announce(chosen_port, token=token, open_browser=open_browser)

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _stop(*_args: object) -> None:
        stopping.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, NotImplementedError):
            loop.add_signal_handler(signal_name, _stop)
    try:
        await asyncio.wait(
            [asyncio.create_task(process.wait()), asyncio.create_task(stopping.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
    return int(process.returncode or 0)


async def _announce(port: int, *, token: str, open_browser: bool) -> None:
    """Print the launch URL and optionally open the default browser."""
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(f"javis web: {url}")
    if open_browser:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(webbrowser.open, url)


async def _serve_in_process(
    *,
    assets: Any,
    cwd: str | None,
    workspace: str | Path | None,
    model: str | None,
    max_turns: int | None,
    plugins: str | Path | None,
    port: int,
    open_browser: bool,
    token: str,
) -> int:
    """Run the host on this process's event loop."""
    from javis.app.web.server import serve

    runtime, auth, registry = _host_parts(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
        port=port,
        token=token,
    )
    await _announce(port, token=token, open_browser=open_browser)
    await serve(
        assets=assets,
        runtime=runtime,
        auth=auth,
        registry=registry,
        port=port,
    )
    return 0


def _host_parts(
    *,
    cwd: str | None,
    workspace: str | Path | None,
    model: str | None,
    max_turns: int | None,
    plugins: str | Path | None,
    port: int,
    token: str | None = None,
) -> tuple[Any, Any, Any]:
    """Build the runtime, auth, and endpoint registry for one host."""
    from javis.app.web.auth import BrowserAuth
    from javis.app.web.endpoints import register_all
    from javis.app.web.registry import EndpointRegistry
    from javis.app.web.session_runtime import SessionRuntime

    runtime = SessionRuntime(
        cwd=str(Path(cwd or Path.cwd()).resolve()),
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
    )
    auth = BrowserAuth("127.0.0.1", port, token=token or os.environ.get(TOKEN_ENV))
    registry = EndpointRegistry()
    register_all(registry, runtime)
    return runtime, auth, registry


async def run_web_backend(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    plugins: str | Path | None = None,
    port: int | None = None,
) -> int:
    """Run the host directly (the mode ``javis web`` spawns)."""
    from javis.app.web.server import serve

    frontend_dir = _get_web_frontend_dir()
    try:
        assets = load_assets(frontend_dir)
    except MissingAssetsError as exc:
        print(f"javis web: {exc}", file=sys.stderr)
        return 1
    chosen_port = port or DEFAULT_WEB_PORT
    runtime, auth, registry = _host_parts(
        cwd=cwd,
        workspace=workspace,
        model=model,
        max_turns=max_turns,
        plugins=plugins,
        port=chosen_port,
        token=os.environ.get(TOKEN_ENV),
    )
    print(f"javis web: {auth.authenticated_url(f'http://127.0.0.1:{chosen_port}')}")
    await serve(
        assets=assets,
        runtime=runtime,
        auth=auth,
        registry=registry,
        port=chosen_port,
    )
    return 0
