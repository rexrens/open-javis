"""Thin dsh-style host for the mock demo.

Run from the repository root:
    .venv/bin/python -m examples.agentloop_demo.harness
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from examples.agentloop_demo.mock_dsh import DshRuntime
from examples.agentloop_demo.plugins.agents import AGENTS_SERVICE, AgentsService
from examples.agentloop_demo.plugins.session import SESSION_SERVICE, SessionStore

console = Console()

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"
WORKSPACE_ROOT = Path(__file__).resolve().parent

PROMPTS: tuple[str, ...] = (
    "请读取 README.md 并总结一下",
    "运行测试",
)


def _say(message: str) -> None:
    print(f"[harness] {message}")


async def main() -> int:
    async with DshRuntime(SETTINGS_PATH) as ctx:
        _say(f"mounted composition from {SETTINGS_PATH.name}")
        agents = ctx.get(AGENTS_SERVICE, AgentsService)
        handle = await agents.create(
            {"sessionId": "demo-session", "cwd": str(WORKSPACE_ROOT)}
        )

        for prompt in PROMPTS:
            _say(f"user: {prompt}")
            await handle.followup(prompt)
            await handle.when_idle()
            console.print(Panel(Markdown(handle.final_text), title="最终回答", border_style="green"))

        session = ctx.get(SESSION_SERVICE, SessionStore).get("demo-session")
        _say(f"session log ({len(session.events)} events)")
        for event in session.events:
            brief = str(event.data)[:120]
            _say(f"  #{event.seq:<2} {event.type:<18} {brief}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
