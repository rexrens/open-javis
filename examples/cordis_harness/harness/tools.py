"""The tool registry and execution pipeline (``ctx.tools``).

Tools declare a model-facing name, description and JSON-schema parameters.
Execution validates the model's arguments, consults the approval hook for
tools marked ``approval``, and turns every failure into an ordinary error
result so a bad call never ends the turn.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .types import ContentBlock, ToolSchema, text_block, tool_result_block

if TYPE_CHECKING:
    from javis.cordis import Context


@dataclass
class ToolOutcome:
    """A tool body's explicit result (a plain ``str`` means "ok, this text")."""

    text: str
    is_error: bool = False


@dataclass
class ToolDefinition:
    """One model-facing capability."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any], str], Any]
    approval: bool = False

    def schema(self) -> ToolSchema:
        return ToolSchema(name=self.name, description=self.description, parameters=self.parameters)


@dataclass
class ToolResult:
    """The finalized result of one tool invocation."""

    call_id: str
    name: str
    text: str
    is_error: bool = False

    def block(self) -> ContentBlock:
        return tool_result_block(self.call_id, [text_block(self.text)], self.is_error)


#: ``async (call_id, definition, arguments) -> bool``: may this call run?
Approver = Callable[[str, ToolDefinition, dict[str, Any]], Awaitable[bool]]


def validate_arguments(schema: dict[str, Any], value: Any, path: str = "") -> list[str]:
    """Validate ``value`` against the small JSON-schema subset tools declare."""
    errors: list[str] = []
    where = path or "arguments"
    expected = schema.get("type")

    def check_type(expected_type: str) -> bool:
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "null":
            return value is None
        return True

    if isinstance(expected, str) and not check_type(expected):
        return [f"{where}: expected {expected}"]
    if isinstance(expected, list):
        if not any(check_type(option) for option in expected):
            return [f"{where}: expected one of {expected}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: expected one of {schema['enum']!r}")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{where}.{name}: required")
        for name, item in value.items():
            if name in properties:
                errors.extend(validate_arguments(properties[name], item, f"{where}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}.{name}: unexpected property")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_arguments(schema["items"], item, f"{where}[{index}]"))

    return errors


class ToolsService:
    """Tool registry and guarded execution, installed as ``ctx.tools``."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self._tools: dict[str, ToolDefinition] = {}
        self._approver: Approver | None = None

    # -- registry -----------------------------------------------------------

    def register(self, definition: ToolDefinition) -> Callable[[], None]:
        if definition.name in self._tools:
            raise ValueError(f'tool "{definition.name}" is already registered')
        self._tools[definition.name] = definition

        def disposer() -> None:
            if self._tools.get(definition.name) is definition:
                del self._tools[definition.name]

        return disposer

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[ToolSchema]:
        return [definition.schema() for definition in self._tools.values()]

    def set_approver(self, approver: Approver | None) -> None:
        """Install the approval hook consulted for tools marked ``approval``."""
        self._approver = approver

    # -- execution ----------------------------------------------------------

    async def execute(self, call_id: str, name: str, raw_arguments: str, cwd: str = ".") -> ToolResult:
        """Run one model-requested call, never raising for caller mistakes."""
        definition = self._tools.get(name)
        if definition is None:
            return ToolResult(call_id, name, f'unknown tool "{name}"', is_error=True)

        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as error:
            return ToolResult(call_id, name, f"arguments are not valid JSON: {error}", is_error=True)
        if not isinstance(arguments, dict):
            return ToolResult(call_id, name, "arguments must be a JSON object", is_error=True)

        problems = validate_arguments(definition.parameters, arguments)
        if problems:
            return ToolResult(call_id, name, "invalid arguments:\n" + "\n".join(problems), is_error=True)

        if definition.approval and self._approver is not None:
            self.ctx.emit("tools/approval-request", call_id, definition, arguments)
            try:
                approved = await self._approver(call_id, definition, arguments)
            except Exception as error:  # noqa: BLE001 - a broken approver denies
                return ToolResult(call_id, name, f"approval failed: {error}", is_error=True)
            if not approved:
                return ToolResult(call_id, name, "the user denied this call", is_error=True)

        try:
            outcome = definition.execute(arguments, cwd)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - tool bugs become error results
            detail = traceback.format_exception_only(type(error), error)[-1].strip()
            return ToolResult(call_id, name, f"{definition.name} failed: {detail}", is_error=True)

        if isinstance(outcome, ToolOutcome):
            return ToolResult(call_id, name, outcome.text, outcome.is_error)
        if outcome is None:
            return ToolResult(call_id, name, "(no output)")
        if isinstance(outcome, str):
            return ToolResult(call_id, name, outcome)
        return ToolResult(call_id, name, json.dumps(outcome, ensure_ascii=False))
