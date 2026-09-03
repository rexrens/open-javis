#!/usr/bin/env python
"""mini_dsh 的 standalone 驱动（无 javis 宿主，仅 javis.cordis）。

    uv run python examples/mini_dsh/cli.py                 # 全部 7 个 demo 场景
    uv run python examples/mini_dsh/cli.py --scenario tools
    uv run python examples/mini_dsh/cli.py --prompt "2+2"  # 真实模型（有 API key）
    uv run python examples/mini_dsh/cli.py --repl          # 交互 REPL
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_COMPOSITION = _HERE / "cordis.yml"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from core import types as t
from providers import SCENARIOS

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader
from javis.cordis.registry import settle

PROMPTS: dict[str, str] = {
    "text": "what is 2+2?",
    "tools": "save a note and check two cities",
    "retry": "say something",
    "steer": "what time is it?",
    "skills": "save a note about autumn",
    "instructions": "what is your policy?",
    "compaction": "read the big file",
}

#: run_demo 期间写入的环境变量（循环后恢复，不污染调用方/测试进程）。
_ENV_KEYS = ("MINI_DSH_CWD", "HARNESS_DEMO_SCENARIO")


async def _compose(scenario: str, *, cwd: str | None = None) -> Context:
    ctx = Context()
    ctx.baseUrl = cwd or str(_HERE)
    loader_fiber = ctx.plugin(Loader, {"file": str(_COMPOSITION)})
    await loader_fiber
    await settle(ctx)
    failed = [f for f in _all_fibers(ctx) if f.state == FiberState.FAILED]
    if failed:
        raise RuntimeError(f"plugin load failed: {failed[0]._error}")
    return ctx


def _all_fibers(ctx: Context) -> list[Any]:
    return [fiber for runtime in ctx.registry.values() for fiber in list(runtime.fibers)]


def _attach_steer_hook(ctx: Context) -> None:
    """steer 场景：llm 即将发出 tool-call 时注入纠正消息（ScriptedAdapter 钩子）。"""
    ctx.get("llm").on_tool_call = lambda: ctx.get("agent").steer(
        t.UserMessage.from_text("also include Tokyo's weather in your answer")
    )


async def _run_turn(ctx: Context, prompt: str) -> None:
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


async def run_demo_async(scenario: str | None = None) -> int:
    """跑 1 个或全部 demo 场景，每个带断言；失败抛 AssertionError。"""
    names = [scenario] if scenario else list(SCENARIOS)
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        for name in names:
            workspace = tempfile.mkdtemp(prefix=f"mini-dsh-{name}-")
            if name == "instructions":
                _seed_workspace(workspace)  # 拷入 fixtures/AGENTS.md
            os.environ["MINI_DSH_CWD"] = workspace
            os.environ["HARNESS_DEMO_SCENARIO"] = name  # llm 插件 apply 时读
            print(f"[mini-dsh] running scenario {name} ...", file=sys.stderr)
            try:
                ctx = await _compose(name, cwd=workspace)
                session = ctx.get("session")
                if name == "steer":
                    _attach_steer_hook(ctx)
                await _run_turn(ctx, PROMPTS[name])
                _assert_scenario(name, ctx, session)
            except BaseException:  # —— 原样重抛，仅补场景名上下文
                print(f"[mini-dsh] scenario {name} FAILED", file=sys.stderr)
                raise
            print(f"[mini-dsh] scenario {name}: OK", file=sys.stderr)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return 0


def _seed_workspace(workspace: str) -> None:
    from shutil import copyfile

    copyfile(_HERE / "fixtures" / "AGENTS.md", Path(workspace) / "AGENTS.md")


def _tool_result_text(event: Any) -> str:
    """tool/result 消息的文本：块在 content[0]（ToolResultBlock）的 content 里。"""
    block = event.data["message"].content[0]
    return "".join(getattr(b, "text", "") for b in block.content)


def _assert_scenario(name: str, ctx: Context, session: Any) -> None:
    """每场景 2–4 条断言（语义验证，不全量）。"""
    if name == "text":
        msgs = session.derive_messages()
        assert any("2 + 2 = 4" in getattr(m, "text", "") for m in msgs)
    elif name == "tools":
        results = [e for e in session.events_of("tool/result")]
        assert len(results) == 3
        names = [e.data["name"] for e in session.events_of("tool/call")]
        assert names == ["set_note", "weather", "weather"]
    elif name == "retry":
        msgs = session.derive_messages()
        assert any("Recovered" in getattr(m, "text", "") for m in msgs)
        assert len(session.events_of("assistant/message")) == 1
        observed = ctx.get("middleware-observed", strict=False) or []
        assert any("request-error: retry" in line for line in observed)
    elif name == "steer":
        msgs = session.derive_messages()
        assert any("Tokyo" in getattr(m, "text", "") for m in msgs)
    elif name == "skills":
        msgs = session.derive_messages()
        assert any("autumn leaves" in getattr(m, "text", "") for m in msgs)
        assert any(
            "available_skills" in (getattr(e.data.get("message"), "text", "") or "")
            for e in session.events_of("user/message")
        )
    elif name == "instructions":
        baseline = [
            e for e in session.events_of("user/message")
            if (getattr(e.data.get("message"), "source", None) or {}).get("kind") == "agent-instructions"
        ]
        assert baseline
        msgs = session.derive_messages()
        assert any("Keeping it brief" in getattr(m, "text", "") for m in msgs)
    elif name == "compaction":
        results = [_tool_result_text(e) for e in session.events_of("tool/result")]
        assert any("truncated by compaction" in text for text in results)
        assert len(session.events_of("compaction/start")) == 1
        msgs = session.derive_messages()
        assert any(getattr(m, "text", "").startswith("Earlier context (compacted):") for m in msgs)


def run_demo(scenario: str | None = None) -> int:
    """同步入口（pytest 与 main 共用）。"""
    return asyncio.run(run_demo_async(scenario))


async def _run_prompt(prompt: str) -> int:
    os.environ.setdefault("MINI_DSH_PROVIDER", "auto")  # 有 key 走真实模型
    ctx = await _compose("text")
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()
    # 打印 assistant 文本
    session = ctx.get("session")
    for message in session.derive_messages():
        if getattr(message, "role", "") == "assistant":
            print(message.text or "")
    return 0


async def _run_repl() -> int:
    ctx = await _compose("text")
    agent = ctx.get("agent")
    print("mini-dsh REPL — type a message, /exit to quit", file=sys.stderr)
    while True:
        try:
            line = input("mini> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        agent.followup(t.UserMessage.from_text(line))
        await agent.when_idle()
        session = ctx.get("session")
        for message in session.derive_messages():
            if getattr(message, "role", "") == "assistant" and message.text:
                print(message.text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mini-dsh", description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), help="run one demo scenario")
    parser.add_argument("--prompt", help="run one real-model prompt and exit")
    parser.add_argument("--repl", action="store_true", help="interactive REPL")
    args = parser.parse_args(argv)
    if args.prompt:
        return asyncio.run(_run_prompt(args.prompt))
    if args.repl:
        return asyncio.run(_run_repl())
    return run_demo(args.scenario)


if __name__ == "__main__":
    raise SystemExit(main())
