#!/usr/bin/env python
"""Run the harness demo scenarios through the Cordis plugin system.

The demo harness (``examples/dsh_harness``) is a dsh-style agent loop — phase
state machine, inbox, session event log, exclusive/parallel tool scheduling,
and the agent/* waterfalls — composed entirely out of Cordis plugins
(``examples/dsh_harness/dsh_harness/cordis.yml``). This entry point:

1. boots a root context and mounts the composition on the ``Loader``
   (dependency-driven load order, fiber lifecycle, reversible services);
2. drives one scenario through the public agent API
   (``followup`` / ``steer`` / ``when_idle``);
3. prints the live transcript (observer plugin) + the durable session log,
   and checks the scenario's expectations.

Usage (from the repo root, with ``javis`` importable)::

    uv run python examples/dsh_harness/cli.py                 # all four scenarios
    uv run python examples/dsh_harness/cli.py --scenario tools
    uv run python examples/dsh_harness/cli.py --scenario steer --verbose

The mock provider is scripted (``dsh_harness/mock_llm.py``); no API key needed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

DEMO_ROOT = Path(__file__).resolve().parent
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from dsh_harness.contracts import UserMessage

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader
from javis.cordis.registry import settle

COMPOSITION = DEMO_ROOT / "dsh_harness" / "cordis.yml"
SCENARIOS = ("text", "tools", "retry", "steer")

PROMPTS = {
    "text": "What is 2+2?",
    "tools": "Compare the weather in Paris and Tokyo.",
    "retry": "Say hello (the provider is flaky today).",
    "steer": "What time is it?",
}


# ---------------------------------------------------------------------------
# Composition boot (mirrors javis/cordis/cli.py)
# ---------------------------------------------------------------------------


async def compose(scenario: str) -> tuple[Context, Any, Any]:
    """Mount the composition and settle; returns (ctx, agent, session)."""
    os.environ["HARNESS_DEMO_SCENARIO"] = scenario
    ctx = Context()
    ctx.baseUrl = str(COMPOSITION.parent)
    loader_fiber = ctx.plugin(Loader, {"file": str(COMPOSITION)})
    await loader_fiber
    await settle(ctx)
    failed = [
        fiber
        for runtime in ctx.registry.values()
        for fiber in list(runtime.fibers)
        if fiber.state == FiberState.FAILED
    ]
    if failed:
        for fiber in failed:
            print(f"[error] fiber {fiber.name!r} FAILED: {fiber._error}", file=sys.stderr)
        raise SystemExit(1)
    return ctx, ctx.get("agent"), ctx.get("session")


# ---------------------------------------------------------------------------
# Scenario expectations
# ---------------------------------------------------------------------------


def final_assistant_text(session: Any) -> str:
    messages = [event.data["message"] for event in session.events_of("assistant/message")]
    return messages[-1].text if messages else ""


def turn_end_reason(session: Any) -> Any:
    event = session.find_last("turn/end")
    return event.data["reason"] if event else None


def seq_of(session: Any, type: str, predicate=None) -> int:
    for event in session.events:
        if event.type == type and (predicate is None or predicate(event.data)):
            return event.seq
    return -1


def check(scenario: str, ctx: Context, session: Any) -> list[str]:
    """Scenario-level smoke checks; returns a list of failures (empty = OK)."""
    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    text = final_assistant_text(session)
    reason = turn_end_reason(session)
    expect(reason is not None and reason.kind == "completed", f"turn ended completed (got {reason!r})")

    # every step boundary is paired, every tool call has a result
    expect(
        session.events_of("turn/start")
        and len(session.events_of("turn/start")) == len(session.events_of("turn/end")),
        "turn boundaries paired",
    )
    expect(
        len(session.events_of("step/start")) == len(session.events_of("step/end")),
        "step boundaries paired",
    )
    expect(
        len(session.events_of("tool/call")) == len(session.events_of("tool/result")),
        "every tool call has a result",
    )

    if scenario == "text":
        expect("4" in text, f"final text contains the answer (got {text!r})")
    if scenario == "tools":
        expect("Paris" in text and "Tokyo" in text, f"summary covers both cities (got {text!r})")
        expect(
            len(session.events_of("tool/call")) == 3,
            f"three tool calls (got {len(session.events_of('tool/call'))})",
        )
        calls = [event.data["name"] for event in session.events_of("tool/call")]
        expect(
            calls == ["set_note", "weather", "weather"],
            f"model-ordered calls (got {calls})",
        )
        # the exclusive tool committed before the parallel pair
        note_seq = seq_of(session, "tool/result", lambda d: "note saved" in _result_text(d))
        weather_seq = min(
            seq_of(session, "tool/result", lambda d: "Paris" in _result_text(d)),
            seq_of(session, "tool/result", lambda d: "Tokyo" in _result_text(d)),
        )
        expect(0 < note_seq < weather_seq, "exclusive barrier committed before the parallel pair")
    if scenario == "retry":
        expect("Recovered" in text, f"recovered text (got {text!r})")
        observed: list[str] = ctx.get("middleware-observed", strict=False) or []
        expect(any("retry" in line for line in observed), "request-error waterfall retried once")
        expect(
            len(session.events_of("assistant/message")) == 1,
            "only the successful attempt produced an assistant message",
        )
    if scenario == "steer":
        expect("Tokyo" in text, f"answer absorbed the steering (got {text!r})")
        steer_seq = seq_of(
            session, "user/message", lambda d: "also include Tokyo" in d["message"].text
        )
        step1_end = seq_of(session, "step/end")
        expect(steer_seq > step1_end, "steered message claimed at the next step boundary")

    return failures


def _result_text(data: dict[str, Any]) -> str:
    message = data["message"]
    block = message.content[0]
    from dsh_harness.contracts import TextBlock

    return "".join(b.text for b in block.content if isinstance(b, TextBlock))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_scenario(scenario: str, verbose: bool) -> bool:
    print(f"\n════ scenario: {scenario} " + "═" * 40)
    ctx, agent, session = await compose(scenario)
    if scenario == "steer":
        from dsh_harness.mock_llm import steer_hook

        ctx.get("llm").on_tool_call = steer_hook(agent)

    agent.followup(UserMessage.from_text(PROMPTS[scenario]))
    await agent.when_idle()

    observer = ctx.get("observer")
    observer.report(session)

    failures = check(scenario, ctx, session)
    if failures:
        for failure in failures:
            print(f"  ✗ FAIL: {failure}")
        return False
    print("  ✓ scenario OK")
    return True


async def main_async(args: argparse.Namespace) -> int:
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    results = {scenario: await run_scenario(scenario, args.verbose) for scenario in scenarios}
    failed = [scenario for scenario, ok in results.items() if not ok]
    print()
    for scenario, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {scenario}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("ALL SCENARIOS OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness demo (dsh-style loop on the Cordis plugin system)")
    parser.add_argument(
        "--scenario",
        choices=[*SCENARIOS, "all"],
        default="all",
        help="run one scenario (default: all)",
    )
    parser.add_argument("--verbose", action="store_true", help="reserved (verbose logging)")
    args = parser.parse_args()
    if args.scenario == "all":
        args.scenario = None
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
