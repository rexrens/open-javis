"""Plugin: system-prompt service (dsh ``ctx.systemPrompt``).

``assemble()`` returns a :class:`~javis.dsh.contracts.PromptAssembly` — the
ordered sections **plus the tool schemas** (dsh: tools are part of the
prompt assembly). ``render_prompt`` renders the ``persona`` sections into
the system slot; ``render_context`` renders the ``context`` sections into
the step-boundary context message that the ``agent/pre-step`` default
injects.
"""


from javis.dsh.contracts import PromptAssembly, PromptSection

name = "system-prompt"

SECTIONS: tuple[PromptSection, ...] = (
    PromptSection(
        title="Persona",
        body=(
            "You are Javis-Demo, a compact assistant harness demo. Answer "
            "concisely. When a task needs facts (time, weather), call the "
            "available tools instead of guessing."
        ),
    ),
    PromptSection(
        title="Tool usage",
        body=(
            "Tools run in model order; exclusive tools form barriers. Tool "
            "results are returned as text blocks."
        ),
    ),
    PromptSection(
        title="Session context",
        body="workspace=open-javis ; date=2026-08-31 ; provider=mock",
        kind="context",
    ),
)


class SystemPromptService:
    def __init__(self, ctx, sections: tuple[PromptSection, ...] = SECTIONS) -> None:
        self._ctx = ctx
        self.sections = sections

    def assemble(self, *, agent=None, signal=None) -> PromptAssembly:
        """The assembly for one request: sections + the live tool schemas."""
        registry = self._ctx.get("tools")
        return PromptAssembly(sections=self.sections, tools=tuple(registry.schemas()))

    def render_prompt(self, assembly: PromptAssembly) -> str:
        parts = [f"# {section.title}\n{section.body}" for section in assembly.sections if section.kind == "persona"]
        return "\n\n".join(parts)

    def render_context(self, assembly: PromptAssembly) -> str:
        parts = [f"[{section.title}] {section.body}" for section in assembly.sections if section.kind == "context"]
        return " ; ".join(parts)


def apply(ctx):
    ctx.provide("systemPrompt", SystemPromptService(ctx))
