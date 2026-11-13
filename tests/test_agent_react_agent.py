from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from javis.agent.agent.agent import BaseReactAgent
from javis.agent.agent.protocol import (
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    ReasoningStartEvent,
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
from javis.agent.model.proto import ToolCallBlock, ToolResultBlock
from javis.agent.model.request import Message, ModelRequest
from javis.agent.model.response import ModelResponse
from javis.agent.tools.base import BaseTool, ToolCallResult, ToolExecutionContext, ToolRegister
from javis.harness.types import (
    FinishChunk,
    ReasoningDeltaChunk,
    StopFinish,
    StreamChunk,
    TextDeltaChunk,
    TokenUsage,
    ToolCallDeltaChunk,
    ToolCallsFinish,
    UsageChunk,
)


class ScriptedModel(Model):
    def __init__(self, rounds: list[list[StreamChunk]]) -> None:
        super().__init__(model="scripted")
        self.rounds = rounds
        self.requests: list[ModelRequest] = []

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise NotImplementedError

    async def ainvoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[StreamChunk]:
        raise NotImplementedError

    async def ainvoke_stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(request)
        for chunk in self.rounds.pop(0):
            yield chunk


class EchoArgs(BaseModel):
    text: str


class EmptyArgs(BaseModel):
    pass


class EchoTool(BaseTool):
    name = "echo"
    description = "Return the supplied text."
    args_schema = EchoArgs

    def __init__(self) -> None:
        self.contexts: list[ToolExecutionContext] = []

    async def _run(
        self,
        context: ToolExecutionContext,
        args: BaseModel | None,
    ) -> ToolCallResult:
        assert isinstance(args, EchoArgs)
        self.contexts.append(context)
        return ToolCallResult(output=args.text)


class ExplodingTool(BaseTool):
    name = "explode"
    description = "Raise an exception."
    args_schema = EmptyArgs

    async def _run(
        self,
        context: ToolExecutionContext,
        args: BaseModel | None,
    ) -> ToolCallResult:
        raise RuntimeError("tool exploded")


class ErrorResultTool(BaseTool):
    name = "fail"
    description = "Return an error result."
    args_schema = EmptyArgs

    async def _run(
        self,
        context: ToolExecutionContext,
        args: BaseModel | None,
    ) -> ToolCallResult:
        return ToolCallResult(output="invalid input", is_error=True)


@pytest.mark.asyncio
async def test_run_without_tool_calls_uses_one_step_and_records_history() -> None:
    model = ScriptedModel(
        [
            [
                TextDeltaChunk(index=0, text="hello"),
                FinishChunk(reason=StopFinish()),
            ]
        ]
    )
    agent = BaseReactAgent(model=model, tools=ToolRegister())

    events = [event async for event in agent.run("hi")]

    assert [type(event) for event in events] == [
        TurnStartEvent,
        StepStartEvent,
        TextDeltaEvent,
        StepEndEvent,
        TurnEndEvent,
    ]
    assert events[0].data == {"turn": 1, "prompt": "hi"}
    assert events[2].data == {"text": "hello"}
    assert model.requests[0].messages == [Message.user("hi")]
    assert agent.history == [Message.user("hi"), Message.assistant("hello")]


@pytest.mark.asyncio
async def test_run_executes_tool_and_uses_another_step_for_final_answer() -> None:
    model = ScriptedModel(
        [
            [
                TextDeltaChunk(index=0, text="checking"),
                ToolCallDeltaChunk(index=0, id="call_1", name="echo", arguments_delta='{"text":'),
                ToolCallDeltaChunk(index=0, id="", arguments_delta='"hi"}'),
                FinishChunk(reason=ToolCallsFinish()),
            ],
            [
                TextDeltaChunk(index=0, text="done"),
                FinishChunk(reason=StopFinish()),
            ],
        ]
    )
    tool = EchoTool()
    tools = ToolRegister()
    tools.register(tool)
    agent = BaseReactAgent(model=model, tools=tools)

    events = [event async for event in agent.run("use echo")]

    assert [type(event) for event in events] == [
        TurnStartEvent,
        StepStartEvent,
        TextDeltaEvent,
        ToolCallStartEvent,
        ToolCallEndEvent,
        StepEndEvent,
        StepStartEvent,
        TextDeltaEvent,
        StepEndEvent,
        TurnEndEvent,
    ]
    assert events[3].data == {
        "id": "call_1",
        "name": "echo",
        "arguments": {"text": "hi"},
    }
    assert events[4].data == {
        "id": "call_1",
        "name": "echo",
        "output": "hi",
        "is_error": False,
    }
    assert tool.contexts[0].cwd == Path.cwd()
    assert model.requests[0].tools == [tool.to_openai_tool()]
    assert isinstance(agent.history[1].content[1], ToolCallBlock)
    assert model.requests[1].messages == agent.history[:3]


@pytest.mark.asyncio
async def test_run_maps_reasoning_and_accumulates_token_usage() -> None:
    model = ScriptedModel(
        [
            [
                ReasoningDeltaChunk(index=0, text="think"),
                TextDeltaChunk(index=1, text="answer"),
                UsageChunk(usage=TokenUsage(input_tokens=3, output_tokens=2)),
                FinishChunk(reason=StopFinish()),
            ]
        ]
    )
    agent = BaseReactAgent(model=model, tools=ToolRegister())

    events = [event async for event in agent.run("hi")]

    assert [type(event) for event in events] == [
        TurnStartEvent,
        StepStartEvent,
        ReasoningStartEvent,
        ReasoningDeltaEvent,
        ReasoningEndEvent,
        TextDeltaEvent,
        StepEndEvent,
        TurnEndEvent,
    ]
    assert events[3].data == {"text": "think"}
    assert agent.tokens == 5


@pytest.mark.parametrize(
    ("tool", "expected_error"),
    [
        (ExplodingTool(), "RuntimeError: tool exploded"),
        (ErrorResultTool(), "invalid input"),
    ],
)
@pytest.mark.asyncio
async def test_run_reports_tool_error_and_continues_to_next_step(
    tool: BaseTool,
    expected_error: str,
) -> None:
    model = ScriptedModel(
        [
            [
                ToolCallDeltaChunk(
                    index=0,
                    id="call_1",
                    name=tool.name,
                    arguments_delta="{}",
                ),
                FinishChunk(reason=ToolCallsFinish()),
            ],
            [
                TextDeltaChunk(index=0, text="recovered"),
                FinishChunk(reason=StopFinish()),
            ],
        ]
    )
    tools = ToolRegister()
    tools.register(tool)
    agent = BaseReactAgent(model=model, tools=tools)

    events = [event async for event in agent.run("fail once")]

    error_event = next(event for event in events if isinstance(event, ToolCallErrorEvent))
    assert error_event.data["error"] == expected_error
    tool_result = agent.history[2].content[0]
    assert isinstance(tool_result, ToolResultBlock)
    assert tool_result.tool_call_id == "call_1"
    assert tool_result.content == expected_error
    assert tool_result.is_error is True
    assert isinstance(events[-1], TurnEndEvent)
