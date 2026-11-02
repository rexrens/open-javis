"""Harness composition rows — the only place the harness is assembled.

Each module is a Cordis plugin addressing one service (module name == service
name, except ``snip`` which only registers a ``tools/post-execute`` listener):

- ``llm``            → ``llm`` (``javis.llm.LlmRuntime`` + provider adapter)
- ``agent_tools``    → ``agentTools`` (live view over the host ``tools``)
- ``system_prompt``  → ``systemPrompt``
- ``agent_loop``     → ``agentLoop`` (loop config, incl. history compression)
- ``snip``           → tool-output truncation middleware (no service)
- ``harness``        → ``harness`` (Session + AgentLoop + Harness shell)

Rows declaring a ``Config`` model (``agent_loop`` and ``snip``) are called as
``apply(ctx, config)``; the other rows declare ``apply(ctx)`` and are called
without config, so a YAML ``config:`` block on those rows would be silently
dropped.

Rows declare their dependencies with module-level ``inject``; the Cordis
loader activates them only once every listed service is ACTIVE.
"""
