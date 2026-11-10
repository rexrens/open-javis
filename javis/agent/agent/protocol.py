from __future__ import annotations

from time import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type JsonScalar = int | float | str | bool
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


type EventType = Literal[
    "turn-start",
    "turn-end",
    "step-start",
    "step-end",

    "text-delta",

    "reasoning-start",
    "reasoning-end",
    "reasoning-delta",

    "toolcall-start",
    "toolcall-end",
    "toolcall-error",
]


class BaseEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    create_at: int = Field(default_factory=lambda: int(time()))

    type: EventType


class _SessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnStartEvent(_SessionEvent):
    type: Literal["turn-start"] = "turn-start"
    seq: int = Field(ge=0)
    data: JsonObject = Field(default_factory=dict)


class TurnEndEvent(_SessionEvent):
    type: Literal["turn-end"] = "turn-end"
    seq: int = Field(ge=0)


class StepStartEvent(_SessionEvent):
    type: Literal["step-start"] = "step-start"
    seq: int = Field(ge=0)


class StepEndEvent(_SessionEvent):
    type: Literal["step-end"] = "step-end"
    seq: int = Field(ge=0)

class ToolCallStartEvent(_SessionEvent):
    type: Literal["toolcall-start"] = "toolcall-start"
    data: JsonObject = Field(default_factory=dict)


class ToolCallEndEvent(_SessionEvent):
    type: Literal["toolcall-end"] = "toolcall-end"
    data: JsonObject = Field(default_factory=dict)


class ToolCallErrorEvent(_SessionEvent):
    type: Literal["toolcall-error"] = "toolcall-error"
    data: JsonObject = Field(default_factory=dict)


class ReasoningStartEvent(_SessionEvent):
    type: Literal["reasoning-start"] = "reasoning-start"


class ReasoningEndEvent(_SessionEvent):
    type: Literal["reasoning-end"] = "reasoning-end"


class ReasoningDeltaEvent(_SessionEvent):
    type: Literal["reasoning-delta"] = "reasoning-delta"
    data: JsonObject = Field(default_factory=dict)


class TextDeltaEvent(_SessionEvent):
    type: Literal["text-delta"] = "text-delta"
    data: JsonObject = Field(default_factory=dict)


SessionEvent = Annotated[
    TurnStartEvent
    | TurnEndEvent
    | StepStartEvent
    | StepEndEvent
    | ToolCallStartEvent
    | ToolCallEndEvent
    | ToolCallErrorEvent
    | ReasoningStartEvent
    | ReasoningEndEvent
    | ReasoningDeltaEvent
    | TextDeltaEvent,
    Field(discriminator="type"),
]

__all__ = [
    "BaseEvent",
    "EventType",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "ReasoningDeltaEvent",
    "ReasoningEndEvent",
    "ReasoningStartEvent",
    "SessionEvent",
    "StepEndEvent",
    "StepStartEvent",
    "TextDeltaEvent",
    "ToolCallEndEvent",
    "ToolCallErrorEvent",
    "ToolCallStartEvent",
    "TurnEndEvent",
    "TurnStartEvent",
]
