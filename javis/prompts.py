"""System prompt builder for javis.

The mock agent does not consume the system prompt, but it is stored on the
engine and surfaced in the TUI status snapshot, so it should look plausible.
"""

from __future__ import annotations

from pathlib import Path


def build_javis_system_prompt(cwd: str | Path | None = None, *, workspace: str | Path | None = None) -> str:
    """Return a short, honest system prompt for the mock agent."""
    del cwd, workspace  # unused — kept for parity with ohmo's signature
    return (
        "You are javis, a mock agent running on the OpenHarness TUI.\n\n"
        "You are a stand-in for a real agent backend. Your responses are canned "
        "and exist to exercise every TUI render path (text, tools, errors, "
        "status, permission modals, selectors).\n\n"
        "Try prompts containing: tool, error, status, permission, chinese."
    )


__all__ = ["build_javis_system_prompt"]
