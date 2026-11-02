"""The ``cdh`` entry point, exercised as a real subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cordis_harness_support.fake_llm import text_step, tool_step

#: The example under test owns the ``harness`` package the subprocess imports.
EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "cordis_harness"


def run_cli(composition: Path, args: list[str], stdin: str = "", script=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # The example provides ``harness``; this test dir provides the scripted adapter.
    env["PYTHONPATH"] = os.pathsep.join([str(EXAMPLE_DIR), str(Path(__file__).resolve().parent)])
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    if script is not None:
        env["CDH_FAKE_SCRIPT"] = json.dumps(script)
    return subprocess.run(
        [sys.executable, "-m", "harness", "--config", str(composition), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_help_exits_cleanly(tmp_path):
    composition = tmp_path / "cordis.yml"
    composition.write_text("[]", encoding="utf-8")
    result = run_cli(composition, ["--help"])
    assert result.returncode == 0
    assert "--session-dir" in result.stdout


def test_list_sessions_on_an_empty_directory(make_composition, tmp_path):
    composition = make_composition(cli=True, session_dir=tmp_path / "sessions")
    result = run_cli(composition, ["--list-sessions", "--session-dir", str(tmp_path / "sessions")])
    assert result.returncode == 0
    assert "no sessions in" in result.stdout


def test_one_turn_streams_output_and_persists_the_session(make_composition, tmp_path):
    sessions = tmp_path / "sessions"
    composition = make_composition(cli=True, session_dir=sessions)
    result = run_cli(
        composition,
        ["--session-dir", str(sessions)],
        stdin="hello\n/exit\n",
        script=[text_step("streamed from the fake provider")],
    )
    assert result.returncode == 0, result.stderr
    assert "streamed from the fake provider" in result.stdout
    assert "cordis-harness · session" in result.stdout
    stored = list(sessions.glob("*.jsonl"))
    assert len(stored) == 1
    assert "assistant/message" in stored[0].read_text(encoding="utf-8")


def test_approval_prompt_is_honoured_from_stdin(make_composition, tmp_path):
    sessions = tmp_path / "sessions"
    composition = make_composition(cli=True, session_dir=sessions)
    result = run_cli(
        composition,
        ["--session-dir", str(sessions)],
        stdin="run it\nn\n",
        script=[tool_step("call-1", "bash", {"command": "echo danger"}), text_step("understood")],
    )
    assert result.returncode == 0, result.stderr
    assert "[approval] bash" in result.stdout
    assert "[result:failed]" in result.stdout
    assert "understood" in result.stdout
    assert result.stdout.index("[tool] bash") < result.stdout.index("[approval] bash")
    log = next(sessions.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "the user denied this call" in log


def test_resume_continues_the_same_session(make_composition, tmp_path):
    sessions = tmp_path / "sessions"
    composition = make_composition(cli=True, session_dir=sessions)
    first = run_cli(
        composition,
        ["--session-dir", str(sessions)],
        stdin="first\n/exit\n",
        script=[text_step("one")],
    )
    assert first.returncode == 0, first.stderr

    second = run_cli(
        composition,
        ["--session-dir", str(sessions), "--resume", "latest"],
        stdin="second\n/exit\n",
        script=[text_step("two")],
    )
    assert second.returncode == 0, second.stderr
    stored = list(sessions.glob("*.jsonl"))
    assert len(stored) == 1, "resume must reuse the stored session, not create another"
    log = stored[0].read_text(encoding="utf-8")
    assert log.count('"turn/start"') == 2
    assert "first" in log and "second" in log


def test_real_provider_without_a_key_reports_missing_credential(tmp_path):
    """The shipped OpenAI-compatible row is mounted; no key means no request."""
    composition = tmp_path / "cordis.yml"
    composition.write_text(
        "\n".join(
            [
                "- id: llm",
                "  name: harness.plugins.llm",
                "- id: llm-openai",
                "  name: harness.plugins.llm_openai",
                "  config:",
                "    provider: openai",
                "    baseUrl: http://127.0.0.1:9/v1",
                "    models: [deepseek-v4-flash]",
                "- id: sessions",
                "  name: harness.plugins.session",
                f"  config: {{directory: {tmp_path / 'sessions'}}}",
                "- id: tools",
                "  name: harness.plugins.tools",
                "- id: tools-builtin",
                "  name: harness.plugins.tools_builtin",
                "- id: system-prompt",
                "  name: harness.plugins.system_prompt",
                "- id: agent",
                "  name: harness.plugins.agent",
                "  config: {provider: openai, model: deepseek-v4-flash}",
                "- id: cli",
                "  name: harness.plugins.cli",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_cli(composition, [], stdin="hi\n/exit\n")
    assert result.returncode == 0, result.stderr
    assert "[error] MISSING_CREDENTIAL" in result.stdout
    assert "OPENAI_API_KEY" in result.stdout


def test_a_failed_fiber_exits_nonzero(make_composition, tmp_path):
    composition = make_composition(
        cli=True,
        session_dir=tmp_path / "s",
        extra_rows=[{"id": "broken", "name": "missing_plugin_module"}],
    )
    result = run_cli(composition, [])
    assert result.returncode == 1
    assert "FAILED" in result.stderr


def test_the_example_entry_point_delegates_to_the_harness_cli():
    """Inside Javis the example ships its own driver instead of a console script."""
    entry = (EXAMPLE_DIR / "cli.py").read_text(encoding="utf-8")
    assert "harness.cli" in entry
    assert "sys.path" in entry
