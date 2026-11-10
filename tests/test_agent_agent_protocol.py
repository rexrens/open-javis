from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from javis.agent.agent.protocol import (
    EventType,
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

EVENT_CASES: list[tuple[type[Any], str, dict[str, Any]]] = [
    (TurnStartEvent, "turn-start", {"seq": 7, "data": {"turn": 1, "text": "hello"}}),
    (TurnEndEvent, "turn-end", {"seq": 7}),
    (StepStartEvent, "step-start", {"seq": 1}),
    (StepEndEvent, "step-end", {"seq": 1}),
    (
        ToolCallStartEvent,
        "toolcall-start",
        {"data": {"id": "call_1", "name": "echo", "arguments": {}}},
    ),
    (
        ToolCallEndEvent,
        "toolcall-end",
        {"data": {"id": "call_1", "name": "echo", "output": "hi"}},
    ),
    (
        ToolCallErrorEvent,
        "toolcall-error",
        {"data": {"id": "call_1", "name": "echo", "error": "failed"}},
    ),
    (ReasoningStartEvent, "reasoning-start", {}),
    (ReasoningEndEvent, "reasoning-end", {}),
    (ReasoningDeltaEvent, "reasoning-delta", {"data": {"text": "thinking"}}),
    (TextDeltaEvent, "text-delta", {"data": {"text": "hello"}}),
]


def test_event_type_literal_contains_every_wire_value() -> None:
    assert set(get_args(EventType.__value__)) == {case[1] for case in EVENT_CASES}


@pytest.mark.parametrize(("event_class", "event_type", "fields"), EVENT_CASES)
def test_session_event_discriminates_and_round_trips(
    event_class: type[Any],
    event_type: str,
    fields: dict[str, Any],
) -> None:
    event = event_class(**fields)
    payload: Any = event.model_dump(mode="json")

    restored = TypeAdapter(SessionEvent).validate_python(payload)

    assert event.type == event_type
    assert isinstance(restored, event_class)
    assert restored.model_dump(mode="json") == payload


def test_events_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TextDeltaEvent(data={"text": "hello"}, unexpected=True)
