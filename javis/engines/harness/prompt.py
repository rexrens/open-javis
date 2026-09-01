"""System-prompt service for the harness engine.

Mirrors the demo's ``system_prompt`` plugin but sources its persona from the
javis runtime instead of a fixed string: the runtime builds the system prompt
(``javis.app.runtime.build_system_prompt``, CLI overrides included) and hands
it to the engine; this service wraps it in a ``persona`` section plus a live
``context`` section (cwd / workspace / session id / date) that the
``agent/pre-step`` default injects at every step boundary.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from javis.dsh.contracts import PromptAssembly, PromptSection, ToolSchema


class HarnessPromptService:
    def __init__(
        self,
        ctx: Any,
        system_prompt: str,
        *,
        cwd: str,
        workspace: str,
        session_id: str,
    ) -> None:
        self._ctx = ctx
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._workspace = workspace
        self._session_id = session_id

    def set_system_prompt(self, prompt: str) -> None:
        """Host setter (``AgentEngine.set_system_prompt``)."""
        self._system_prompt = prompt

    # -- dsh systemPrompt service surface -----------------------------------

    def assemble(self, *, agent: Any = None, signal: Any = None) -> PromptAssembly:
        """One request's assembly: persona + context sections + live tools."""
        registry = self._ctx.get("tools")
        tools: tuple[ToolSchema, ...] = ()
        schemas = getattr(registry, "schemas", None)
        if callable(schemas):
            tools = tuple(schemas())
        sections = (
            PromptSection(title="Persona", body=self._system_prompt, kind="persona"),
            PromptSection(
                title="Session context",
                body=(
                    f"cwd={self._cwd} ; workspace={self._workspace} ; "
                    f"session={self._session_id} ; date={_dt.datetime.now(_dt.UTC).date().isoformat()}"
                ),
                kind="context",
            ),
        )
        return PromptAssembly(sections=sections, tools=tools)

    def render_prompt(self, assembly: PromptAssembly) -> str:
        """Persona sections → the system slot of the request."""
        parts = [
            f"# {section.title}\n{section.body}"
            for section in assembly.sections
            if section.kind == "persona"
        ]
        return "\n\n".join(parts)

    def render_context(self, assembly: PromptAssembly) -> str:
        """Context sections → the step-boundary context message."""
        parts = [
            f"[{section.title}] {section.body}"
            for section in assembly.sections
            if section.kind == "context"
        ]
        return " ; ".join(parts)


__all__ = ["HarnessPromptService"]
