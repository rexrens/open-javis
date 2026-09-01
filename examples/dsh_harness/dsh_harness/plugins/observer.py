"""Plugin: the live-event observer (transcript).

Subscribes to the agent's **live** events (emit/serial listeners, in the
agent's scope in dsh; the demo dispatches on the shared context with the
agent in the payload) and prints a running transcript:

- ``agent/status``              — lifecycle transitions
- ``agent/inbox/inserted``      — queued input
- ``agent/inbox/claimed``       — boundary consumed the input
- ``agent/inbox/discarded``     — cancel cleared the queue
- ``tools/result``              — every committed tool result
- ``agent/turn-stopping``       — the turn boundary
- ``agent/error``               — failures at their live boundary

``report(session)`` renders the durable session log (user / assistant /
tool messages, turn outcomes, usage) — the part a UI bridge would replay.
"""

import os as _os
import sys as _sys

_DEMO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _DEMO_ROOT not in _sys.path:
    _sys.path.insert(0, _DEMO_ROOT)

from dsh_harness.contracts import (
    Events,
    TextBlock,
    ToolCallBlock,
)

name = "observer"


class Observer:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.lines: list[str] = []

    def line(self, text: str) -> None:
        self.lines.append(text)
        print(text)

    # -- live listeners ------------------------------------------------------

    def on_status(self, payload):
        self.line(f"● agent status → {payload['status']}")

    def on_inbox_inserted(self, payload):
        self.line(f"◦ inbox queued: {payload['message'].text!r}")

    def on_inbox_claimed(self, payload):
        self.line(f"◦ inbox claimed (turn {payload['turn']}): {payload['message'].text!r}")

    def on_inbox_discarded(self, payload):
        self.line(f"◦ inbox discarded: {payload['message'].text!r}")

    def on_tool_result(self, _exec, result):
        text = "".join(block.text for block in result.content if isinstance(block, TextBlock))
        flag = "✗" if result.is_error else "✓"
        extra = " [concludes-turn]" if result.concludes_turn else ""
        self.line(f"  {flag} tool result: {text}{extra}")

    def on_turn_stopping(self, payload):
        self.line(f"… turn {payload['turn']} stopping")

    def on_error(self, payload):
        self.line(f"✗ agent error (turn {payload['turn']} step {payload['step']}): {payload['error']}")

    # -- durable report ------------------------------------------------------

    def report(self, session) -> None:
        print()
        print("── session log " + "─" * 40)
        for event in session.events:
            data = event.data
            if event.type == "turn/start":
                print(f"  [{event.seq:>3}] turn {data['turn']} start")
            elif event.type == "step/start":
                print(f"  [{event.seq:>3}]   step {data['step']} start")
            elif event.type == "user/message":
                print(f"  [{event.seq:>3}]   user: {data['message'].text!r}")
            elif event.type == "assistant/message":
                message = data["message"]
                calls = [block for block in message.content if isinstance(block, ToolCallBlock)]
                interrupted = " (interrupted)" if data.get("interrupted") else ""
                text = message.text
                if text:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: {text!r}")
                for call in calls:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: → {call.name}({call.arguments})")
                if not text and not calls:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: (no content)")
            elif event.type == "tool/call":
                print(f"  [{event.seq:>3}]   tool call: {data['name']}({data['arguments']})")
            elif event.type == "tool/result":
                message = data["message"]
                block = message.content[0]
                text = "".join(b.text for b in block.content if isinstance(b, TextBlock))
                flag = "✗" if block.is_error else "✓"
                print(f"  [{event.seq:>3}]   tool result {flag}: {text}")
            elif event.type == "turn/end":
                print(f"  [{event.seq:>3}] turn {data['turn']} end: {data['reason'].kind}")
            elif event.type == "request/header":
                config = data["header"]["config"]
                print(
                    f"  [{event.seq:>3}] request/header ({data['reason']}): "
                    f"{config['provider']}/{config['model']} maxTokens={config['maxTokens']}"
                )
            # chunks / inbox splices / step-end / request-context: log detail
        total_in, total_out = session.usage_total()
        print(f"  usage: {total_in} input / {total_out} output tokens")


def apply(ctx):
    observer = Observer(ctx)
    ctx.on(Events.AGENT_STATUS, observer.on_status)
    ctx.on(Events.AGENT_INBOX_INSERTED, observer.on_inbox_inserted)
    ctx.on(Events.AGENT_INBOX_CLAIMED, observer.on_inbox_claimed)
    ctx.on(Events.AGENT_INBOX_DISCARDED, observer.on_inbox_discarded)
    ctx.on(Events.TOOLS_RESULT, observer.on_tool_result)
    ctx.on(Events.AGENT_TURN_STOPPING, observer.on_turn_stopping)
    ctx.on(Events.AGENT_ERROR, observer.on_error)
    ctx.provide("observer", observer)
