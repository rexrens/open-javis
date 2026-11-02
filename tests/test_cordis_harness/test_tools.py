"""Tool registry, validation, approval and the built-in tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness import builtins
from harness.tools import ToolDefinition, ToolsService, validate_arguments
from cordis_harness_support.stub import StubContext


def _service() -> ToolsService:
    return ToolsService(StubContext())


def _echo() -> ToolDefinition:
    async def execute(args, cwd):
        return f"{args['text']} @ {cwd}"

    return ToolDefinition(
        name="echo",
        description="echo the text back",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}, "times": {"type": "integer"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def test_validate_arguments_covers_the_declared_subset():
    schema = _echo().parameters
    assert validate_arguments(schema, {"text": "hi"}) == []
    assert validate_arguments(schema, {}) == ["arguments.text: required"]
    assert validate_arguments(schema, {"text": 1}) == ["arguments.text: expected string"]
    assert validate_arguments(schema, {"text": "hi", "times": True}) == ["arguments.times: expected integer"]
    assert validate_arguments(schema, {"text": "hi", "extra": 1}) == ["arguments.extra: unexpected property"]
    assert validate_arguments(schema, "nope") == ["arguments: expected object"]
    assert validate_arguments({"type": "array", "items": {"type": "string"}}, ["a", 2]) == [
        "arguments[1]: expected string"
    ]


async def test_execute_returns_results_for_happy_path_and_caller_mistakes():
    tools = _service()
    tools.register(_echo())
    assert [schema.name for schema in tools.schemas()] == ["echo"]

    ok = await tools.execute("c1", "echo", '{"text": "hi"}', cwd="/work")
    assert (ok.text, ok.is_error) == ("hi @ /work", False)

    unknown = await tools.execute("c2", "nope", "{}")
    assert unknown.is_error and "unknown tool" in unknown.text

    bad_json = await tools.execute("c3", "echo", "{oops")
    assert bad_json.is_error and "not valid JSON" in bad_json.text

    not_an_object = await tools.execute("c4", "echo", "[1, 2]")
    assert not_an_object.is_error and "must be a JSON object" in not_an_object.text

    invalid = await tools.execute("c5", "echo", "{}")
    assert invalid.is_error and "required" in invalid.text


async def test_tool_bodies_may_return_structures_and_errors():
    tools = _service()

    def structured(args, cwd):
        return {"ok": True}

    def broken(args, cwd):
        raise RuntimeError("tool bug")

    tools.register(ToolDefinition("structured", "d", {"type": "object"}, structured))
    tools.register(ToolDefinition("broken", "d", {"type": "object"}, broken))
    assert (await tools.execute("c1", "structured", "{}")).text == '{"ok": true}'
    assert "tool bug" in (await tools.execute("c2", "broken", "{}")).text
    empty = tools.register(ToolDefinition("nothing", "d", {"type": "object"}, lambda args, cwd: None))
    assert (await tools.execute("c3", "nothing", "{}")).text == "(no output)"
    empty()
    assert tools.names() == ["structured", "broken"]


async def test_approval_is_only_asked_for_flagged_tools():
    tools = _service()
    asked: list[str] = []

    async def approver(call_id, definition, arguments):
        asked.append(definition.name)
        return False

    tools.register(_echo())
    tools.register(ToolDefinition("danger", "d", {"type": "object"}, lambda args, cwd: "ran", approval=True))
    tools.set_approver(approver)

    assert (await tools.execute("c1", "echo", '{"text":"hi"}')).is_error is False
    denied = await tools.execute("c2", "danger", "{}")
    assert asked == ["danger"]
    assert denied.is_error and "denied" in denied.text
    assert tools.ctx.names() == ["tools/approval-request"]

    tools.set_approver(None)
    assert (await tools.execute("c3", "danger", "{}")).text == "ran"


async def test_broken_approver_denies_instead_of_crashing():
    tools = _service()

    async def approver(call_id, definition, arguments):
        raise RuntimeError("no tty")

    tools.register(ToolDefinition("danger", "d", {"type": "object"}, lambda args, cwd: "ran", approval=True))
    tools.set_approver(approver)
    result = await tools.execute("c1", "danger", "{}")
    assert result.is_error and "approval failed" in result.text


def test_duplicate_registration_is_rejected():
    tools = _service()
    tools.register(_echo())
    with pytest.raises(ValueError):
        tools.register(_echo())


# -- built-ins --------------------------------------------------------------


def test_builtin_schemas_and_approval_flags():
    definitions = {definition.name: definition for definition in builtins.definitions()}
    assert set(definitions) == {"bash", "read_file", "write_file"}
    assert definitions["bash"].approval is True
    assert definitions["write_file"].approval is True
    assert definitions["read_file"].approval is False
    assert definitions["bash"].parameters["required"] == ["command"]


async def test_bash_runs_in_the_session_directory(tmp_path):
    outcome = await builtins.run_bash({"command": "pwd && echo hello"}, str(tmp_path))
    assert outcome.is_error is False
    assert str(tmp_path) in outcome.text
    assert "hello" in outcome.text
    assert "[exit code: 0]" in outcome.text


async def test_bash_reports_failures_and_timeouts(tmp_path):
    failed = await builtins.run_bash({"command": "echo oops >&2; exit 3"}, str(tmp_path))
    assert failed.is_error is True
    assert "oops" in failed.text and "[exit code: 3]" in failed.text

    timed_out = await builtins.run_bash({"command": "sleep 5"}, str(tmp_path), timeout_ms=50)
    assert timed_out.is_error is True and "timed out" in timed_out.text


async def test_bash_output_is_truncated(tmp_path):
    outcome = await builtins.run_bash({"command": "printf 'x%.0s' {1..500}"}, str(tmp_path), max_output_chars=100)
    assert "truncated" in outcome.text


async def test_bash_cancellation_kills_the_command(tmp_path):
    task = asyncio.ensure_future(builtins.run_bash({"command": "sleep 30"}, str(tmp_path)))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_read_file_window_and_errors(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    whole = builtins.run_read_file({"path": "notes.txt"}, str(tmp_path))
    assert whole.text == "one\ntwo\nthree\nfour"

    window = builtins.run_read_file({"path": "notes.txt", "offset": 2, "limit": 2}, str(tmp_path))
    assert window.text == "two\nthree"

    absolute = builtins.run_read_file({"path": str(path)}, "/elsewhere")
    assert absolute.text.startswith("one")

    missing = builtins.run_read_file({"path": "nope.txt"}, str(tmp_path))
    assert missing.is_error and "no such file" in missing.text

    past_end = builtins.run_read_file({"path": "notes.txt", "offset": 99}, str(tmp_path))
    assert past_end.text == "(empty file or offset past end of file)"


def test_read_file_truncates_large_files(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("y" * 500, encoding="utf-8")
    outcome = builtins.run_read_file({"path": "big.txt"}, str(tmp_path), max_bytes=100)
    assert "truncated at 100 bytes" in outcome.text


def test_write_file_creates_parents_and_reports_bytes(tmp_path):
    outcome = builtins.run_write_file({"path": "nested/dir/file.txt", "content": "hello"}, str(tmp_path))
    assert outcome.is_error is False
    assert "wrote 5 bytes" in outcome.text
    assert (tmp_path / "nested" / "dir" / "file.txt").read_text(encoding="utf-8") == "hello"

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    failed = builtins.run_write_file({"path": "blocker/x", "content": "y"}, str(tmp_path))
    assert failed.is_error and "cannot write" in failed.text


def test_resolve_path_expands_user_and_relative_paths(tmp_path):
    assert builtins.resolve_path(str(tmp_path), "a/b.txt") == Path(tmp_path) / "a" / "b.txt"
    assert builtins.resolve_path(str(tmp_path), "/tmp/x").is_absolute()
