"""``$events``: the forwarded-host-event stream that opens a connection generation."""

from __future__ import annotations

from typing import Any

from javis.app.web.registry import EndpointRegistry, StreamSink
from javis.app.web.session_runtime import SessionRuntime


def register(registry: EndpointRegistry, runtime: SessionRuntime) -> None:
    """Wire the internal event stream and its waterfall result endpoint."""

    @registry.stream("$events")
    async def _events(args: dict[str, Any], sink: StreamSink) -> None:
        del args
        client_id = f"client_{id(sink):x}"
        await sink.send(
            {
                "type": "ready",
                "clientId": client_id,
                "host": {"home": str(runtime.cwd)},
            }
        )
        queue = runtime.subscribe_events()
        try:
            while True:
                frame = await queue.get()
                await sink.send(
                    {"type": "emit", "event": frame["event"], "args": list(frame["args"])}
                )
        finally:
            runtime.unsubscribe_events(queue)

    @registry.unary("$events/result")
    async def _result(args: dict[str, Any]) -> dict[str, Any]:
        # v1 forwards no waterfall events, so nothing is ever pending.
        del args
        return {}
