# javis

**English** | [简体中文](README.zh-CN.md)

> javis — your own local AI coding assistant. Fully customizable, written in Python.

You use Claude Code every day, but it's a closed box — you can't shape it to your workflow. javis is a Python-native local assistant built to be **yours**: the frontend, the agent loop, and the extension surface are all open to customization.

- **Frontend** — built on the **openharness** React/Ink terminal UI and continuously customized for javis. You never need to write TypeScript: the frontend is AI-maintained, while you stay in Python.
- **Backend** — a **self-developed agent harness in Python** (`javis/harness/`): a dsh-style `AgentLoop` (phase state machine, turn/step loop, inbox, session event log) with exclusive/parallel tool scheduling, assembled from `cordis.yml` composition rows and wired to real LLM providers and tools.
- **Extensibility** — a **Cordis-style plugin system** (following the DeepSeek Harness approach): tools, slash commands, and even the harness itself are pluggable through a `cordis.yml` composition.

Two layers:

- **`javis/harness/`** — the self-developed harness: the dsh-style `AgentLoop` (turn/step loop, exclusive/parallel tool scheduling, session event log), compression middleware, retries and cost tracking, plus the `Harness` shell that implements the contract.
- **`javis/`** — the shell: CLI, runtime, JSON-lines backend host, plugin composition, slash commands, session persistence, and the TUI launcher.

## Features

