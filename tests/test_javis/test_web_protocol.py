"""Wire protocol, page rendering, auth, and asset resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from javis.app.web.assets import MissingAssetsError, load_assets, version_mismatch
from javis.app.web.auth import BrowserAuth
from javis.app.web.index import READY_MARKUP, render_index
from javis.app.web.protocol import (
    ProtocolError,
    RemoteError,
    end_frame,
    error_frame,
    error_response,
    item_frame,
    ok_response,
    parse_client_request,
    parse_stream_message,
    request_args,
)
from tests.test_javis.web_fixtures import write_assets


def _envelope(method: str = "session/list", args: dict[str, object] | None = None) -> bytes:
    return json.dumps(
        {
            "type": "client-request",
            "rpcId": "r1",
            "method": method,
            "payload": {"args": args or {}},
        }
    ).encode()


def test_parse_client_request_reads_the_named_args_object():
    rpc_id, method, args = parse_client_request(_envelope("session/prompt", {"a": 1}))
    assert (rpc_id, method) == ("r1", "session/prompt")
    assert args == {"a": 1}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        json.dumps({"type": "server-response", "rpcId": "r", "method": "m"}).encode(),
        json.dumps({"type": "client-request", "rpcId": "", "method": "m"}).encode(),
        json.dumps({"type": "client-request", "rpcId": "r", "payload": {"args": []}}).encode(),
    ],
)
def test_parse_client_request_rejects_malformed_envelopes(body: bytes):
    with pytest.raises(ProtocolError):
        parse_client_request(body)


def test_response_and_stream_frames_carry_the_dsh_shape():
    assert ok_response("r1", {"a": 1}) == {
        "type": "server-response",
        "rpcId": "r1",
        "result": {"ok": True, "value": {"a": 1}},
    }
    failure = error_response("r1", RemoteError("session/not-found", "gone", {"sessionId": "x"}))
    assert failure["result"]["ok"] is False
    assert failure["result"]["error"]["code"] == "session/not-found"
    assert item_frame("s1", 5) == {"type": "item", "streamId": "s1", "value": 5}
    assert end_frame("s1") == {"type": "end", "streamId": "s1"}
    assert error_frame("s1", RemoteError("x/y", "z"))["error"]["message"] == "z"


def test_parse_stream_message_handles_open_and_cancel():
    kind, stream_id, payload = parse_stream_message(
        json.dumps(
            {
                "type": "open",
                "streamId": "s1",
                "endpoint": "session/follow",
                "payload": {"args": {"sessionId": "abc"}},
            }
        )
    )
    assert (kind, stream_id) == ("open", "s1")
    endpoint, raw = payload
    assert endpoint == "session/follow"
    assert request_args(raw) == {"sessionId": "abc"}
    assert parse_stream_message('{"type":"cancel","streamId":"s1"}')[0] == "cancel"
    with pytest.raises(ProtocolError):
        parse_stream_message('{"type":"open","streamId":""}')


def test_render_index_places_rows_and_settles_boot_readiness():
    html = "<!doctype html><html><head><title>t</title></head><body><div></div></body></html>"
    rendered = render_index(
        html,
        [
            {"kind": "style", "text": ".a{}"},
            {"kind": "script", "placement": "body", "text": "1"},
            {"kind": "global", "name": "__X__", "value": {"a": "</script>"}},
        ],
    )
    head_at = rendered.index("<head>") + len("<head>")
    assert rendered[head_at:].startswith("<style>.a{}</style>")
    assert rendered.index("<title>") > rendered.index("<style>.a{}</style>")
    assert "<body><script>1</script>" in rendered
    assert "\\u003c/script>" in rendered
    assert f"<body><script>1</script>{READY_MARKUP}" in rendered


def test_assets_missing_reports_the_prepare_command(tmp_path: Path):
    with pytest.raises(MissingAssetsError) as excinfo:
        load_assets(tmp_path)
    assert "npm run prepare" in str(excinfo.value)


def test_assets_serve_bundles_and_reject_traversal(tmp_path: Path):
    write_assets(tmp_path)
    assets = load_assets(tmp_path)
    assert assets.dsh_version == "0.1.6-alpha.2"
    assert version_mismatch(assets) is None
    assert version_mismatch(assets, "9.9.9") is not None
    assert assets.plugin_path("/plugins/nope") is None
    served = assets.plugin_path(
        "/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=bbb222bbb222"
    )
    assert served is not None and served.name == "combo-bootstrap.js"
    assert assets.dist_path("/") == assets.dist_index.resolve()
    assert assets.dist_path("/assets/app.js") is not None
    assert assets.dist_path("/missing.js") is None
    assert assets.dist_path("/../boot.json") is None
    assert assets.graph["entries"][0]["id"] == "@deepseek-ai/dsh-client-modules"


def test_assets_serve_package_local_chunks_next_to_the_entry(tmp_path: Path):
    """A bundle's dynamic chunks resolve under its own /plugins prefix."""
    write_assets(tmp_path)
    package_dir = tmp_path / "plugins" / "@deepseek-ai" / "dsh-client-ui-chat"
    package_dir.mkdir(parents=True)
    (package_dir / "client.js").write_text("entry", encoding="utf-8")
    (package_dir / "client.helper.js").write_text("chunk", encoding="utf-8")
    assets = load_assets(tmp_path)

    entry = assets.plugin_path("/plugins/@deepseek-ai/dsh-client-ui-chat/client.js")
    chunk = assets.plugin_path("/plugins/@deepseek-ai/dsh-client-ui-chat/client.helper.js")
    assert entry is not None and entry.name == "client.js"
    assert chunk is not None and chunk.name == "client.helper.js"
    assert assets.plugin_path("/plugins/@deepseek-ai/../../boot.json") is None
    assert assets.plugin_path("/plugins/") is None


def test_browser_auth_exchanges_the_token_and_fences_requests():
    auth = BrowserAuth("127.0.0.1", 8422)
    trusted = {"host": "127.0.0.1:8422"}
    cookie = auth.issue_cookie()

    assert auth.index_exchange(trusted, auth.token) == "redirect"
    assert auth.index_exchange({**trusted, "cookie": cookie}, None) is None
    with pytest.raises(PermissionError, match="unauthorized"):
        auth.index_exchange(trusted, None)
    with pytest.raises(PermissionError, match="forbidden"):
        auth.index_exchange({"host": "evil.example:8422"}, auth.token)

    assert auth.request_rejection({**trusted, "cookie": cookie}) is None
    assert auth.request_rejection(trusted) == 401
    assert auth.request_rejection({"host": "evil.example:8422"}) == 403
    cross_site = {**trusted, "cookie": cookie, "sec-fetch-site": "cross-site"}
    assert auth.request_rejection(cross_site) == 403
    assert auth.cookie_is_valid("1.abc") is False
    assert auth.cookie_name in cookie
