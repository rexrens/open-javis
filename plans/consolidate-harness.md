# Consolidate the harness: merge `javis.dsh` into `javis.harness`, free "contracts", drop the cordis CLI

Status: **ready for review**

## Context

User concerns (2026-09-01):

1. **`javis/cordis/cli.py` is a main-package component with no real production use.**
   It is a generic Cordis composition runner (`python -m javis.cordis.cli run [cordis.yml]`).
   Only consumers: `examples/dsh_harness` README + cordis.yml comment, `tests/test_cordis/test_cli.py`.
   The main package should not ship it; CLI usage belongs in `examples/`.
2. **Two "contracts" modules.** `javis/contracts/` is the host/plugin contract layer
   (AgentEngine, LLMProvider, Tool, AgentEvent, service names…). `javis/dsh/contracts.py` is a
   *different* thing (dsh-aligned data types: StreamChunk / FinishReason / blocks / Events /
   AgentLoopConfig). "contracts" should uniquely mean the plugin-contract layer.
3. **`javis/dsh/` + `javis/engines/harness/` together implement the harness.** The split into
   "architecture layer" + "integration shell" is artificial; merge into one package.

## Decisions (user-confirmed 2026-09-01)

- **Q1 → `javis/harness/` top-level package, flat layout.** Delete the `javis/engines/` directory.
  dsh modules fold flat into the package; no nested `dsh/` subpackage.
- **Q2 → delete the generic runner.** Keep only `examples/dsh_harness/cli.py` (its `compose()`
  already re-implements the boot). Delete `javis/cordis/cli.py` and `tests/test_cordis/test_cli.py`
  (coverage absorbed by `tests/test_demo_harness.py`, which already boots the real composition
  via `cli.compose()` and asserts scenario outcomes).
- **contracts rename → `javis/harness/types.py`.** After the merge the dsh contract module becomes
  `javis/harness/types.py`, so `contracts` uniquely means `javis/contracts`.

## Target layout

```
javis/harness/            ← new merged package
  __init__.py             # re-exports: agent, types, inbox, llm, session, tools
                          #   + HarnessEngine, build, JavisLLMAdapter, adapt_registry,
                          #     adapt_tool, HistoryCompressor, make_snip_listener, __version__
  agent.py                # ← javis/dsh/agent.py        (ReactLoopAgent)
  types.py                # ← javis/dsh/contracts.py    (RENAMED)
  inbox.py                # ← javis/dsh/inbox.py
  llm.py                  # ← javis/dsh/llm.py          (LLM seam, BlockAssembler)
  session.py              # ← javis/dsh/session.py
  tools.py                # ← javis/dsh/tools.py        (exclusive/parallel scheduler)
  engine.py               # ← javis/engines/harness/engine.py      (HarnessEngine)
  build.py                # ← javis/engines/harness/build.py
  llm_adapter.py          # ← javis/engines/harness/llm_adapter.py (JavisLLMAdapter)
  tool_adapter.py         # ← javis/engines/harness/tool_adapter.py
  prompt.py               # ← javis/engines/harness/prompt.py
  compression.py          # ← javis/engines/harness/compression.py
```

Unchanged (host-level, per 2026-09-01 decision): `javis/contracts/`, `javis/cordis/` (plugin
system), `javis/llm/providers.py`, `javis/tools/`. `DEFAULT_ENGINE = "harness"` string stays.

## Files to modify / delete

**Move (git mv, then edit imports):**
- `javis/dsh/{agent,inbox,llm,session,tools}.py` → `javis/harness/`; `javis/dsh/contracts.py` → `javis/harness/types.py`
- `javis/engines/harness/{engine,build,llm_adapter,tool_adapter,prompt,compression,__init__}.py` → `javis/harness/`

**Delete:**
- `javis/dsh/` (after moves), `javis/engines/` (after moves)
- `javis/cordis/cli.py` — generic runner
- `tests/test_cordis/test_cli.py` — 3 tests absorbed/obsolete

