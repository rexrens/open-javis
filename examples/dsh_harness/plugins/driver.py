"""Plugin: the composition root — session + agent (services ``"session"`` / ``"agent"``).

Everything the agent needs arrives via ``inject`` (dependency-driven load
order, decided by Cordis, not mount order):

    llm · tools · systemPrompt · agentLoop

The driver never constructs the engine's parts directly — it composes them
from the context, exactly like dsh's runtime builds a ``ReactLoopAgent``
over its ``Context`` services.
"""


import os as _os
import uuid

from javis.dsh.agent import ReactLoopAgent
from javis.dsh.contracts import AgentOptions
from javis.dsh.session import Session

name = "driver"

#: Service dependencies — the fiber stays PENDING until every one is ACTIVE.
inject = ["llm", "tools", "systemPrompt", "agentLoop"]


def apply(ctx):
    session = Session(f"demo-{uuid.uuid4().hex[:8]}", cwd=_os.getcwd())
    ctx.provide("session", session)
    agent = ReactLoopAgent(
        ctx,
        session.id,
        AgentOptions(provider="mock", model="mock-mini"),
        session,
    )
    ctx.provide("agent", agent)
