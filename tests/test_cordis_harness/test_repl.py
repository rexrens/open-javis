"""The interactive front end, driven with scripted stdin."""

from __future__ import annotations

import io

from harness.repl import Repl
from cordis_harness_support import fake_llm_plugin
from cordis_harness_support.fake_llm import text_step, tool_step


def scripted_input(lines: list[str]):
    """An ``input`` replacement that raises EOFError when the script is done."""
    remaining = list(lines)

    def read(prompt: str = "") -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


async def _run_repl(mount, lines: list[str], *, composition: dict | None = None, **repl_kwargs):
    ctx, _ = await mount(**(composition or {}))
    out = io.StringIO()
    exits: list[int] = []
    ctx.on("app/exit", lambda code=0: exits.append(int(code)))
    options = {"provider": "fake", "model": "fake-model", "input_fn": scripted_input(lines), "out": out}
    options.update(repl_kwargs)
    repl = Repl(ctx, **options)
    await repl.run()
    return ctx, repl, out.getvalue(), exits


async def test_streams_a_turn_and_exits(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("streamed answer")])
    ctx, repl, output, exits = await _run_repl(mount, ["hello"], composition={"session_dir": tmp_path / "s"})
    assert "streamed answer" in output
    assert "cordis-harness · session" in output
    assert exits == [0]
    sessions = ctx.get("sessions")
    stored = sessions.latest()
    assert stored is not None and stored.messages()[-1]["content"][0]["text"] == "streamed answer"


async def test_slash_commands(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("first"), text_step("second")])
    ctx, repl, output, exits = await _run_repl(
        mount,
        [
            "/help",
            "/tools",
            "/model other-model",
            "/model",
            "/provider",
            "/yolo on",
            "/yolo off",
            "/yolo",
            "/nope",
            "hi",
            "/exit",
        ],
        composition={"session_dir": tmp_path / "s"},
    )
    assert "Commands:" in output
    assert "bash (needs approval)" in output
    assert "[model] fake/other-model" in output
    assert "[provider] fake (fake)" in output
    assert "[yolo] on" in output
    assert "[yolo] off" in output
    assert "unknown command /nope" in output
    assert "first" in output  # the model switched for the turn that followed
    assert exits == [0]


async def test_new_and_resume_commands(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("first")])
    ctx, repl, output, _ = await _run_repl(
        mount, ["hi", "/sessions", "/new", "/resume latest"], composition={"session_dir": tmp_path / "s"}
    )
    assert "[session]" in output
    sessions = ctx.get("sessions")
    assert len(sessions.list()) == 2
    assert repl.session.id == sessions.list()[0].id
    assert "messages)" in output


async def test_approval_prompt_can_deny_and_allow(mount, tmp_path):
    fake_llm_plugin.set_script(
        [
            tool_step("call-1", "bash", {"command": "echo danger"}),
            text_step("denied"),
            tool_step("call-2", "bash", {"command": "echo allowed"}),
            text_step("ran"),
        ]
    )
    ctx, repl, output, _ = await _run_repl(
        mount, ["go", "n", "go again", "y"], composition={"session_dir": tmp_path / "s"}
    )
    assert "[approval] bash" in output
    assert "[result:failed]" in output
    assert "[result:ok]" in output
    assert "allowed" in output
    assert "ran" in output


async def test_yolo_skips_the_prompt(mount, tmp_path):
    fake_llm_plugin.set_script([tool_step("call-1", "bash", {"command": "echo fast"}), text_step("done")])
    ctx, repl, output, _ = await _run_repl(
        mount, ["go"], composition={"session_dir": tmp_path / "s"}, auto_approve=True
    )
    assert "[approval]" not in output
    assert "fast" in output


async def test_reasoning_is_hidden_unless_requested(mount, tmp_path):
    from harness.types import block_end, block_start, finish_chunk, reasoning_delta, text_delta

    step = [
        block_start(0, "reasoning"),
        reasoning_delta(0, "secret thoughts"),
        block_end(0, {"type": "reasoning", "text": "secret thoughts"}),
        block_start(1, "text"),
        text_delta(1, "visible"),
        block_end(1, {"type": "text", "text": "visible"}),
        finish_chunk("stop"),
    ]
    fake_llm_plugin.set_script([step])
    _, _, hidden, _ = await _run_repl(mount, ["hi"], composition={"session_dir": tmp_path / "a"})
    assert "secret thoughts" not in hidden

    fake_llm_plugin.set_script([step])
    _, _, shown, _ = await _run_repl(
        mount, ["hi"], composition={"session_dir": tmp_path / "b"}, show_reasoning=True
    )
    assert "[thinking] secret thoughts" in shown


async def test_tool_output_is_summarised(mount, tmp_path):
    lines = "\n".join(f"line {index}" for index in range(40))
    fake_llm_plugin.set_script([tool_step("call-1", "bash", {"command": f"printf '{lines}\\\\n'"}), text_step("done")])
    _, _, output, _ = await _run_repl(
        mount, ["go"], composition={"session_dir": tmp_path / "s"}, auto_approve=True
    )
    assert "more line(s)" in output


async def test_list_sessions_mode(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("hi")])
    ctx, repl, output, exits = await _run_repl(
        mount, [], composition={"session_dir": tmp_path / "s"}, list_sessions=True
    )
    assert "no sessions in" in output
    assert exits == [0]
    assert repl.exit_code == 0


async def test_resume_unknown_session_fails(mount, tmp_path):
    fake_llm_plugin.set_script([text_step("hi")])
    _, repl, output, exits = await _run_repl(
        mount, [], composition={"session_dir": tmp_path / "s"}, resume="nope"
    )
    assert "[error]" in output
    assert exits == [1]


async def test_interrupt_aborts_the_running_turn(mount, tmp_path):
    """A Ctrl-C flag stops the turn and still closes its session frame."""
    import asyncio

    fake_llm_plugin.set_script([[{"$sleep": 5}], text_step("late")])
    ctx, _ = await mount(session_dir=tmp_path / "s")
    out = io.StringIO()
    repl = Repl(
        ctx,
        provider="fake",
        model="fake-model",
        input_fn=scripted_input(["go"]),
        out=out,
    )

    async def interrupt() -> None:
        await asyncio.sleep(0.3)
        repl._interrupted = True  # what the SIGINT handler sets

    interrupter = asyncio.ensure_future(interrupt())
    await repl.run()
    interrupter.cancel()
    assert "[aborted]" in out.getvalue()
    session = ctx.get("sessions").latest()
    assert [event["type"] for event in session.events()][-1] == "turn/end"


async def test_cwd_command_changes_the_tool_directory(mount, tmp_path):
    fake_llm_plugin.set_script([tool_step("call-1", "bash", {"command": "pwd"}), text_step("done")])
    other = tmp_path / "other"
    other.mkdir()
    ctx, repl, output, _ = await _run_repl(
        mount, [f"/cwd {other}", "go"], composition={"session_dir": tmp_path / "s"}, auto_approve=True
    )
    assert f"[cwd] {other}" in output
    assert str(other) in output
    assert "not a directory" in await _cwd_error(mount, tmp_path)


async def _cwd_error(mount, tmp_path) -> str:
    fake_llm_plugin.set_script([text_step("hi")])
    _, _, output, _ = await _run_repl(
        mount, [f"/cwd {tmp_path / 'missing'}"], composition={"session_dir": tmp_path / "s2"}
    )
    return output
