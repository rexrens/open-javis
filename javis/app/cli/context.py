from dataclasses import dataclass

from .fake import FakeAgent


@dataclass
class Context:
    """One conversation session. Created dynamically; owns its agent.

    This is the single place handlers reach for per-session state. Add whatever a
    real session needs later — user, tool registry, model config, permissions — and
    it flows to every handler through ``ui.context`` without touching globals.
    """

    agent: FakeAgent
    session_id: str
