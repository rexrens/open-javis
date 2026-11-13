import json
from collections.abc import AsyncIterator
from pathlib import Path

from javis.agent.agent.protocol import (
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
    SessionEvent,
    StepEndEvent,
    StepStartEvent,
    TextDeltaEvent,
    ToolCallEndEvent,
    ToolCallErrorEvent,
    ToolCallStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from javis.agent.model.model import Model
from javis.agent.model.proto import ContentBlock, TextBlock, ToolCallBlock, ToolResultBlock
from javis.agent.model.request import Message, ModelRequest
from javis.agent.tools.base import ToolExecutionContext, ToolRegister
from javis.harness.types import (
    ReasoningDeltaChunk,
    TextDeltaChunk,
    ToolCallDeltaChunk,
    UsageChunk,
)


class BaseReactAgent:
    """A ReAct agent backed by a streaming model.

    ``run()`` consumes ``Model.ainvoke_stream()`` and forwards each model delta as a
    session event. A turn contains one or more steps; tool calls are executed and
    appended to history before the next model step starts.
    """

    def __init__(
        self,
        model: Model,
        tools: ToolRegister,
        persona: str = "default",
        max_steps: int = 50,
    ) -> None:
        self.tokens = 0
        self.model = model
        self.persona = persona
        self.history: list[Message] = []
        self.turn = 0
        self.tools = tools
        self.max_steps = max_steps
        self._seq = 0
        self._cwd = Path.cwd()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def run(self, prompt: str) -> AsyncIterator[SessionEvent]:
        self.turn += 1
        step = 0
        self.history.append(Message.user(prompt))

        yield TurnStartEvent(
            seq=self._next_seq(),
            data={"turn": self.turn, "prompt": prompt},
        )

        for _step in range(1, self.max_steps + 1):
            step += 1
            yield StepStartEvent(
                seq = step
            )

            text_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            reasoning_open = False
            request = ModelRequest(messages=self.history, tools=self.tools.schemas())
            async for chunk in self.model.ainvoke_stream(request):
                if isinstance(chunk, ReasoningDeltaChunk):
                    if not reasoning_open:
                        yield ReasoningStartEvent()
                        reasoning_open = True
                    yield ReasoningDeltaEvent(data={"text": chunk.text})
                elif isinstance(chunk, TextDeltaChunk):
                    if reasoning_open:
                        yield ReasoningEndEvent()
                        reasoning_open = False
                    text_parts.append(chunk.text)
                    yield TextDeltaEvent(data={"text": chunk.text})
                elif isinstance(chunk, ToolCallDeltaChunk):
                    #tool_calls = _parse_tool_call(chunk)
                    call = tool_calls.setdefault(
                        chunk.index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    call["id"] = chunk.id or call["id"]
                    call["name"] = chunk.name or call["name"]
                    call["arguments"] += chunk.arguments_delta
                elif isinstance(chunk, UsageChunk):
                    self.tokens += chunk.usage.input_tokens + chunk.usage.output_tokens

            if reasoning_open:
                yield ReasoningEndEvent()

            if tool_calls:
                content: list[ContentBlock] = []
                text = "".join(text_parts)
                if text:
                    content.append(TextBlock(text=text))
                for call in tool_calls.values():
                    content.append(
                        ToolCallBlock(
                            id=call["id"],
                            name=call["name"],
                            arguments=call["arguments"] or "{}",
                        )
                    )
                self.history.append(Message(role="assistant", content=content))

                for call in tool_calls.values():
                    async for event in self._run_tool(call):
                        yield event

                yield StepEndEvent(
                    seq=step
                )
                continue

            self.history.append(Message.assistant("".join(text_parts)))
            yield StepEndEvent(
                seq=step
            )
            yield TurnEndEvent(seq=self._next_seq())
            return

        yield TurnEndEvent(seq=self._next_seq())

    async def _run_tool(self, call: dict[str, str]) -> AsyncIterator[SessionEvent]:
        call_id = call["id"]
        name = call["name"]
        raw_arguments = call["arguments"] or "{}"

        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be a JSON object")
        except (json.JSONDecodeError, TypeError) as parse_error:
            yield ToolCallErrorEvent(
                data={
                    "id": call_id,
                    "name": name,
                    "error": str(parse_error),
                    "arguments": raw_arguments,
                }
            )
            self._append_tool_result(call_id, str(parse_error), is_error=True)
            return

        yield ToolCallStartEvent(
            data={"id": call_id, "name": name, "arguments": arguments}
        )

        tool = self.tools.get(name)
        if tool is None:
            tool_error = f"unknown tool: {name}"
            yield ToolCallErrorEvent(
                data={"id": call_id, "name": name, "error": tool_error}
            )
            self._append_tool_result(call_id, tool_error, is_error=True)
            return

        try:
            result = await tool(
                ToolExecutionContext(cwd=self._cwd),
                arguments,
            )
        except Exception as tool_error:  # noqa: BLE001 - tool boundary normalizes failures
            yield ToolCallErrorEvent(
                data={"id": call_id, "name": name, "error": str(tool_error)}
            )
            self._append_tool_result(call_id, str(tool_error), is_error=True)
            return

        if result.is_error:
            yield ToolCallErrorEvent(
                data={"id": call_id, "name": name, "error": result.output}
            )
        else:
            yield ToolCallEndEvent(
                data={
                    "id": call_id,
                    "name": name,
                    "output": result.output,
                    "is_error": False,
                }
            )
        self._append_tool_result(call_id, result.output, is_error=result.is_error)

    def _append_tool_result(self, call_id: str, output: str, *, is_error: bool) -> None:
        self.history.append(
            Message(
                role="tool",
                content=[
                    ToolResultBlock(
                        tool_call_id=call_id,
                        content=output,
                        is_error=is_error,
                    )
                ],
            )
        )
