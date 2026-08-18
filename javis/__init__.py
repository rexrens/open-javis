"""javis: a minimal TUI for driving a custom agent.

Standalone fork of the OpenHarness TUI, trimmed to the essentials: a
``QueryEngine`` driving an ``AgentBackend`` Protocol, a JSON-lines wire
protocol to the React/Ink frontend, and a slash-command registry. No MCP,
no tools, no hooks, no permissions subsystem — just the bridge.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
