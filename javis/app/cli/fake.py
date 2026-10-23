from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


# --------------------------------------------------------------------------- output types
@dataclass
class Event:
    """One typed chunk of a fake-agent turn.

    kind is one of: reasoning | tool | plan | diff | note | text.
    """

    kind: str
    payload: Any


PERSONAS = {
    "default": "a helpful assistant",
    "poet": "a rhyming poet",
    "pirate": "a pirate",
    "terse": "a person of few words",
}

DIFF = (
    "--- a/greet.py\n+++ b/greet.py\n@@ -1,2 +1,2 @@\n"
    "-def greet(name):\n-    return 'hi ' + name\n"
    "+def greet(name: str) -> str:\n+    return f'hi {name}'\n"
)


# --------------------------------------------------------------------------- FakeAgent
class FakeAgent:
    """A fake LLM agent. No network, no keys — fabricated multi-turn output.

    ``run()`` is an async generator: it appends the user message to ``history``,
    picks which output types this turn should show (keyword routing), and yields
    one :class:`Event` per step. Text is yielded as a single event whose words the
    handler streams — so the generator itself is the "stream".
    """

    def __init__(self, model: str = "fake-4o", persona: str = "default") -> None:
        self.model = model
        self.persona = persona
        self.history: list[dict[str, str]] = []  # multi-turn memory
        self.turn = 0
        self.tokens = 0  # display-only counter, bumped while streaming text

    async def run(self, prompt: str) -> AsyncIterator[Event]:
        self.turn += 1
        self.history.append({"role": "user", "content": prompt})
        p = prompt.strip().lower()

        yield Event("reasoning", f"turn {self.turn} · choosing output shapes for “{prompt}”")

        if "plan" in p:
            yield Event(
                "plan",
                [
                    ("understand the request", "completed"),
                    ("choose output types", "completed"),
                    ("stream the reply", "in_progress"),
                ],
            )

        if "diff" in p or "patch" in p:
            yield Event(
                "tool",
                {
                    "name": "apply_patch",
                    "args": {"path": "greet.py"},
                    "output": "applied: 2 insertions, 1 deletion",
                },
            )
            yield Event("diff", DIFF)
        elif "tool" in p or "run" in p or "test" in p:
            yield Event(
                "tool",
                {
                    "name": "shell",
                    "args": {"command": ["pytest", "-q"]},
                    "output": "......................\n54 passed in 0.12s",
                },
            )

        if "code" in p or "代码" in p:
            yield Event("note", "code sample appended below")

        text = self._compose(prompt)
        self.history.append({"role": "assistant", "content": text})
        yield Event("text", text)

    # -- reply shaping ---------------------------------------------------
    def _compose(self, prompt: str) -> str:
        """Canned, multi-turn-aware reply, flavored by persona."""
        prev = self.history[-3]["content"] if len(self.history) >= 3 else None
        p = prompt.strip().lower()

        lines = [f"Turn **{self.turn}**. You asked: “{prompt}”."]
        if prev:
            lines.append(f"(Still keeping in mind your earlier “{prev}”.)")

        if "code" in p or "代码" in p:
            lines.append(
                "\nHere's a tiny function:\n\n"
                "```python\n"
                "def greet(name: str) -> str:\n"
                "    return f'hi {name}'\n"
                "```"
            )

        if self.persona == "poet":
            lines.append(
                "\nA rhyme to close:\n*the stream flows on, word by word,*\n*each token a little bird.*"
            )
        elif self.persona == "pirate":
            lines.append("\nArrr, that be me answer, matey.")
        elif self.persona == "terse":
            lines.append("\nDone.")
        else:
            lines.append("\nThat's a fully fabricated answer — no real model was called.")

        return "\n".join(lines)


__all__ = ["Event", "FakeAgent"]