**Import rewrites (code):**
- `javis/harness/*` moved modules: dsh internals already use relative imports (`.contracts` /
  `.inbox` / `.llm` / `.session` / `.tools`) → change `.contracts` → `.types` in agent, session,
  inbox, llm, tools. Engine-shell modules: `from javis.dsh.X import …` → `from .X import …`
  (engine, llm_adapter, tool_adapter, prompt, compression).
- `javis/app/runtime.py:132` — `from javis.engines.harness import build` → `from javis.harness import build`
- `tests/test_harness/{test_engine,test_agent_loop,test_compression,test_llm_adapter}.py` —
  `javis.engines.harness.X` → `javis.harness.X`; `javis.dsh.contracts` → `javis.harness.types`
- `tests/test_demo_harness.py` (lines 50/58/75/303) — `javis.dsh.contracts` → `javis.harness.types`,
  `javis.dsh.tools` → `javis.harness.tools`
- `tests/test_javis/test_runtime.py:106-107` — `javis.dsh.agent` / `javis.engines.harness.engine`
  → `javis.harness.agent` / `javis.harness.engine`
- `examples/dsh_harness/{cli.py, mock_llm.py}` + 7 plugins — `javis.dsh.*` → `javis.harness.*`
- Docstring-only refs: `javis/contracts/engine.py:6`, `javis/llm/providers.py` header,
  `javis/tools/__init__.py` header, `examples/dsh_harness/cli.py:9`, `examples/plugin_harness/harness.py:6`

**Docs:**
- `README.md` — lines ~15, 53 (diagram box `javis.dsh` → `javis.harness`), 209-210 (project layout),
  coverage command `--cov=javis/harness`
- `examples/dsh_harness/README.md` — mapping table paths (`javis/dsh/…` → `javis/harness/…`,
  `contracts.py` → `types.py`), "共享 javis.dsh" wording, **remove the generic-runner block**
  (lines ~44-46: `python -m javis.cordis.cli run …`)
- `examples/dsh_harness/cordis.yml` — comment: drop generic-runner mention, point to
  `examples/dsh_harness/cli.py` only

## Reuse

- `examples/dsh_harness/cli.py::compose()` — already the canonical boot (kept as-is, imports updated)
- `javis/cordis/` Context / Loader / `settle` — unchanged, imported by demo cli + engine
- Relative-import structure of `javis/dsh/*` — preserved by moving the whole set together;
  only `.contracts` → `.types` renames within the package

## Steps

- [x] 1. `git mv` the six dsh modules into `javis/harness/`; rename `contracts.py` → `types.py`
- [x] 2. `git mv` the seven engines/harness modules into `javis/harness/`; rewrite
      `javis/harness/__init__.py` (merged exports + "single source for engine and demo" docstring)
- [x] 3. Rewrite intra-package imports: `.contracts` → `.types` (5 dsh modules);
      `javis.dsh.X` → `.X` / `javis.dsh.contracts` → `.types` (5 engine-shell modules)
- [x] 4. Delete `javis/dsh/`, `javis/engines/`, `javis/cordis/cli.py`, `tests/test_cordis/test_cli.py`
- [x] 5. Update all remaining `javis.dsh.*` / `javis.engines.*` references (app/runtime, tests,
      examples, docstrings) — use grep to enumerate each category
- [x] 6. Update docs: README.md (layout/diagram/coverage), examples/dsh_harness/README.md
      (mapping table + remove generic runner), cordis.yml comment
- [x] 7. grep sweep for leftover `javis.dsh` / `javis.engines` / `cordis.cli` / `dsh/contracts`

## Verification

- [x] `uv run pytest tests/ -q` — all green (**246 passed**)
- [x] `uv run python examples/dsh_harness/cli.py` — all 4 scenarios OK (demo boots via `javis.harness.*`)
- [x] `uv run ruff check javis/ examples/ tests/` — no new violations (12 I001 fixed;
      remaining 115 pre-existing in untouched javis/cordis + tests/test_cordis)
- [x] `grep -rn "javis\.dsh\|javis\.engines\|cordis\.cli\|dsh/contracts" javis examples tests docs README.md` → empty
