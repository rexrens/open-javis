# javis

**English** | [简体中文](README.zh-CN.md)

> An open-source, Python-native terminal AI coding assistant — a from-scratch port of the Claude Code experience.

javis drives an agentic coding loop from your terminal: you type a request, the agent reads files, runs commands, edits code, and streams its progress back — all rendered in a React/Ink TUI talking to a Python backend over a JSON-lines protocol.

It is built from two layers:

- **`corecoder/`** — the agent engine: an LLM tool-calling loop with parallel tool execution, context compression, retry/backoff and cost tracking.
- **`javis/`** — the shell: CLI, runtime, JSON-lines backend host, engine registry, slash commands, session persistence, and the React TUI launcher.

## Features

- **Any OpenAI-compatible model** — DeepSeek, Qwen, Kimi, GLM, Ollama, etc. Switch providers by changing `base_url` + `api_key`. Non-OpenAI providers (Bedrock, Vertex, …) work via the built-in LiteLLM backend.
- **Agentic tool loop** — `bash`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, plus a nested sub-`agent` tool. Multiple tool calls execute **in parallel** (thread-pool based, inspired by Claude Code's `StreamingToolExecutor`).
- **Streaming TUI** — React + Ink terminal frontend with markdown rendering, tool transcripts, permission/edit modals, theme/permission/turns selectors, and image attachments.
- **Async & sync LLM paths** — both `chat()` and `achat()` loops with cancellation-safe history (interrupted tool calls are backfilled so the conversation stays valid for OpenAI-compatible APIs).
- **Context management** — automatic compression when tool outputs push the conversation past the token budget.
- **Robust LLM layer** — exponential-backoff retries (rate limit / timeout / 5xx), `stream_options` fallback for providers that reject it, usage tracking and per-model cost estimates.
- **Session persistence** — atomic JSON snapshots per session under `~/.javis/sessions/`, with `/resume` support from the TUI.
- **Deterministic offline testing** — `ScriptedLLM` / `AsyncScriptedLLM` and a built-in `mock` engine let you exercise the full stack without network.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  React/Ink TUI (frontend/terminal, TypeScript)                 │
└───────────────▲───────────────────────────────┬────────────────┘
                │ OHJSON: {…} JSON-lines        │ requests
                │ (stdout)                      │ (stdin)
┌───────────────┴───────────────────────────────▼────────────────┐
│  javis.backend_host.JavisBackendHost                          │
│    (wire protocol, modals, selectors, permission flow)        │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentEvent stream
┌───────────────────────────┴───────────────────────────────────┐
│  javis.runtime.handle_line (slash commands + agent turns)     │
│  javis.engine.mock_engine.MockEngine (conversation history)   │
└───────────────────────────▲───────────────────────────────────┘
                            │ AgentBackend protocol (one seam)
┌───────────────────────────┴───────────────────────────────────┐
│  javis.engines.corecoder_backend.CoreCoderBackend             │
└───────────────────────────▲───────────────────────────────────┘
                            │
┌───────────────────────────┴───────────────────────────────────┐
│  corecoder.Agent.achat — tool loop, parallel exec, compress   │
│  corecoder.llm.AsyncLLM — streaming, retries, token/cost      │
│  corecoder.tools — bash/read/write/edit/glob/grep/agent       │
└────────────────────────────────────────────────────────────────┘
```

The **`AgentBackend` protocol is the only seam**: swap `MockAgent` for `CoreCoderBackend` (or any third-party backend registered via `register_engine`) without touching the engine or the TUI.

## Quick start

Requires Python ≥ 3.10, Node.js ≥ 18, and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (Python + frontend)
uv sync
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

Engine selection priority: **`--engine` CLI flag > `JAVIS_ENGINE` env var > `<workspace>/config.json` `engine` key > default (`corecoder`)**.

`config.json` lives in the javis workspace (default `~/.javis`, override with `JAVIS_WORKSPACE` or `--workspace`):

```json
{
  "engine": "corecoder",
  "engines": {
    "corecoder": {
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-..."
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
| `JAVIS_ENGINE` | Engine name (default `corecoder`) |
| `JAVIS_WORKSPACE` | Workspace root (default `~/.javis`) |

## Usage

### Modes

```bash
uv run javis                    # React/Ink TUI (default)
uv run javis -p "prompt"        # single prompt, print result, exit
uv run javis --backend-only     # JSON-lines backend host (for custom frontends)
uv run javis --engine mock -p "hi"   # fully offline (built-in mock agent)
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
uv run pytest tests/ -q          # 84 tests, all green
uv run pytest tests/ --cov=javis --cov=corecoder   # coverage report
uv run ruff check javis/ corecoder/
uv run mypy javis/
```

### Project layout

```
corecoder/            Agent engine: tool loop, LLM layer, tools, context manager
javis/                Shell: CLI, runtime, backend host, protocol, engine registry
  engine/             AgentBackend protocol + MockEngine/MockAgent + event types
  engines/            Backend adapters (corecoder, mock) + registration
  frontend/terminal   React/Ink TUI (TypeScript)
tests/                pytest suite
```

## License

MIT
