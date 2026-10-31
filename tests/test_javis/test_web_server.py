"""End-to-end HTTP/WebSocket behavior of the web host."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from javis.app.web.auth import BrowserAuth
from javis.app.web.endpoints import register_all
from javis.app.web.registry import EndpointRegistry
from javis.app.web.server import create_app
from javis.app.web.session_runtime import SessionRuntime
from tests.test_javis.web_fixtures import write_assets

HOST = "127.0.0.1:8422"


@pytest.fixture
def host(tmp_path: Path, fake_engine_factory):
    """A live host: synthetic assets, deterministic harness, and a TestClient."""
    from javis.app.web.assets import load_assets

    fake_engine_factory()
    write_assets(tmp_path)
    assets = load_assets(tmp_path)
    runtime = SessionRuntime(cwd=str(tmp_path), workspace=str(tmp_path / "workspace"))
    auth = BrowserAuth("127.0.0.1", 8422)
    registry = EndpointRegistry()
    register_all(registry, runtime)
    app = create_app(assets=assets, runtime=runtime, auth=auth, registry=registry)
    with TestClient(app) as client:
        client.headers.update({"host": HOST})
        yield client, auth, runtime


def _rpc(client: TestClient, endpoint: str, args: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/{endpoint}",
        json={
            "type": "client-request",
            "rpcId": "r1",
            "method": endpoint,
            "payload": {"args": args},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _authenticate(client: TestClient, auth: BrowserAuth) -> None:
    response = client.get("/", params={"token": auth.token}, follow_redirects=False)
    assert response.status_code == 302
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    client.cookies.set(auth.cookie_name, value)


def test_index_requires_the_launch_token_then_serves_the_boot_manifest(host):
    client, auth, _runtime = host
    assert client.get("/").status_code == 401

    exchange = client.get("/", params={"token": auth.token}, follow_redirects=False)
    assert exchange.status_code == 302
    assert exchange.headers["set-cookie"].startswith(auth.cookie_name)

    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    client.cookies.set(auth.cookie_name, value)
    page = client.get("/")
    assert page.status_code == 200
    assert "__DSH_BOOT__" in page.text
    assert "__DSH_BOOT_READY__" in page.text
    assert "<style>" not in page.text


def test_static_assets_and_bundles_are_public(host):
    client, _auth, _runtime = host
    assert client.get("/assets/app.js").status_code == 200
    bundle = client.get(
        "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=bbb222bbb222"
    )
    assert bundle.status_code == 200
    assert "__combo" in bundle.text
    assert client.get("/plugins/unknown.js").status_code == 404
    assert client.get("/nope.js").status_code == 404


def test_unary_endpoints_require_the_cookie(host):
    client, auth, _runtime = host
    assert (
        client.post(
            "/api/session/list",
            json={"type": "client-request", "rpcId": "r1", "method": "session/list"},
        ).status_code
        == 401
    )
    _authenticate(client, auth)
    payload = _rpc(client, "session/list", {})
    assert payload["type"] == "server-response"
    assert payload["rpcId"] == "r1"
    assert payload["result"]["ok"] is True
    # The host owns the first session: a blank browser conversation still gets
    # one row to select, so the composer never prompts an empty identity.
    items = payload["result"]["value"]["items"]
    assert len(items) == 1
    assert items[0]["sessionId"]


def test_unary_failures_use_the_structured_envelope(host):
    client, auth, _runtime = host
    _authenticate(client, auth)

    unknown = _rpc(client, "terminal/create", {})
    assert unknown["result"]["ok"] is False
    assert unknown["result"]["error"]["code"] == "gateway/unknown-endpoint"

    mismatch = client.post(
        "/api/session/list",
        json={
            "type": "client-request",
            "rpcId": "r2",
            "method": "session/prompt",
            "payload": {"args": {}},
        },
    ).json()
    assert mismatch["rpcId"] == "r2"
    assert mismatch["result"]["error"]["code"] == "gateway/bad-request"

    malformed = client.post("/api/session/list", content=b"not json").json()
    assert malformed["rpcId"] == "invalid-request"
    assert malformed["result"]["error"]["code"] == "gateway/bad-request"


def test_prompt_round_trip_reaches_a_settled_assistant_message(host):
    client, auth, _runtime = host
    _authenticate(client, auth)
    created = _rpc(client, "session/create", {})
    session_id = created["result"]["value"]["sessionId"]

    accepted = _rpc(
        client,
        "session/prompt",
        {
            "requestId": "req-1",
            "sessionId": session_id,
            "mode": "queue",
            "content": [{"type": "text", "text": "hello from the browser"}],
        },
    )
    assert accepted["result"]["value"] == {"accepted": True}

    records: list[dict[str, Any]] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        page = _rpc(client, "session/page", {"sessionId": session_id, "maxMessages": 100})
        records = page["result"]["value"]["records"]
        if records and records[-1]["event"]["type"] == "turn/end":
            break
        time.sleep(0.05)

    types = [record["event"]["type"] for record in records]
    assert types[:3] == ["turn/start", "user/message", "step/start"]
    assert types[-1] == "turn/end"
    assistant = records[types.index("assistant/message")]["event"]["data"]
    assert assistant["message"]["content"] == [
        {"type": "text", "text": "fake reply to: hello from the browser\n"}
    ]

    listed = _rpc(client, "session/list", {})["result"]["value"]["items"]
    assert [row["sessionId"] for row in listed] == [session_id]
    assert listed[0]["blank"] is False


def test_events_stream_opens_with_ready(host):
    client, auth, _runtime = host
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    with client.websocket_connect(
        "/api/remote.mux",
        headers={"host": HOST, "cookie": f"{auth.cookie_name}={value}"},
    ) as socket:
        socket.send_json(
            {
                "type": "open",
                "streamId": "s1",
                "endpoint": "$events",
                "payload": {"args": {}},
            }
        )
        frame = socket.receive_json()
        assert frame["type"] == "item"
        assert frame["streamId"] == "s1"
        assert frame["value"]["type"] == "ready"
        socket.send_json({"type": "cancel", "streamId": "s1"})


def test_mux_rejects_unauthenticated_sockets(host):
    client, _auth, _runtime = host
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/api/remote.mux", headers={"host": HOST}
    ) as socket:
        socket.receive_json()


def test_follow_stream_delivers_the_snapshot_over_the_mux(host):
    client, auth, _runtime = host
    _authenticate(client, auth)
    session_id = _rpc(client, "session/create", {})["result"]["value"]["sessionId"]
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    with client.websocket_connect(
        "/api/remote.mux",
        headers={"host": HOST, "cookie": f"{auth.cookie_name}={value}"},
    ) as socket:
        socket.send_json(
            {
                "type": "open",
                "streamId": "f1",
                "endpoint": "session/follow",
                "payload": {"args": {"sessionId": session_id, "assistantStream": True}},
            }
        )
        snapshot = socket.receive_json()["value"]
        assert snapshot["type"] == "snapshot"
        assert snapshot["header"]["id"] == session_id
        assert snapshot["records"] == []
        socket.send_json({"type": "cancel", "streamId": "f1"})


def test_unknown_stream_endpoint_reports_an_error_frame(host):
    client, auth, _runtime = host
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    with client.websocket_connect(
        "/api/remote.mux",
        headers={"host": HOST, "cookie": f"{auth.cookie_name}={value}"},
    ) as socket:
        socket.send_json(
            {
                "type": "open",
                "streamId": "t1",
                "endpoint": "terminal/follow",
                "payload": {"args": {}},
            }
        )
        frame = socket.receive_json()
        assert frame["type"] == "error"
        assert frame["error"]["code"] == "gateway/unknown-endpoint"


def test_malformed_stream_frame_is_answered_without_closing(host):
    client, auth, _runtime = host
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    with client.websocket_connect(
        "/api/remote.mux",
        headers={"host": HOST, "cookie": f"{auth.cookie_name}={value}"},
    ) as socket:
        socket.send_text(json.dumps({"type": "nope", "streamId": "x"}))
        frame = socket.receive_json()
        assert frame["type"] == "error"
        assert frame["error"]["code"] == "gateway/bad-request"


def test_blank_identity_prompts_the_host_default_session(host):
    """The browser may submit before it ever created a session."""
    client, auth, _runtime = host
    _authenticate(client, auth)

    listed = _rpc(client, "session/list", {})["result"]["value"]["items"]
    default_id = listed[0]["sessionId"]

    accepted = _rpc(
        client,
        "session/prompt",
        {
            "requestId": "req-blank",
            "sessionId": "",
            "mode": "queue",
            "content": [{"type": "text", "text": "hello without a session"}],
        },
    )
    assert accepted["result"]["value"] == {"accepted": True}

    records: list[dict[str, Any]] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        page = _rpc(client, "session/page", {"sessionId": default_id, "maxMessages": 100})
        records = page["result"]["value"]["records"]
        if records and records[-1]["event"]["type"] == "turn/end":
            break
        time.sleep(0.05)
    assert [record["event"]["type"] for record in records][-1] == "turn/end"


def test_blank_address_opens_the_follow_stream(host):
    """`session/follow` with no address must open, not fail with bad-request."""
    client, auth, _runtime = host
    value = auth.issue_cookie().split(";", 1)[0].partition("=")[2]
    with client.websocket_connect(
        "/api/remote.mux",
        headers={"host": HOST, "cookie": f"{auth.cookie_name}={value}"},
    ) as socket:
        socket.send_json(
            {
                "type": "open",
                "streamId": "blank",
                "endpoint": "session/follow",
                "payload": {"args": {"address": {"kind": "session", "sessionId": ""}}},
            }
        )
        frame = socket.receive_json()
        assert frame["type"] == "item"
        assert frame["value"]["type"] == "snapshot"
        assert frame["value"]["header"]["id"]
        socket.send_json({"type": "cancel", "streamId": "blank"})
