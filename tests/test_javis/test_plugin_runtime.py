"""Plugin-ization tests: cordis composition mounted inside ``build_runtime``.

Covers the reserved service seams (``config`` / ``tools`` / ``commands`` /
``host`` / ``harness``), harness selection (composition row or loud boot
error), plugin tool/command registration through the live ``agentTools``
view, teardown, and the permission-hook injection path.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
import yaml

from javis.app.backend_host import BackendHost
from javis.app.runtime import RuntimeBundle, build_runtime
from javis.commands.registry import create_default_command_registry
from javis.contracts import HARNESS_SERVICE
from javis.harness.types import Events, PostToolDecision, TextBlock, ToolExecutionResult
from javis.session.session_storage import JavisSessionBackend
from javis.session.state import AppState, AppStateStore
from tests.test_javis.fake_backend import FakeEngine

# ---------------------------------------------------------------------------
# plugin module sources (executed by the Cordis loader, so self-contained)
# ---------------------------------------------------------------------------

ENGINE_PLUGIN = '''
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from javis.contracts import HARNESS_SERVICE, Harness
from javis.contracts.messages import ConversationMessage, TextBlock
from javis.contracts.types import AgentEvent, AgentTextDelta, AgentTurnEnd
from javis.contracts.usage import UsageSnapshot


class PluginEngine(Harness):
    model = "plugin-model"

    def __init__(self):
        self._messages: list[ConversationMessage] = []
        self._usage = UsageSnapshot()
        self._system_prompt = "plugin system prompt"
        self._max_turns: int | None = None
        self._tool_metadata: dict[str, Any] = {}
        self._effort: str | None = None

    @property
    def messages(self):
        return list(self._messages)

    @property
    def total_usage(self):
        return self._usage

    @property
    def system_prompt(self):
        return self._system_prompt

    @property
    def max_turns(self):
        return self._max_turns

    @property
    def tool_metadata(self):
        return self._tool_metadata

    def set_system_prompt(self, prompt):
        self._system_prompt = prompt

    def set_model(self, model):
        self.model = model

    def set_effort(self, effort):
        self._effort = effort

    def set_max_turns(self, max_turns):
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def clear(self):
        self._messages.clear()
        self._usage = UsageSnapshot()

    def load_messages(self, messages):
        self._messages = list(messages)

    async def submit_message(self, prompt):
        text = prompt.text if isinstance(prompt, ConversationMessage) else prompt
        self._messages.append(ConversationMessage.from_user_text(text))
        final = "plugin reply"
        self._messages.append(ConversationMessage(role="assistant", content=[TextBlock(text=final)]))
        yield AgentTextDelta(text=final)
        yield AgentTurnEnd(text=final)


def apply(ctx):
    host = ctx.get("host")
    seen = {
        "session_id": host.session_id,
        "cwd": host.cwd,
        "workspace": host.workspace,
        "has_config": type(ctx.get("config")).__name__,
        "tool_names": [t.name for t in ctx.get("tools").all()],
    }
    Path(host.workspace, "plugin_seen.json").write_text(json.dumps(seen), encoding="utf-8")
    ctx.provide(HARNESS_SERVICE, PluginEngine())

    def _dispose():
        Path(host.workspace, "disposed.txt").write_text("yes", encoding="utf-8")

    ctx.effect(lambda: _dispose)
'''

EXTRA_TOOLS_PLUGIN = '''
from __future__ import annotations

from typing import Any, ClassVar

from javis.commands.registry import Command, CommandResult
from javis.contracts.tools import Tool


class HelloTool(Tool):
    name = "hello_tool"
    description = "say hello"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return "hello from plugin"


def apply(ctx):
    tools = ctx.get("tools")
    commands = ctx.get("commands")

    async def hello_handler(args, context):
        return CommandResult(message="hello from plugin command")

    ctx.effect(lambda: tools.register(HelloTool()))
    ctx.effect(lambda: commands.register(Command("hello", "Say hello", hello_handler)))
'''


# ---------------------------------------------------------------------------
# fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_workspace(tmp_path, monkeypatch):
    """Isolated javis workspace; ``JAVIS_WORKSPACE`` points into tmp_path."""
    ws = tmp_path / "javis-workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("JAVIS_WORKSPACE", str(ws))
    monkeypatch.chdir(tmp_path)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return ws


def write_composition(workspace: Path, entries: list[dict[str, object]]) -> Path:
    path = workspace / "cordis.yml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")
    return path


def _seen(workspace: Path) -> dict[str, object]:
    return json.loads((workspace / "plugin_seen.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_harness_provides_instance(plugin_workspace):
    """A composition row that provides ``harness`` is the harness — no
    built-in row is mounted — and sees the host services inside ``apply``."""
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "harness", "name": "./engine_plugin.py", "inject": ["config", "tools", "host"]},
    ])

    bundle = await build_runtime(cwd=str(plugin_workspace.parent))

    assert type(bundle.engine).__name__ == "PluginEngine"
    seen = _seen(plugin_workspace)
    assert seen["has_config"] == "JavisConfig"
    assert seen["session_id"] == bundle.session_id
    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "agent"} <= set(seen["tool_names"])
    await bundle.close()


@pytest.mark.asyncio
async def test_missing_composition_writes_full_default(plugin_workspace, fake_engine_factory):
    """No composition → the full six-row default composition is written, every
    row activates, and the harness service is the patched test double."""
    from javis.session.config import DEFAULT_COMPOSITION

    fake_engine_factory()
    bundle = await build_runtime(cwd=str(plugin_workspace.parent))

    assert isinstance(bundle.engine, FakeEngine)
    composition = (plugin_workspace / "cordis.yml").read_text(encoding="utf-8")
    assert composition == DEFAULT_COMPOSITION
    assert bundle.context is not None
    assert bundle.context.get("agentTools") is not None
    assert bundle.context.get("systemPrompt") is not None
    assert bundle.context.get("agentLoop") is not None
    assert bundle.context.get("llm") is not None
    await bundle.close()


@pytest.mark.asyncio
async def test_composition_without_harness_row_raises(plugin_workspace):
    """A composition that never provides ``harness`` fails loudly instead of
    silently falling back to a built-in engine."""
    (plugin_workspace / "extra_tools.py").write_text(EXTRA_TOOLS_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "extra-tools", "name": "./extra_tools.py", "inject": ["tools", "commands"]},
    ])

    with pytest.raises(RuntimeError, match="provides no 'harness' service"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_harness_service_with_wrong_type_raises(plugin_workspace):
    """A row that provides ``harness`` as a non-``Harness`` value fails boot
    with ``build_runtime`` naming the offending type."""
    (plugin_workspace / "bad_harness.py").write_text(
        "from javis.contracts import HARNESS_SERVICE\n"
        "\n"
        "\n"
        "def apply(ctx):\n"
        '    ctx.provide(HARNESS_SERVICE, "not-a-harness")\n',
        encoding="utf-8",
    )
    write_composition(plugin_workspace, [{"id": "harness", "name": "./bad_harness.py"}])

    with pytest.raises(RuntimeError, match=r"provides no 'harness' service \(str\)"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_empty_composition_raises_naming_the_file(plugin_workspace):
    """An explicitly empty composition (``[]``) boots and settles, then fails
    loud with the composition file in the error message."""
    (plugin_workspace / "cordis.yml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"cordis\.yml provides no 'harness' service"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_entry_with_missing_dependency_raises(plugin_workspace):
    """A row whose ``inject`` names a service nobody provides fails the boot
    assertion with the missing service name (``settle`` would swallow it)."""
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "broken", "name": "./engine_plugin.py", "inject": ["nosuchservice"]},
    ])

    with pytest.raises(RuntimeError, match="nosuchservice"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_failing_entry_reports_original_error(plugin_workspace):
    """A row whose ``apply`` raises surfaces the original exception message."""
    (plugin_workspace / "boom.py").write_text(
        "def apply(ctx):\n    raise ValueError('row boom')\n", encoding="utf-8"
    )
    write_composition(plugin_workspace, [
        {"id": "boom", "name": "./boom.py"},
    ])

    with pytest.raises(RuntimeError, match="row boom"):
        await build_runtime(cwd=str(plugin_workspace.parent))


@pytest.mark.asyncio
async def test_entry_module_that_fails_to_import_names_the_entry(plugin_workspace):
    """A row whose module path does not resolve fails with the entry id, the
    module name and the original import error."""
    write_composition(plugin_workspace, [
        {"id": "harness", "name": "javis.harness.plugins.nope"},
    ])

    with pytest.raises(RuntimeError) as excinfo:
        await build_runtime(cwd=str(plugin_workspace.parent))

    message = str(excinfo.value)
    assert "composition entry 'harness'" in message
    assert "javis.harness.plugins.nope" in message
    assert "No module named" in message


@pytest.mark.asyncio
async def test_snip_row_wires_tool_output_max_chars(plugin_workspace):
    """The ``snip`` row reads ``toolOutputMaxChars`` from its composition
    config into the ``tools/post-execute`` waterfall."""
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "snip", "name": "javis.harness.plugins.snip",
         "config": {"toolOutputMaxChars": 20}},
        {"id": "harness", "name": "./engine_plugin.py", "inject": ["config", "tools", "host"]},
    ])

    bundle = await build_runtime(cwd=str(plugin_workspace.parent))
    assert bundle.context is not None

    result = ToolExecutionResult(content=[TextBlock(text="y" * 200)])
    post = bundle.context.waterfall(
        Events.TOOLS_POST_EXECUTE, None, result, lambda *_args: None
    )
    if inspect.isawaitable(post):
        post = await post

    assert isinstance(post, PostToolDecision)
    assert post.content is not None
    assert "truncated by compression middleware" in post.content[0].text
    assert len(post.content[0].text) < 200
    await bundle.close()


@pytest.mark.asyncio
async def test_plugin_tools_and_commands_reach_engine(plugin_workspace):
    """Tool/command plugins registered after the live ``agentTools`` view was
    built show up through it (composition order), and commands reach the
    command registry."""
    (plugin_workspace / "extra_tools.py").write_text(EXTRA_TOOLS_PLUGIN, encoding="utf-8")
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "agent-tools", "name": "javis.harness.plugins.agent_tools", "inject": ["tools"]},
        {"id": "extra-tools", "name": "./extra_tools.py", "inject": ["tools", "commands"]},
        {"id": "engine", "name": "./engine_plugin.py", "inject": ["config", "tools", "host"]},
    ])

    bundle = await build_runtime(cwd=str(plugin_workspace.parent))

    assert "hello_tool" in _seen(plugin_workspace)["tool_names"]
    assert {cmd.name for cmd in bundle.commands.list_commands()} >= {"hello", "help", "status"}
    assert bundle.context is not None
    view = bundle.context.get("agentTools")
    assert view.get("hello_tool") is not None
    assert "hello_tool" in {schema.name for schema in view.schemas()}
    await bundle.close()


@pytest.mark.asyncio
async def test_close_disposes_plugins_and_revokes_harness(plugin_workspace):
    """``bundle.close()`` runs plugin disposers and removes provided services;
    a second close is a no-op."""
    (plugin_workspace / "engine_plugin.py").write_text(ENGINE_PLUGIN, encoding="utf-8")
    write_composition(plugin_workspace, [
        {"id": "engine", "name": "./engine_plugin.py", "inject": ["config", "tools", "host"]},
    ])

    bundle = await build_runtime(cwd=str(plugin_workspace.parent))
    assert not (plugin_workspace / "disposed.txt").exists()

    await bundle.close()

    assert (plugin_workspace / "disposed.txt").read_text(encoding="utf-8") == "yes"
    assert bundle.context is not None
    assert bundle.context.get(HARNESS_SERVICE) is None
    await bundle.close()  # idempotent


@pytest.mark.asyncio
async def test_explicit_composition_path(plugin_workspace, fake_engine_factory, tmp_path):
    """``plugins=`` (CLI override) selects a specific composition file; a
    missing explicit file is a hard error."""
    from javis.session.config import DEFAULT_COMPOSITION

    fake_engine_factory()
    comp = tmp_path / "plugins.yml"
    comp.write_text(DEFAULT_COMPOSITION, encoding="utf-8")

    bundle = await build_runtime(cwd=str(plugin_workspace.parent), plugins=str(comp))
    assert isinstance(bundle.engine, FakeEngine)
    await bundle.close()

    with pytest.raises(ValueError, match="Plugin composition file not found"):
        await build_runtime(cwd=str(plugin_workspace.parent), plugins=str(tmp_path / "nope.yml"))


@pytest.mark.asyncio
async def test_backend_host_injects_permission_hook(plugin_workspace):
    """BackendHost uses the contract-level ``set_permission_checker`` hook when
    the engine implements it (legacy ``engine.agent`` path stays as fallback)."""

    class PermissionEngine(FakeEngine):
        def set_permission_checker(self, checker):
            self.permission_callback = checker

    engine = PermissionEngine()
    bundle = RuntimeBundle(
        engine=engine,
        cwd=str(plugin_workspace),
        app_state=AppStateStore(AppState(model="test-model", cwd=str(plugin_workspace))),
        commands=create_default_command_registry(),
        session_backend=JavisSessionBackend(plugin_workspace),
        session_id="perm-test",
    )
    host = BackendHost(bundle=bundle)

    host._inject_permission_checker()

    assert engine.permission_callback == host._check_permission
