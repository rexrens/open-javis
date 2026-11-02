"""Entry-point dispatch and launcher behavior for ``javis web``."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from javis.app.app import run_web_mode
from javis.app.web.assets import load_assets
from javis.app.web_launcher import (
    TOKEN_ENV,
    _get_web_frontend_dir,
    _host_parts,
    free_port,
    launch_web,
    prepare_assets,
    wait_for_port,
)
from tests.test_javis.web_fixtures import write_assets


def _loopback_sockets_allowed() -> bool:
    """Whether this environment permits binding a loopback socket."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except OSError:
        return False
    return True


needs_sockets = pytest.mark.skipif(
    not _loopback_sockets_allowed(), reason="loopback sockets are blocked in this environment"
)


async def test_run_web_mode_backend_only_dispatches_to_the_host(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_backend(**kwargs: object) -> int:
        captured.update(kwargs)
        return 5

    monkeypatch.setattr("javis.app.app.run_web_backend", fake_backend)
    code = await run_web_mode(
        backend_only=True,
        cwd="/tmp/proj",
        workspace="/tmp/ws",
        model="m1",
        max_turns=3,
        port=9000,
    )
    assert code == 5
    assert captured == {
        "cwd": "/tmp/proj",
        "workspace": "/tmp/ws",
        "model": "m1",
        "max_turns": 3,
        "plugins": None,
        "port": 9000,
    }


async def test_run_web_mode_default_dispatches_to_the_launcher(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_launch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("javis.app.app.launch_web", fake_launch)
    code = await run_web_mode(cwd="/tmp/proj", open_browser=False, in_process=True)
    assert code == 0
    assert captured["cwd"] == "/tmp/proj"
    assert captured["open_browser"] is False
    assert captured["in_process"] is True


def test_web_frontend_dir_resolves_the_repo_layout():
    frontend = _get_web_frontend_dir()
    assert (frontend / "package.json").is_file()
    assert (frontend / "prepare.mjs").is_file()
    assert (frontend / "composition.json").is_file()


@needs_sockets
def test_free_port_prefers_a_free_port_and_falls_back():
    assert free_port(0) > 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = int(busy.getsockname()[1])
        assert free_port(taken) != taken


async def test_prepare_assets_needs_a_dsh_root(tmp_path: Path, capsys):
    code = await prepare_assets(tmp_path)
    assert code == 1
    assert "dsh-root" in capsys.readouterr().err


async def test_launch_web_without_assets_explains_the_fix(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("javis.app.web_launcher._get_web_frontend_dir", lambda: tmp_path)
    code = await launch_web()
    assert code == 1
    assert "npm run prepare" in capsys.readouterr().err


async def test_launch_web_rejects_a_version_mismatch(tmp_path: Path, monkeypatch, capsys):
    write_assets(tmp_path, dsh_version="0.0.1")
    monkeypatch.setattr("javis.app.web_launcher._get_web_frontend_dir", lambda: tmp_path)
    code = await launch_web()
    assert code == 1
    assert "wire contract" in capsys.readouterr().err


@needs_sockets
async def test_wait_for_port_times_out_on_a_closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        closed = int(probe.getsockname()[1])
    assert await wait_for_port(closed, timeout=0.3) is False


async def test_launch_web_honours_a_preset_token(tmp_path: Path, monkeypatch):
    """A caller-set token survives a restart, so the printed URL stays valid."""
    write_assets(tmp_path)
    monkeypatch.setattr("javis.app.web_launcher._get_web_frontend_dir", lambda: tmp_path)
    monkeypatch.setenv(TOKEN_ENV, "stable-token")
    monkeypatch.setattr("javis.app.web_launcher.free_port", lambda _preferred: 8422)
    announcements: list[tuple[int, str]] = []

    async def fake_announce(port: int, *, token: str, open_browser: bool) -> None:
        announcements.append((port, token))
        raise RuntimeError("stop before spawning the host")

    monkeypatch.setattr("javis.app.web_launcher._announce", fake_announce)
    with pytest.raises(RuntimeError, match="stop before spawning"):
        await launch_web(in_process=True)
    assert announcements == [(8422, "stable-token")]


def test_host_parts_wires_the_v1_endpoints(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "shared-token")
    _runtime, auth, registry = _host_parts(
        cwd=str(tmp_path),
        workspace=None,
        model=None,
        max_turns=None,
        plugins=None,
        port=8422,
    )
    assert auth.token == "shared-token"
    assert "session/follow" in registry.implemented()
    assert "workspace/follow" in registry.implemented()
    assert "terminal/create" not in registry.implemented()


def test_assets_round_trip_through_the_fixture(tmp_path: Path):
    write_assets(tmp_path)
    assets = load_assets(tmp_path)
    assert assets.manifest["dshCommit"] == "ddefc45fbc"
    assert assets.graph["batches"][0]["phase"] == "bootstrap"


async def test_run_web_backend_wires_assets_runtime_and_token(tmp_path: Path, monkeypatch):
    from javis.app.web_launcher import run_web_backend

    write_assets(tmp_path)
    monkeypatch.setattr("javis.app.web_launcher._get_web_frontend_dir", lambda: tmp_path)
    monkeypatch.setenv(TOKEN_ENV, "shared-token")
    captured: dict[str, object] = {}

    async def fake_serve(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("javis.app.web.server.serve", fake_serve)
    code = await run_web_backend(cwd=str(tmp_path), port=8422)
    assert code == 0
    assert captured["port"] == 8422
    auth = captured["auth"]
    assert auth.token == "shared-token"
    assert "session/follow" in captured["registry"].implemented()


@needs_sockets
@pytest.mark.parametrize("preferred", [None, 0])
def test_free_port_handles_unset_preferences(preferred: int | None):
    assert 1024 < free_port(preferred) < 65536