- **Built on the openharness frontend** — the React/Ink TUI is forked from openharness and customized for javis; frontend changes are AI-assisted, so you never have to write TypeScript.
- **Self-developed harness** — the Python harness (`javis/harness/`) is written from scratch: a dsh-style `AgentLoop` with exclusive/parallel tool scheduling, compression middleware, retries, cost tracking.
- **Plugin system** — following the DeepSeek Harness **"everything is a plugin"** philosophy: the tool registry, slash commands, and even the harness itself are pluggable and swappable via Cordis services.
- **Any OpenAI-compatible model** — DeepSeek, Qwen, Kimi, GLM, Ollama, etc. Switch providers by changing `base_url` + `api_key`. Non-OpenAI providers (Bedrock, Vertex, …) work via the built-in LiteLLM backend.
- **Agentic tool loop** — `bash`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, plus a nested sub-`agent` tool. Multiple tool calls execute **in parallel** (thread-pool based, inspired by Claude Code's `StreamingToolExecutor`).
- **Streaming TUI** — React + Ink terminal frontend with markdown rendering, tool transcripts, permission/edit modals, theme/permission/turns selectors, and image attachments.
- **Async & sync LLM paths** — both `chat()` and `achat()` loops with cancellation-safe history (interrupted tool calls are backfilled so the conversation stays valid for OpenAI-compatible APIs).
- **Context management** — automatic compression when tool outputs push the conversation past the token budget.
- **Robust LLM layer** — exponential-backoff retries (rate limit / timeout / 5xx), `stream_options` fallback for providers that reject it, usage tracking and per-model cost estimates.
- **Session persistence** — atomic JSON snapshots per session under `~/.javis/sessions/`, with `/resume` support from the TUI.
- **Deterministic offline testing** — `ScriptedAdapter` lets you exercise the harness without network.

## Architecture

The React/Ink frontend is forked from openharness and customized for javis; everything below it — protocol, runtime, agent loop — is our own Python implementation.

```
┌────────────────────────────────────────────────────────────────┐
│  React/Ink TUI (frontend/terminal, TypeScript)                 │
└───────────────▲───────────────────────────────┬────────────────┘
                │ OHJSON: {…} JSON-lines        │ requests
                │ (stdout)                      │ (stdin)
┌───────────────┴───────────────────────────────▼────────────────┐
│  javis.app.backend_host.JavisBackendHost                       │
│    (wire protocol, modals, selectors, permission flow)         │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentEvent stream
┌───────────────────────────┴───────────────────────────────────┐
│  javis.app.runtime.handle_line (slash commands + agent turns)  │
│  javis.contracts.harness.Harness — assembled by the            │
│    composition rows in javis/harness/plugins/                  │
└───────────────────────────▲───────────────────────────────────┘
                            │ Harness contract (the only seam)
┌───────────────────────────┴───────────────────────────────────┐
│  javis.harness (AgentLoop, session log) — shared with demo     │
└───────────────────────────▲───────────────────────────────────┘
                            │
┌───────────────────────────┴───────────────────────────────────┐
│  AgentLoop — turn/step loop, exclusive/parallel tools          │
│  javis.llm.LlmRuntime — adapter registry, llm/stream waterfall │
│  javis.llm — OpenAICompatAdapter / ScriptedAdapter             │
│  javis.tools — bash/read/write/edit/glob/grep/agent            │
└────────────────────────────────────────────────────────────────┘
```

The **`Harness` contract is the only seam**: the composition's `harness` row can point at your own implementation (or be disabled and replaced) without touching the host or the TUI.

## Quick start

Requires Python ≥ 3.10, Node.js ≥ 18, and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (Python + frontend)
uv sync --extra dev   # --extra dev pulls in pytest, ruff, mypy
cd frontend/terminal && npm install && cd ../..

# Configure your model provider (see Configuration below)
mkdir -p ~/.javis
# create ~/.javis/config.json

# Launch the TUI
uv run javis

# Or run a single prompt non-interactively
uv run javis -p "explain this repo"
```

## Configuration

`config.json` lives in the javis workspace (default `~/.javis`, override with `JAVIS_WORKSPACE` or `--workspace`):

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "providers": {
    "deepseek": {
      "baseUrl": "https://api.deepseek.com",
      "apiKeyEnv": "DEEPSEEK_API_KEY"
    }
  }
}
```

Alternatively, use environment variables (read from `.env` in the working directory, walking up to `$HOME`):

| Variable | Purpose |
|---|---|
| `CORECODER_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | API key |
| `CORECODER_MODEL` | Model name (default `gpt-5.5`) |
| `CORECODER_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible endpoint |
| `CORECODER_MAX_TOKENS`, `CORECODER_TEMPERATURE`, `CORECODER_MAX_CONTEXT` | Generation settings |
| `CORECODER_PROVIDER=litellm` | Route through LiteLLM (100+ providers) |
| `JAVIS_WORKSPACE` | Workspace root (default `~/.javis`) |

## Plugins

Plugins are Cordis-style `apply(ctx, config)` modules declared in a
**`cordis.yml` composition** — by default `<workspace>/cordis.yml` (auto-created
with the full six-row default composition when missing). The runtime mounts the
composition on every session and waits for all plugins to settle before reading
the harness.

Resolution order: `--plugins <file>` > `JAVIS_PLUGINS` > `config.json`
`pluginsFile` > `<workspace>/cordis.yml`. Entry `name:` paths resolve against
the composition file's directory; absolute paths also work.

```yaml
# ~/.javis/cordis.yml
- id: harness
  name: './my_harness.py'
  inject: [llm, agentTools, systemPrompt, agentLoop, config, host]
- id: extra-tools
  name: './extra_tools.py'
  inject: ['tools']
```

```python
# my_harness.py — a composition row that replaces the built-in Harness
def apply(ctx):
    cfg = ctx.get('config')        # JavisConfig
    tools = ctx.get('agentTools')  # ToolRegistry view of the host tools
    host = ctx.get('host')         # HostContext (cwd/session_id/tool_metadata/…)
    ctx.provide('harness', build_my_harness(cfg, tools=tools.all(), host=host))
```

Built-in services: `config` (`JavisConfig`), `tools` (`ToolRegistry`),
`commands` (`CommandRegistry`) and `host` (`HostContext`) are provided by the
host and never revoked. The services the harness needs — `llm`, `agentTools`,
`systemPrompt`, `agentLoop` and `harness` itself — come from the rows in
`javis/harness/plugins/`; the default composition wires all six rows. A composition
without a `harness` service (including an empty `[]`) is a hard boot error —
there is no built-in fallback.

Tools/commands plugins follow the disposer pattern so unloads clean up
automatically:

```python
def apply(ctx):
    tools = ctx.get('tools')
    ctx.effect(lambda: tools.register(MyTool()))  # unregister on unload
    commands = ctx.get('commands')
    ctx.effect(lambda: commands.register(Command('hello', 'Say hello', handler)))
```

See [docs/plugins.md](docs/plugins.md) for the full contract reference.

## Usage

### Modes

```bash
uv run javis                    # React/Ink TUI (default)
uv run javis -p "prompt"        # single prompt, print result, exit
uv run javis --backend-only     # JSON-lines backend host (for custom frontends)
uv run javis -v                 # debug logging to stderr
uv run javis doctor             # check workspace & frontend layout
```

### Slash commands

| Command | Description |
|---|---|
| `/help` | List available commands |
| `/status` | Model, message count, token usage, cwd, session id |
| `/clear` | Clear conversation history |
| `/exit`, `/quit` | Exit javis |
| `/version` | Show version |

Interactive selectors (from the TUI command picker): **permissions** (Default / Auto / Plan Mode), **theme**, **max turns**, **fast mode**, **vim mode**, **voice**, **model**, and **resume** (replay a past session).

### Permission modes

- **Default** — ask before write/execute
- **Auto** (`full_auto`) — allow all tools automatically, edit diffs auto-approved
- **Plan Mode** — block all write operations

## Development

```bash
uv run pytest tests/ -q          # 311 tests, all green
uv run pytest tests/ --cov=javis --cov=javis/harness   # coverage report
uv run ruff check javis/
uv run mypy javis/
```

### Project layout

```
javis/harness/       Harness: dsh-style AgentLoop + javis integration
  plugins/           composition rows that assemble the harness
  stream.py          loop-side stream assembly
  harness.py         Harness shell (implements the contract)
  tool_adapter.py    javis Tool → loop Tool + AgentToolView live view
javis/llm/            LLM provider implementations (OpenAICompat / Scripted)
javis/tools/          Host tool registry + 7 built-in tools
javis/                Host shell: CLI, runtime, backend host, wire protocol
  app/               Runtime, backend host, wire protocol, TUI launcher
  contracts/         Harness contract, event/message models (pure contracts)
  session/           Session persistence, app state, workspace layout
  commands/          Slash-command registry
  cordis/            Cordis-style plugin system (Context, Loader, services)
frontend/terminal    React/Ink TUI (TypeScript)
tests/               pytest suite
```

## License

MIT
