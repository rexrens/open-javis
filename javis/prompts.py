"""System prompt builder for javis.

The backend does not consume the system prompt directly, but it is stored on
the engine and surfaced in the TUI status snapshot, so it should look plausible.
"""

from __future__ import annotations

from pathlib import Path


def build_javis_system_prompt(cwd: str | Path | None = None, *, workspace: str | Path | None = None) -> str:
    """Return a short system prompt for the agent."""
    del cwd, workspace  # signature kept for parity; stored on the engine
    return (
        "You are javis, an agent running on the javis TUI.\n\n"
        "You are backed by an ``AgentBackend`` implementation. Your responses "
        "stream through the React terminal frontend via the JSON-lines wire "
        "protocol."
    )


__all__ = ["build_javis_system_prompt"]
