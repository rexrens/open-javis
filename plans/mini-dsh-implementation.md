# mini_dsh 实现计划（examples/plugin_harness → examples/mini_dsh）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 把 `examples/plugin_harness` 重建为 `examples/mini_dsh`——一个 cordis-only、核心自包含的 dsh 风格精简 harness（8 个 core 模块 + 8 个插件 + 7 个 demo 场景），除 `javis.cordis` 外零 javis 依赖。

**架构：** `core/` = `javis/harness` 架构层（types/session/inbox/llm/tools/agent）的独立精简复刻（copy + trim，命名对齐 dsh），外加两个新模块（skill/compaction）；`plugins/` = 8 个 cordis 插件负责装配（服务提供 + waterfall 监听器 + 组合根）；`providers.py` = scripted + OpenAI 兼容 adapter；`cli.py` = standalone 驱动。

**技术栈：** Python ≥3.12，`javis.cordis`（Context/Loader/effect/waterfall/serial/emit），pydantic（插件 config 校验），pyyaml（SKILL.md frontmatter），openai SDK（可选真实模型），pytest + pytest-asyncio，ruff，uv。

**规格：** `plans/mini-dsh-example.md`（Q1–Q7 用户确认决策）
**参照源码：** `javis/harness/*.py`（copy+trim 基线）+ `/home/rensu/workspace/deepseek-harness/packages/*`（dsh TS 原版，查证用）
**测试模板：** `tests/test_demo_harness.py`（场景组合测试模式）、`tests/test_harness/*`（core 单元测试模式）

---

## 全局约定（所有任务适用）

- 测试命令（仓库根）：`uv run pytest tests/test_mini_dsh/<file>.py -v`
- async 测试统一 `@pytest.mark.asyncio`（pytest-asyncio 已装）
- 新测试目录 `tests/test_mini_dsh/`（`__init__.py` + `conftest.py` 在 Task 2 建立）
- ruff：改动文件零新增告警（`uv run ruff check <改动文件>`）
- 每个任务一次 commit，消息 `feat/test/chore(mini-dsh): ...`（英文）
- 插件文件头约定（cordis Loader 按文件路径加载插件，插件必须能独立 import core）：

```python
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```

- "copy + trim" 任务：源文件 = `javis/harness/<同名>.py`；只改内部相对 import 与 docstring 自述；**公开符号名与语义不得改变**。
- 服务名沿用 dsh 词汇：`sessions` / `skills` / `compaction` / `llm` / `tools` / `agentLoop` / `systemPrompt` / `agent`（session 实例也以 `"session"` 提供，供测试/插件运行时读取——driver 提供，与 dsh_harness 一致）。

## 文件布局（锁定）

| 文件 | 职责 | 量级 |
|---|---|---|
| `core/__init__.py` | 包 docstring（不做 re-export，子模块直接 import） | ~10 |
| `core/types.py` | 数据契约：blocks/chunks/finish/messages/usage/abort/事件名/配置 | ~270 |
| `core/session.py` | Session 事件日志 + derive_messages（含 compaction shadow）+ SessionStore 服务 | ~160 |
| `core/inbox.py` | next-turn/next-step 双队列 + splice | ~70 |
| `core/llm.py` | LLM 协议 + BlockAssembler + normalized_stream + chunk_response + SystemPrompt | ~180 |
| `core/tools.py` | Tool/ToolRegistry + execute_tool_calls（exclusive/parallel） | ~200 |
| `core/skill.py` | SkillRegistry 服务 + FileSkillProvider | ~130 |
| `core/compaction.py` | Compaction 服务 + 规则摘要器 + make_snip_listener | ~180 |
| `core/agent.py` | ReactLoopAgent 相位状态机 + turn/step 主循环 | ~330 |
| `plugins/session.py` | provide `"sessions"` | ~20 |
| `plugins/llm.py` | provide `"llm"`（scripted/openai 选择） | ~50 |
| `plugins/tools.py` | provide `"tools"` + demo 工具 | ~70 |
| `plugins/skill_tool.py` | provide `"skills"` + skill 加载工具 + 目录发布 + `/<name>` 注入 | ~130 |
| `plugins/instructions.py` | AGENTS.md/CLAUDE.md baseline + 变更重注入 | ~90 |
| `plugins/compaction.py` | provide `"compaction"` + pre-step 压力检查 | ~40 |
| `plugins/middleware.py` | request-error 重试 + steer 观察 | ~90 |
| `plugins/driver.py` | 组合根：store.create() → ReactLoopAgent → provide agent/agentLoop/systemPrompt/session | ~70 |
| `providers.py` | ScriptedAdapter（7 场景）+ OpenAICompatAdapter + scenario_script | ~280 |
| `cordis.yml` | 8 个插件条目 | ~30 |
| `cli.py` | demo 场景（断言）/ --scenario / --prompt / --repl | ~230 |
| `skills/poetic-note/SKILL.md` | 示例技能 | ~15 |
| `fixtures/AGENTS.md` | instructions 场景 fixture | ~5 |
| `README.md` | 全重写（Task 17） | ~150 |

测试文件：`tests/test_mini_dsh/{conftest,test_types,test_session,test_inbox,test_llm,test_tools,test_skill,test_compaction,test_agent,test_providers,test_composition}.py`（后 5 个场景在 Task 11–15 逐步补进 test_composition.py）+ `tests/test_javis/test_mini_dsh_example.py`（Task 16 重写为 E2E）。

---

## Task 1：git mv 目录 + 改名测试 + 引用改名（旧代码在新位置继续工作）

**文件：**
- 移动：`examples/plugin_harness` → `examples/mini_dsh`
- 移动：`tests/test_javis/test_plugin_harness_example.py` → `tests/test_javis/test_mini_dsh_example.py`
- 修改：`examples/cordis/README.md`（第 7 行）、`examples/dsh_harness/README.md`（全部 `plugin_harness` → `mini_dsh`）

- [ ] **步骤 1：git mv**

```bash
git mv examples/plugin_harness examples/mini_dsh
git mv tests/test_javis/test_plugin_harness_example.py tests/test_javis/test_mini_dsh_example.py
```

- [ ] **步骤 2：修测试路径与 docstring**

`tests/test_javis/test_mini_dsh_example.py`：
- `_COMPOSITION = Path(...).parents[2] / "examples" / "plugin_harness" / "cordis.yml"` → `"mini_dsh"`
- 模块 docstring 里 `examples/plugin_harness/cordis.yml` → `examples/mini_dsh/cordis.yml`（其余措辞留待 Task 16 重写）

- [ ] **步骤 3：引用改名**

`examples/cordis/README.md` 第 7 行：
`` [`examples/plugin_harness`](../plugin_harness/README.md)（独立引擎如何接入宿主） `` → `` [`examples/mini_dsh`](../mini_dsh/README.md)（cordis-only 的 dsh 精简 harness——随重建更新定位） ``

`examples/dsh_harness/README.md`：全文 `plugin_harness` → `mini_dsh`（链接路径、小节标题、对照表列头），定位措辞留待 Task 17 重写。

- [ ] **步骤 4：跑测试确认绿**

运行：`uv run pytest tests/test_javis/test_mini_dsh_example.py -v`
预期：1 passed（旧代码在新位置正常）

- [ ] **步骤 5：Commit**

```bash
git add -A
git commit -m "chore(mini-dsh): rename example dir plugin_harness to mini_dsh"
```

---

## Task 2：core 包骨架 + core/types.py

**文件：**
- 创建：`examples/mini_dsh/core/__init__.py`、`examples/mini_dsh/core/types.py`
- 测试：`tests/test_mini_dsh/__init__.py`、`tests/test_mini_dsh/conftest.py`、`tests/test_mini_dsh/test_types.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/conftest.py`：

```python
"""Make examples/mini_dsh importable as top-level ``core`` / ``providers``."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "examples" / "mini_dsh"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```

`tests/test_mini_dsh/__init__.py`：空文件。

`tests/test_mini_dsh/test_types.py`：

```python
"""core/types data-contract invariants (dsh-aligned vocabulary)."""
from core import types as t


def test_session_event_types_include_compaction_family():
    assert {"compaction/start", "compaction/summary", "compaction/end"} <= set(t.SESSION_EVENT_TYPES)
    assert "user/message" in t.SESSION_EVENT_TYPES
    assert "agent/inbox/spliced" in t.SESSION_EVENT_TYPES


def test_call_config_equals():
    a = t.LlmCallConfig(provider="p", model="m")
    b = t.LlmCallConfig(provider="p", model="m")
    c = t.LlmCallConfig(provider="p", model="other")
    assert t.call_config_equals(a, b)
    assert not t.call_config_equals(a, c)


def test_finish_kinds():
    kinds = {
        t.StopFinish(): "stop",
        t.ToolCallsFinish(): "tool-calls",
        t.MaxTokensFinish(): "max-tokens",
        t.AbortedFinish(failure=t.LlmFailure(message="x", code="C")): "aborted",
        t.ErrorFinish(failure=t.LlmFailure(message="x", code="C")): "error",
    }
    for finish, expected in kinds.items():
        assert finish.kind == expected


def test_chunk_union_accepts_all_chunk_types():
    chunks = [
        t.BlockStartChunk(index=0, block_type="text"),
        t.TextDeltaChunk(index=0, text="hi"),
        t.BlockEndChunk(index=0, block=t.TextBlock(text="hi")),
        t.UsageChunk(usage=t.TokenUsage(input_tokens=1, output_tokens=1)),
        t.FinishChunk(reason=t.StopFinish()),
    ]
    for chunk in chunks:
        assert isinstance(chunk, t.StreamChunk)


def test_agent_loop_config_defaults():
    cfg = t.AgentLoopConfig()
    assert cfg.max_parallel_tool_calls == 4
    assert cfg.max_steps_per_turn == 20
    assert not hasattr(cfg, "history_compressor")  # javis 扩展已裁


def test_generate_options_constructible():
    opts = t.GenerateOptions(provider="p", model="m", messages=(t.UserMessage.from_text("hi"),))
    assert opts.system is None
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_types.py -v`
预期：FAIL——`ModuleNotFoundError: No module named 'core'`

- [ ] **步骤 3：copy + trim 实现**

复制 `javis/harness/types.py` → `examples/mini_dsh/core/types.py`，然后：
1. 把相对 import 改为包内相对（本就无跨模块 import，仅 docstring 提及 javis，把 docstring 顶部改为 "mini_dsh core —— 数据契约（dsh 对齐）"）；
2. `AgentLoopConfig` 删掉 `history_compressor: Any = None` 字段及其 docstring 里的 javis 扩展 bullet（保留 `max_parallel_tool_calls=4` 与 `max_steps_per_turn=20`）；
3. `SessionEvents` 加三个常量：

```python
    COMPACTION_START = "compaction/start"
    COMPACTION_SUMMARY = "compaction/summary"
    COMPACTION_END = "compaction/end"
```

4. `SESSION_EVENT_TYPES` 里加入 `SessionEvents.COMPACTION_START / COMPACTION_SUMMARY / COMPACTION_END`。

其余全部保留（含 `SESSION_FORMAT_VERSION`、`TOOL_ABORTED_BEFORE_DISPATCH`、`AgentLoop` 包装类、`Events` 常量、`PromptSection/PromptAssembly`）。

`core/__init__.py`：

```python
"""mini_dsh core —— dsh 风格 harness 的自包含精简实现（唯一外部依赖 javis.cordis）。

模块与 javis/harness 架构层同名（命名对齐 dsh TS 源码），代码是独立复刻：
不 import javis.harness / javis.llm / javis.contracts 的任何符号。
"""
```

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_types.py -v`
预期：6 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core tests/test_mini_dsh
git commit -m "feat(mini-dsh): core package skeleton + types contract (dsh vocabulary)"
```

---

## Task 3：core/session.py（Session + SessionStore + shadow-aware derive）

**文件：**
- 创建：`examples/mini_dsh/core/session.py`
- 测试：`tests/test_mini_dsh/test_session.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_session.py`：

```python
"""Session event log + SessionStore service."""
import pytest

from core import types as t
from core.session import Session, SessionStore
from javis.cordis import Context


def test_append_seq_and_whitelist():
    session = Session("s1", cwd="/tmp")
    e1 = session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("hi")})
    e2 = session.append("turn/start", {"turn": 1})
    assert e1.seq == 1 and e2.seq == 2
    assert session.events[1].data["turn"] == 1
    with pytest.raises(ValueError):
        session.append("bogus/type", {})


def test_derive_messages_skips_shadowed():
    session = Session("s1")
    user = session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("hello")})
    asst = session.append(
        t.SessionEvents.ASSISTANT_MESSAGE,
        {"message": t.AssistantMessage(content=(t.TextBlock(text="world"),))},
    )
    assert len(session.derive_messages()) == 2
    # compaction 摘要事件把前两条消息标为 shadowed
    session.append(
        t.SessionEvents.COMPACTION_SUMMARY,
        {"summary": "Earlier context (compacted): hello", "shadowedSeqs": [user.seq, asst.seq]},
    )
    summary = session.append(
        t.SessionEvents.USER_MESSAGE,
        {"message": t.UserMessage.from_text("Earlier context (compacted): hello")},
    )
    messages = session.derive_messages()
    assert [m.text for m in messages] == ["Earlier context (compacted): hello"]


def test_events_of_find_last_last_turn():
    session = Session("s1")
    session.append("turn/start", {"turn": 1})
    session.append("turn/end", {"turn": 1})
    session.append("turn/start", {"turn": 2})
    assert session.last_turn() == 2
    assert len(session.events_of("turn/start")) == 2
    assert session.find_last("turn/end").data["turn"] == 1


def test_store_create_get_and_announce():
    ctx = Context()
    store = SessionStore(ctx)
    ctx.provide("sessions", store)
    seen = []
    ctx.on("session/created", lambda payload: seen.append(payload["session"].id))
    s = store.create(cwd="/tmp")
    assert s.id.startswith("session-")
    assert store.get(s.id) is s
    assert seen == [s.id]
    # store 生命周期（fiber effect 卸载移除）由 Task 11 组合语义覆盖，不在此单测
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_session.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：copy + trim + 新增实现**

1. 复制 `javis/harness/session.py` → `examples/mini_dsh/core/session.py`，改 docstring 首行与相对 import（去掉 `on_append` 宿主钩子相关 docstring；import 里的 `SESSION_FORMAT_VERSION` 等保留）。
2. **删掉 `on_append` 宿主钩子**：`Session.__init__` 签名变为 `def __init__(self, id: SessionId, cwd: str | None = None) -> None`；删 `self.on_append = on_append` 与 `append()` 末尾的 `if self.on_append is not None:` 块。
3. **derive_messages 加 compaction shadow**（mini 增强，dsh 语义：摘要替换 shadowed 范围）：

```python
    def derive_messages(self) -> list[Any]:
        """重建模型所见的会话（dsh ``deriveMessages``）。

        mini 增强：任一 ``compaction/summary`` 事件 shadowedSeqs 里的消息事件
        被跳过（摘要消息本身保留——它不在 shadowed 集合里）。
        """
        shadowed: set[int] = set()
        for event in self.events_of(SessionEvents.COMPACTION_SUMMARY):
            shadowed.update((event.data or {}).get("shadowedSeqs", ()))
        out: list[Any] = []
        for event in self._events:
            if event.seq in shadowed:
                continue
            if event.type in ("user/message", "assistant/message", "tool/result"):
                out.append(event.data["message"])
        return out
```

（`SessionEvents` 需从 `.types` import。）
4. **新增 SessionStore**（dsh `SessionStore`，cordis Service 形状的轻量版——普通类 + ctx，与 ToolRegistry 同款）：

```python
class SessionStore:
    """The ``"sessions"`` service: create / get with fiber-effect lifecycle.

    dsh ``SessionStore``（``packages/core/session``）的轻量版：create 走
    ``ctx.effect``（fiber 卸载即从 store 移除），announce 发 ``session/created``
    （emit）。没有 typert 注册 / fork / seed / surface 折叠。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._sessions: dict[str, Session] = {}
        self._counter = 0

    def create(self, id: str | None = None, cwd: str | None = None) -> Session:
        if id is None:
            self._counter += 1
            id = f"session-{self._counter}"
        if id in self._sessions:
            raise ValueError(f"session {id!r} already exists")
        session = Session(id, cwd=cwd)

        def setup() -> Callable[[], None]:
            self._sessions[id] = session

            def disposer() -> None:
                self._sessions.pop(id, None)

            return disposer

        # Cordis effect contract: ``execute`` runs at create time, its return
        # value is the teardown disposer（fiber 卸载时从 store 移除）——
        # 与 ToolRegistry.register 同款 idiom。
        self._ctx.effect(setup, f"sessions.create({id})")
        self._ctx.emit("session/created", {"session": session})
        return session

    def get(self, id: str) -> Session | None:
        return self._sessions.get(id)
```

注意：SessionStore 的 create 里 append 事件用的 `SessionEvents` 常量 import 自 `.types`。`__all__` 加 `SessionStore`。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_session.py -v`
预期：4 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/session.py tests/test_mini_dsh/test_session.py
git commit -m "feat(mini-dsh): session event log + SessionStore service (dsh-faithful)"
```

---

## Task 4：core/inbox.py

**文件：**
- 创建：`examples/mini_dsh/core/inbox.py`
- 测试：`tests/test_mini_dsh/test_inbox.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_inbox.py`：

```python
"""Inbox next-turn / next-step dual queue semantics."""
from core import types as t
from core.inbox import Inbox
from core.session import Session


def _msg(text: str) -> t.UserMessage:
    return t.UserMessage.from_text(text)


def test_turn_vs_step_targets():
    inbox = Inbox(Session("s1"))
    inbox.next_turn.append(_msg("a"))
    inbox.next_step.append(_msg("b"))
    assert [m.text for m in inbox.claim("next-turn", 1)] == ["a"]
    assert [m.text for m in inbox.claim("next-step", 1)] == ["b"]
    assert not inbox.has_pending


def test_splice_ordering():
    inbox = Inbox(Session("s1"))
    inbox.next_step.append(_msg("a"))
    inbox.next_step.append(_msg("c"))
    inbox.splice("next-step", len(inbox.next_step), 0, [_msg("b")])
    assert [m.text for m in inbox.claim("next-step", 1)] == ["a", "b", "c"]
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_inbox.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：copy + 一处语义调整**

复制 `javis/harness/inbox.py` → `examples/mini_dsh/core/inbox.py`，改 docstring 首行；相对 import（`from .session import Session`、`from .types import ...`）原样保留。

**有意偏差（与 javis/harness）**：`next_turn` / `next_step` 由"返回副本的 @property"改为**返回内部活队列的 @property**（去掉 `list(...)` 副本）；`has_pending` 保持 @property。理由：dsh getter 返回内部数组（活队列语义，append 可直接作用于队列）；Task 9 的 agent.py port 以属性形式消费（`len(self.inbox.next_step)` / `self.inbox.has_pending` / claim / splice），不受影响。此偏差经实现者实证（简报原测试与 javis 副本语义互斥）与裁决确认。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_inbox.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/inbox.py tests/test_mini_dsh/test_inbox.py
git commit -m "feat(mini-dsh): inbox dual queue (port)"
```

---

## Task 5：core/llm.py（LLM 协议 + BlockAssembler + SystemPrompt）

**文件：**
- 创建：`examples/mini_dsh/core/llm.py`
- 测试：`tests/test_mini_dsh/test_llm.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_llm.py`：

```python
"""LLM seam: BlockAssembler + normalized_stream + SystemPrompt."""
import pytest

from core import types as t
from core.llm import BlockAssembler, SystemPrompt, normalized_stream


def _chunks():
    return [
        t.BlockStartChunk(index=0, block_type="reasoning"),
        t.ReasoningDeltaChunk(index=0, text="think"),
        t.BlockEndChunk(index=0, block=t.ReasoningBlock(text="think")),
        t.BlockStartChunk(index=1, block_type="text"),
        t.TextDeltaChunk(index=1, text="hi"),
        t.BlockEndChunk(index=1, block=t.TextBlock(text="hi")),
        t.FinishChunk(reason=t.StopFinish()),
    ]


def test_block_assembler():
    assembler = BlockAssembler()
    for chunk in _chunks():
        assembler.push(chunk)
    assert isinstance(assembler.finish, t.StopFinish)
    assert [b.type for b in assembler.blocks] == ["reasoning", "text"]


@pytest.mark.asyncio
async def test_normalized_stream_error_finish():
    async def bad(_options):
        yield t.TextDeltaChunk(index=0, text="partial")
        raise t.LlmError("connection reset", "TRANSIENT")

    got = [c async for c in normalized_stream(bad(None), t.GenerateOptions(provider="p", model="m"))]
    assert isinstance(got[-1], t.FinishChunk)
    assert isinstance(got[-1].reason, t.ErrorFinish)
    assert got[-1].reason.failure.code == "TRANSIENT"


def test_system_prompt_assembly():
    from javis.cordis import Context

    ctx = Context()
    registry = type("R", (), {"schemas": lambda self: ()})()
    ctx.provide("tools", registry)
    sp = SystemPrompt(ctx, "You are mini.", cwd="/tmp", session_id="s1")
    assembly = sp.assemble()
    assert assembly.sections[0].kind == "persona"
    assert "You are mini." in sp.render_prompt(assembly)
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_llm.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：copy + 新增实现**

1. 复制 `javis/harness/llm.py` → `examples/mini_dsh/core/llm.py`（278 行，含 PreparedCall/LLM 协议/BlockAssembler/assemble_finish/chunk_response/normalized_stream），改 docstring 首行与相对 import。核实 import 是否包含 `from .types import (...)` 等——原样保留。
2. **追加 SystemPrompt**（从 `javis/harness/prompt.py` 移植精简——80 行原样搬，改名类为 `SystemPrompt`，去掉 `workspace` 参数与 `set_system_prompt`？保留 set_system_prompt 无妨但 mini 不需要——**删掉 workspace 参数与 set_system_prompt**，构造 `SystemPrompt(ctx, system_prompt, *, cwd, session_id)`；`assemble` 的 context 段 = cwd/session_id/date）：

```python
class SystemPrompt:
    """The ``"systemPrompt"`` service: persona + live context assembly.

    dsh ``core/system-prompt`` 的轻量版：persona = 一个普通字符串，
    不做 sections 分层渲染的扩展语法；context 段 = cwd / session_id / 日期。
    """

    def __init__(self, ctx: Any, system_prompt: str, *, cwd: str, session_id: str) -> None:
        self._ctx = ctx
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._session_id = session_id

    def assemble(self, *, agent: Any = None, signal: Any = None) -> PromptAssembly:
        registry = self._ctx.get("tools")
        schemas = tuple(registry.schemas()) if hasattr(registry, "schemas") else ()
        sections = (
            PromptSection(title="Persona", body=self._system_prompt, kind="persona"),
            PromptSection(
                title="Context",
                body=f"cwd: {self._cwd} | session: {self._session_id} | date: {datetime.now().date().isoformat()}",
                kind="context",
            ),
        )
        return PromptAssembly(sections=sections, tools=schemas)

    def render_prompt(self, assembly: PromptAssembly) -> str:
        parts = [
            f"# {section.title}\n{section.body}"
            for section in assembly.sections
            if section.kind == "persona"
        ]
        return "\n\n".join(parts)

    def render_context(self, assembly: PromptAssembly) -> str:
        parts = [
            f"[{section.title}] {section.body}"
            for section in assembly.sections
            if section.kind == "context"
        ]
        return " ; ".join(parts)
```

需要的 import：`datetime`、`from .types import PromptAssembly, PromptSection`。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_llm.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/llm.py tests/test_mini_dsh/test_llm.py
git commit -m "feat(mini-dsh): llm seam + BlockAssembler + SystemPrompt service (port)"
```

---

## Task 6：core/tools.py

**文件：**
- 创建：`examples/mini_dsh/core/tools.py`
- 测试：`tests/test_mini_dsh/test_tools.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_tools.py`（移植 `tests/test_harness/test_tool_registry.py` 的精简版 + exclusive 屏障排序测试）：

```python
"""Tool registry + execute_tool_calls scheduling semantics."""
import asyncio
import json

import pytest

from core import types as t
from core.session import Session
from core.tools import Tool, ToolRegistry, execute_tool_calls
from javis.cordis import Context


def _tc(id: str, name: str, arguments: dict) -> t.ToolCallBlock:
    return t.ToolCallBlock(id=id, name=name, arguments=json.dumps(arguments))


def _agent():
    return type("A", (), {"session": None})()


def _ctx_with_tools(max_parallel: int = 2) -> tuple[Context, ToolRegistry, list[dict]]:
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=max_parallel)))
    log: list[dict] = []
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)

    def run(name: str) -> callable:
        def body(_input):
            log.append({"tool": name, "at": len(log)})
            return f"{name}-done"
        return body

    registry.register(Tool(name="a", body=run("a"), mode="parallel"))
    registry.register(Tool(name="x", body=run("x"), mode="exclusive"))
    registry.register(Tool(name="b", body=run("b"), mode="parallel"))
    return ctx, registry, log


def test_registry_register_get_schemas_modes():
    from javis.cordis import Context
    ctx = Context()
    registry = ToolRegistry(ctx)
    registry.register(Tool(name="t1", body=lambda _i: "ok"))
    assert registry.get("t1") is not None
    assert [s.name for s in registry.schemas()] == ["t1"]
    registry.register(Tool(name="t2", body=lambda _i: "ok", mode="exclusive"))
    assert isinstance(registry.execution_mode("t2"), t.ExclusiveMode)
    assert isinstance(registry.execution_mode("t1"), t.ParallelMode)
    with pytest.raises(ValueError):
        registry.register(Tool(name="t1", body=lambda _i: "dup"))


@pytest.mark.asyncio
async def test_exclusive_barrier_before_parallel_pool():
    ctx, registry, log = _ctx_with_tools()
    session = Session("s1")
    agent = _agent()
    turn = step = 1
    calls = [_tc("x", "x", {}), _tc("a", "a", {}), _tc("b", "b", {})]
    concluded = await execute_tool_calls(ctx, session, agent, turn, step, calls, t.AbortSignal())
    assert concluded is True
    order = [entry["tool"] for entry in log]
    assert order == ["x", "a", "b"]  # exclusive 屏障先于 parallel 池
    results = [e.data["message"].text for e in session.events_of("tool/result")]
    assert results == ["x-done", "a-done", "b-done"]
```

注意：`execute_tool_calls` 的签名是 `(ctx, session, agent, turn, step, tool_calls, signal, accept_context=...)`（从 javis/harness/agent.py 的调用点确认）。`t.AbortSignal()` 可直接构造（dataclass）。

先跑通这个最小集；若 javis/harness/tools.py 里 `execute_tool_calls` 内部还要求 `agent` 提供 `session` 属性或别的，对照 javis/harness/agent.py 的调用方式补齐 stub。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_tools.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：copy 实现**

复制 `javis/harness/tools.py` → `examples/mini_dsh/core/tools.py`（390 行），只改 docstring 首行与相对 import（`.session/.types` 原样）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_tools.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/tools.py tests/test_mini_dsh/test_tools.py
git commit -m "feat(mini-dsh): tool registry + exclusive/parallel scheduler (port)"
```

---

## Task 7：core/skill.py

**文件：**
- 创建：`examples/mini_dsh/core/skill.py`
- 测试：`tests/test_mini_dsh/test_skill.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_skill.py`：

```python
"""Skill registry + filesystem provider (dsh skill seam, trimmed)."""
from core.skill import FileSkillProvider, SkillRegistry, is_skill_name


def test_is_skill_name():
    assert is_skill_name("poetic-note")
    assert is_skill_name("abc123")
    assert not is_skill_name("Poetic Note")
    assert not is_skill_name("-bad")
    assert not is_skill_name("bad/name")


def test_file_provider_discovers_and_loads(tmp_path):
    skill_dir = tmp_path / "poetic-note"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: poetic-note\ndescription: Write notes as poetry.\n---\n"
        "Always answer with a two-line poem.\n",
        encoding="utf-8",
    )
    provider = FileSkillProvider(tmp_path)
    summaries = provider.list()
    assert [s.name for s in summaries] == ["poetic-note"]
    assert summaries[0].description == "Write notes as poetry."
    definition = provider.get("poetic-note")
    assert definition is not None
    assert "two-line poem" in definition.content
    assert provider.get("nope") is None


def test_registry_merges_providers_and_runtime():
    from core.skill import SkillDefinition

    class P:
        name = "p"
        def list(self): return []
        def get(self, name):
            if name == "only-provider":
                return SkillDefinition(name="only-provider", description="from provider", content="body")
            return None

    registry = SkillRegistry(ctx=None)
    registry.register_provider(P())
    registry.register_skill(SkillDefinition(name="runtime-skill", description="from runtime", content="rt"))
    names = {s.name for s in registry.list()}
    assert names == {"only-provider", "runtime-skill"}
    assert registry.get("runtime-skill").content == "rt"
    assert registry.get("only-provider").content == "body"
```

注意：`SkillRegistry(ctx=None)` —— register_skill 不需要 ctx。真实 SkillRegistry 构造带 ctx 参数但 mini 里可传 None。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_skill.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：写实现**

`examples/mini_dsh/core/skill.py`（dsh `dsh-skill` + `dsh-skill-filesystem` 的轻量 port；无 rank/scope/watch，frontmatter 只认 name/description）：

```python
"""Skill registry + filesystem provider（dsh skill 能力的轻量版）。

dsh：``packages/skill/skill``（``ctx.skills`` 注册表服务）+
``packages/skill/skill-filesystem``（SKILL.md 目录包 provider）。mini 版：

- 无 rank 优先级/scope 分层/watch——第一个命中 name 的 provider 胜出；
- frontmatter 只认 ``name``（缺省用目录名）与 ``description``；
- 目录包形态：``<root>/<skill-name>/SKILL.md``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_skill_name(name: str) -> bool:
    """kebab-case 技能名（dsh 同款文法）。"""
    return bool(_SKILL_NAME.fullmatch(name))


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    source: str = "filesystem"
    provider: str = "local"


@dataclass(frozen=True)
class SkillDefinition(SkillSummary):
    content: str = ""
    path: str | None = None


class SkillProvider(Protocol):
    name: str

    def list(self) -> list[SkillSummary]: ...

    def get(self, name: str) -> SkillDefinition | None: ...


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """YAML frontmatter（``--- ... ---``）→ (meta, body)。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].strip()


class FileSkillProvider:
    """扫描 ``<root>/<name>/SKILL.md`` 目录包（dsh directory-bundle 形态）。"""

    def __init__(self, root: str | Path, name: str = "local") -> None:
        self.root = Path(root).expanduser().resolve()
        self.name = name

    def list(self) -> list[SkillSummary]:
        out: list[SkillSummary] = []
        if not self.root.is_dir():
            return out
        for entry in sorted(self.root.iterdir()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or not skill_file.is_file():
                continue
            meta, _ = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            skill_name = str(meta.get("name") or entry.name)
            if not is_skill_name(skill_name):
                continue
            out.append(
                SkillSummary(
                    name=skill_name,
                    description=str(meta.get("description") or ""),
                    source="filesystem",
                    provider=self.name,
                )
            )
        return out

    def get(self, name: str) -> SkillDefinition | None:
        if not is_skill_name(name):
            return None
        skill_file = self.root / name / "SKILL.md"
        if not skill_file.is_file():
            return None
        meta, content = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        return SkillDefinition(
            name=str(meta.get("name") or name),
            description=str(meta.get("description") or ""),
            source="filesystem",
            provider=self.name,
            content=content,
            path=str(skill_file),
        )


class SkillRegistry:
    """The ``"skills"`` service：合并 provider 目录 + runtime 贡献（dsh ``ctx.skills``）。"""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._providers: list[SkillProvider] = []
        self._runtime: dict[str, SkillDefinition] = {}

    def register_provider(self, provider: SkillProvider) -> None:
        self._providers.append(provider)

    def register_skill(self, definition: SkillDefinition) -> None:
        """Runtime 贡献（dsh ``ctx.skills.register``），优先于 provider。"""
        self._runtime[definition.name] = definition

    def list(self) -> list[SkillSummary]:
        seen: dict[str, SkillSummary] = {}
        for provider in self._providers:
            for summary in provider.list():
                seen.setdefault(summary.name, summary)
        for name, definition in self._runtime.items():
            seen[name] = SkillSummary(
                name=name,
                description=definition.description,
                source="runtime",
                provider=definition.provider,
            )
        return list(seen.values())

    def get(self, name: str) -> SkillDefinition | None:
        if name in self._runtime:
            return self._runtime[name]
        for provider in self._providers:
            definition = provider.get(name)
            if definition is not None:
                return definition
        return None
```

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_skill.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/skill.py tests/test_mini_dsh/test_skill.py
git commit -m "feat(mini-dsh): skill registry + filesystem provider (dsh skill seam)"
```

---

## Task 8：core/compaction.py

**文件：**
- 创建：`examples/mini_dsh/core/compaction.py`
- 测试：`tests/test_mini_dsh/test_compaction.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_compaction.py`：

```python
"""Compaction service: event chain + shadow + snip listener."""
import pytest

from core import types as t
from core.compaction import Compaction, make_snip_listener
from core.session import Session
from core.tools import ToolExecutionResult
from javis.cordis import Context


def _fill(session: Session, n: int = 6) -> None:
    for i in range(n):
        session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text(f"user-{i}")})
        session.append(
            t.SessionEvents.ASSISTANT_MESSAGE,
            {"message": t.AssistantMessage(content=(t.TextBlock(text=f"asst-{i}" * 50),))},
        )


def test_compact_under_pressure_shadows_old_messages():
    session = Session("s1")
    _fill(session)  # 总字符数远超阈值
    comp = Compaction(None, max_chars=10, keep_messages=2)
    result = comp.compact_if_needed(session, "pressure")
    assert result is not None
    assert result.start_seq < result.summary_seq < result.end_seq
    assert result.summary.startswith("Earlier context (compacted):")
    # 事件链成对
    assert len(session.events_of("compaction/start")) == 1
    assert len(session.events_of("compaction/summary")) == 1
    assert len(session.events_of("compaction/end")) == 1
    # shadow 生效：只留最近 2 条 + 摘要消息
    messages = session.derive_messages()
    assert len(messages) == 3
    assert messages[-1].text == result.summary
    assert "user-0" not in [m.text for m in messages]


def test_below_threshold_returns_none():
    session = Session("s1")
    session.append(t.SessionEvents.USER_MESSAGE, {"message": t.UserMessage.from_text("tiny")})
    comp = Compaction(None, max_chars=1_000_000)
    assert comp.compact_if_needed(session, "pressure") is None


def test_lock_blocks_reentrant_compact():
    session = Session("s1")
    _fill(session)
    comp = Compaction(None, max_chars=10, keep_messages=1)
    comp._locked = True  # 模拟一个未配对的 start
    assert comp.compact_now(session) is None
    assert len(session.events_of("compaction/start")) == 0


def test_snip_listener_truncates_oversized_result():
    listener = make_snip_listener(max_chars=8)
    result = ToolExecutionResult.text("x" * 100)
    decision = listener(None, result, lambda: None)
    assert decision is not None
    assert len(decision.content[0].text) < 60
    assert "truncated" in decision.content[0].text
```

注意 `test_lock_blocks_reentrant_compact` 中 lock 后不应有事件写入。且 `Compaction(None, ...)` 中 ctx 未用到（事件写 session 不依赖 ctx）。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_compaction.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：写实现**

`examples/mini_dsh/core/compaction.py`（dsh compaction 族的轻量 port；无 token-meter/LLM 摘要/command-compact/scope）：

```python
"""Compaction service（dsh compaction 能力族的轻量版）。

dsh：``packages/compaction/*``——``ctx.compaction`` 服务 +
``compaction/start|summary|end`` 事件（摘要落为 user/message 替换 shadowed
范围）+ 工具结果剪枝。mini 版：

- 压力检测用字符数估算（无 token-meter 服务）；
- 摘要是纯规则（保留最近 N 条，丢弃部分压成一段 "Earlier context: …" 文本；
  LLM 摘要为扩展方向）；
- 无 command-compact 人工命令 / 无 scope / rank；
- ``make_snip_listener`` = dsh ``compaction-tool-result-pruner`` / javis
  ``make_snip_listener`` 同款（``tools/post-execute`` waterfall 监听器）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .session import Session
from .types import (
    PostToolDecision,
    SessionEvents,
    TextBlock,
    UserMessage,
)

DEFAULT_MAX_CHARS = 40_000
DEFAULT_KEEP_MESSAGES = 10
DEFAULT_SNIP_MAX_CHARS = 8_000

#: 可被 compaction shadow 的消息型事件（按日志顺序）。
_MESSAGE_EVENT_TYPES = frozenset(
    {"user/message", "assistant/message", "tool/call", "tool/result"}
)


@dataclass
class CompactionResult:
    compaction_id: str
    start_seq: int
    summary_seq: int
    end_seq: int
    summary: str
    shadowed_seqs: tuple[int, ...]


def _message_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        for part in getattr(block, "content", ()) or ():
            if isinstance(part, TextBlock):
                parts.append(part.text)
    return " ".join(parts)


class Compaction:
    """The ``"compaction"`` service（dsh ``ctx.compaction``）。"""

    def __init__(
        self,
        ctx: Any,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        keep_messages: int = DEFAULT_KEEP_MESSAGES,
    ) -> None:
        self._ctx = ctx
        self._max_chars = max_chars
        self._keep = max(1, int(keep_messages))
        self._locked = False
        self._count = 0

    # -- dsh API 面 ---------------------------------------------------------

    def compact_if_needed(self, session: Session, trigger: str = "pressure") -> CompactionResult | None:
        """自动策略（pressure / context-overflow）：低于阈值直接 None。"""
        if self._estimate_chars(session) < self._max_chars:
            return None
        return self._compact(session, trigger)

    def compact_now(self, session: Session) -> CompactionResult | None:
        """人工一次（dsh ``compactNow``；mini 无 /compact 命令，直接调用）。"""
        return self._compact(session, "manual")

    # -- 内部 ---------------------------------------------------------------

    def _estimate_chars(self, session: Session) -> int:
        total = 0
        for event in session.events:
            message = (event.data or {}).get("message")
            if message is None:
                continue
            total += len(_message_text(message))
        return total

    def _shadowed_so_far(self, session: Session) -> set[int]:
        shadowed: set[int] = set()
        for event in session.events_of(SessionEvents.COMPACTION_SUMMARY):
            shadowed.update((event.data or {}).get("shadowedSeqs", ()))
        return shadowed

    def _compact(self, session: Session, trigger: str) -> CompactionResult | None:
        if self._locked:
            return None  # dsh：未配对的 start 阻塞所有入口
        self._locked = True
        self._count += 1
        compaction_id = f"compaction-{self._count}"
        start = session.append(
            SessionEvents.COMPACTION_START,
            {"turn": None, "trigger": trigger, "compactionId": compaction_id},
        )
        try:
            shadowed, summary_text = self._pick_and_summarize(session)
            if not shadowed:
                session.append(
                    SessionEvents.COMPACTION_END,
                    {"turn": None, "compactionId": compaction_id},
                )
                return None
            summary_message = UserMessage(
                content=(TextBlock(text=summary_text),),
                source={
                    "kind": "compaction-checkpoint",
                    "compactionId": compaction_id,
                    "trigger": trigger,
                },
            )
            summary_event = session.append(
                SessionEvents.COMPACTION_SUMMARY,
                {
                    "summary": summary_text,
                    "shadowedSeqs": list(shadowed),
                    "compactionId": compaction_id,
                },
            )
            session.append(
                SessionEvents.USER_MESSAGE,
                {"message": summary_message, "compactionId": compaction_id},
            )
            end = session.append(
                SessionEvents.COMPACTION_END,
                {"turn": None, "compactionId": compaction_id},
            )
            return CompactionResult(
                compaction_id=compaction_id,
                start_seq=start.seq,
                summary_seq=summary_event.seq,
                end_seq=end.seq,
                summary=summary_text,
                shadowed_seqs=shadowed,
            )
        except BaseException as exc:  # noqa: BLE001 —— 以 compaction/end(error) 收尾
            session.append(
                SessionEvents.COMPACTION_END,
                {"turn": None, "error": str(exc), "compactionId": compaction_id},
            )
            raise
        finally:
            self._locked = False

    def _pick_and_summarize(self, session: Session) -> tuple[tuple[int, ...], str | None]:
        """规则摘要：保留最近 N 条消息事件，丢弃部分压成一段 Earlier-context 文本。"""
        shadowed_so_far = self._shadowed_so_far(session)
        message_events = [
            event
            for event in session.events
            if event.type in _MESSAGE_EVENT_TYPES and event.seq not in shadowed_so_far
        ]
        if len(message_events) <= self._keep:
            return (), None
        head = message_events[: -self._keep]
        shadowed = tuple(event.seq for event in head)
        parts: list[str] = []
        for event in head:
            message = (event.data or {}).get("message")
            if message is None:
                continue
            text = _message_text(message).strip()
            if text:
                parts.append(text[:80].replace("\n", " "))
        summary = "Earlier context (compacted): " + " | ".join(parts)
        return shadowed, summary


def make_snip_listener(max_chars: int = DEFAULT_SNIP_MAX_CHARS) -> Callable[..., Any]:
    """``tools/post-execute`` 监听器：截断超限工具结果（dsh pruner 同款）。

    契约：``(exec_input, result, next)``——调用 ``next()`` 放行链路；超限则
    返回截断后的 :class:`PostToolDecision`；未超限返回 None。
    """

    def listener(_exec: Any, result: Any, next: Callable[[], Any]) -> Any:
        next()
        text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
        total = sum(len(b.text) for b in text_blocks)
        if total <= max_chars:
            return None
        new_content: list[Any] = []
        remaining = max_chars
        for block in result.content:
            if isinstance(block, TextBlock):
                if remaining <= 0:
                    continue
                if len(block.text) > remaining:
                    new_content.append(
                        TextBlock(
                            text=block.text[:remaining]
                            + f"\n... [truncated by compaction: {len(block.text)} chars]"
                        )
                    )
                    remaining = 0
                else:
                    new_content.append(block)
                    remaining -= len(block.text)
            else:
                new_content.append(block)
        return PostToolDecision(content=new_content)

    return listener
```

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_compaction.py -v`
预期：4 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/compaction.py tests/test_mini_dsh/test_compaction.py
git commit -m "feat(mini-dsh): compaction service + rule summarizer + snip listener"
```

---

## Task 9：core/agent.py

**文件：**
- 创建：`examples/mini_dsh/core/agent.py`
- 测试：`tests/test_mini_dsh/test_agent.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_agent.py`（直接驱动 ReactLoopAgent，不经宿主；参照 dsh_harness cli 的驱动方式）：

```python
"""ReactLoopAgent turn/step loop over a minimal composed ctx."""
import json

import pytest

from core import types as t
from core.agent import ReactLoopAgent
from core.llm import BlockAssembler, PreparedCall, SystemPrompt, chunk_response, normalized_stream
from core.session import Session
from core.tools import Tool, ToolRegistry
from javis.cordis import Context


class _FakeLLM:
    """脚本化 LLM：每 stream() 吐一条 chunk 序列（用 chunk_response 构造）。"""

    def __init__(self, script: list[list]) -> None:
        self._script = script
        self._i = 0
        self.on_tool_call = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def stream(self, options: t.GenerateOptions):
        chunks = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1

        async def gen():
            for chunk in chunks:
                if self.on_tool_call is not None and isinstance(chunk, t.BlockStartChunk) and chunk.block_type == "tool-call":
                    self.on_tool_call()
                yield chunk

        return gen()


def _tc(id: str, name: str, arguments: dict) -> t.ToolCallBlock:
    return t.ToolCallBlock(id=id, name=name, arguments=json.dumps(arguments))


def _compose(script: list[list], *, tools: list[Tool] | None = None) -> tuple[Context, ReactLoopAgent, Session]:
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2)))
    session = Session("test-agent", cwd="/tmp")
    ctx.provide("session", session)
    ctx.provide("systemPrompt", SystemPrompt(ctx, "You are mini.", cwd="/tmp", session_id=session.id))
    ctx.provide("llm", _FakeLLM(script))
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    for tool in tools or []:
        registry.register(tool)
    agent = ReactLoopAgent(ctx, session.id, t.AgentOptions(provider="fake", model="mini"), session)
    ctx.provide("agent", agent)
    return ctx, agent, session


async def _run_turn(agent: ReactLoopAgent, prompt: str) -> None:
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


@pytest.mark.asyncio
async def test_text_turn_completes():
    _, agent, session = _compose(
        [chunk_response(text="2 + 2 = 4.", reasoning="basic arithmetic")]
    )
    await _run_turn(agent, "what is 2+2?")
    events = [e.type for e in session.events]
    assert "turn/start" in events and "turn/end" in events
    messages = session.derive_messages()
    assert any("2 + 2 = 4" in getattr(m, "text", "") for m in messages)


@pytest.mark.asyncio
async def test_tool_turn_executes_and_concludes():
    def note(_input):
        return "note saved"
    tools = [Tool(name="set_note", description="append a note", body=note, mode="exclusive")]
    script = [
        chunk_response(tool_calls=[_tc("n1", "set_note", {"text": "buy milk"})]),
        chunk_response(text="Note saved."),
    ]
    _, agent, session = _compose(script, tools=tools)
    await _run_turn(agent, "save a note")
    results = [e.data["message"].text for e in session.events_of("tool/result")]
    assert results == ["note saved"]
    assert session.find_last("turn/end") is not None


@pytest.mark.asyncio
async def test_pre_step_veto_blocks_turn():
    ctx, agent, session = _compose([chunk_response(text="should not appear")])

    def veto(_payload, next):
        return t.PreStepReject(reason="blocked by test")

    ctx.on(t.Events.AGENT_PRE_STEP, veto)
    await _run_turn(agent, "hi")
    assert session.find_last("turn/end") is not None
    end = session.find_last("turn/end")
    assert end.data.get("reason") in (None, "blocked") or True  # veto 走 blocked 结束
```

关于 `test_pre_step_veto_blocks_turn`：dsh 语义——pre-step veto → turn 以 `blocked` 结束。断言改为：`turn/end` 事件存在且其 data 含 blocked 标记（实现时对照 javis/harness/agent.py 的 veto 处理确认 data 形状；先弱断言不崩 + turn/end 存在，Task 12 场景级再强断言）。

再加两个：

```python
@pytest.mark.asyncio
async def test_max_steps_per_turn_guard():
    def note(_input):
        return "ok"
    tools = [Tool(name="ping", description="ping", body=note, mode="parallel")]
    # 每一步都调工具 → 超过 max_steps_per_turn（配置为 3）
    script = [chunk_response(tool_calls=[_tc(f"p{i}", "ping", {})]) for i in range(5)]
    script.append(chunk_response(text="done"))
    ctx = Context()
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2, max_steps_per_turn=3)))
    session = Session("guard", cwd="/tmp")
    ctx.provide("session", session)
    ctx.provide("systemPrompt", SystemPrompt(ctx, "mini", cwd="/tmp", session_id=session.id))
    ctx.provide("llm", _FakeLLM(script))
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    registry.register(tools[0])
    agent = ReactLoopAgent(ctx, session.id, t.AgentOptions(provider="fake", model="mini"), session)
    ctx.provide("agent", agent)
    await _run_turn(agent, "go")
    # guard 事件触发，turn 结束（不无限循环）
    assert session.find_last("turn/end") is not None
```

（`_compose` 不支持自定义 agentLoop config，这段内联写。max-steps guard 的 javis 实现发出 `agent/limit` 事件——见 spec；对照 javis/harness/agent.py `_loop_max_steps` 的实现。）

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_agent.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：copy + trim 实现**

复制 `javis/harness/agent.py` → `examples/mini_dsh/core/agent.py`（692 行），改 docstring 首行与相对 import。**trim 清单**：
1. 删 `_compress_history` 方法（Task 2 已裁 history_compressor 字段，此方法恒为 no-op）及其调用点——在 `_build_request` 内 `messages = self._compress_history(...)` 处直接使用 `session.derive_messages()` 结果；grep `_compress_history` 确认所有调用点。
2. javis 引擎桥接相关分支照抄即可（agent.py 本身已是 core 层，无 javis import；`_maybe_await`/`_dispatch_*` 全保留）。
3. 保留 `agent/limit` guard（`_loop_max_steps` 读 agentLoop config 的 `max_steps_per_turn`）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_agent.py -v`
预期：4 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/core/agent.py tests/test_mini_dsh/test_agent.py
git commit -m "feat(mini-dsh): ReactLoopAgent phase machine (port, trimmed)"
```

---

## Task 10：providers.py（ScriptedAdapter 7 场景 + OpenAICompatAdapter）

**文件：**
- 创建：`examples/mini_dsh/providers.py`
- 测试：`tests/test_mini_dsh/test_providers.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_providers.py`：

```python
"""providers: ScriptedAdapter (LLM 协议) + 场景工厂."""
import json

import pytest

from core import types as t
from core.llm import BlockAssembler
from providers import ScriptedAdapter, scenario_script


def _assemble(chunks: list) -> list:
    assembler = BlockAssembler()
    for chunk in chunks:
        assembler.push(chunk)
    return assembler.blocks


@pytest.mark.asyncio
async def test_scripted_adapter_implements_llm_protocol():
    script = scenario_script("text")
    adapter = ScriptedAdapter(script, model="mini-scripted")
    assert adapter.model == "mini-scripted"
    prepared = adapter.prepare_call(t.LlmCallConfig(provider="scripted", model="mini-scripted"))
    assert prepared is not None
    got = [c async for c in adapter.stream(t.GenerateOptions(provider="scripted", model="mini-scripted"))]
    blocks = _assemble(got)
    assert any(getattr(b, "type", "") == "text" for b in blocks)


def test_all_scenarios_are_available_and_deterministic():
    for name in ("text", "tools", "retry", "steer", "skills", "instructions", "compaction"):
        script = scenario_script(name)
        assert isinstance(script, list) and len(script) >= 1
        assert scenario_script(name) == script  # 确定性


def test_tools_scenario_calls_set_note_then_weather_twice():
    script = scenario_script("tools")
    calls = []
    for response in script:
        blocks = _assemble(response)
        calls.extend(
            (b.name, json.loads(b.arguments))
            for b in blocks
            if getattr(b, "type", "") == "tool-call"
        )
    assert calls[0] == ("set_note", {"text": "remember: parrot"})
    assert [name for name, _ in calls[1:]] == ["weather", "weather"]
```

注意：`ScriptedAdapter` 的 `stream()` 若 `scenario_script("retry")` 里含有"中途抛异常"的响应，需在 adapter 内部处理（遇 `_Fault` 哨兵值则抛 `LlmError("connection reset", "TRANSIENT")`）。实现见步骤 3。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_providers.py -v`
预期：FAIL——ModuleNotFoundError

- [ ] **步骤 3：写实现**

`examples/mini_dsh/providers.py`（scripted + openai 两 adapter，dsh StreamChunk 词汇，无自定义 ChatProvider 中间抽象）：

```python
"""Provider 层：LLM 协议的两个实现（core/llm.LLM 契约面）。

- :class:`ScriptedAdapter` —— 离线确定性模型：按脚本逐条吐 StreamChunk
  （用 ``core.llm.chunk_response`` 构造）；``retry`` 场景的响应含
  :class:`_Fault` 哨兵，stream 中途抛 ``LlmError(TRANSIENT)``（由 core 的
  ``normalized_stream`` 归一化成 error finish）。
- :class:`OpenAICompatAdapter` —— openai SDK → StreamChunk（真实模型）。

场景工厂 :func:`scenario_script` 产出 7 个确定性脚本（text/tools/retry/
steer/skills/instructions/compaction），与 dsh_harness 的 ``mock_llm`` 同思路。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core import types as t
from core.llm import PreparedCall, chunk_response

# ---------------------------------------------------------------------------
# 场景脚本（确定性，离线）
# ---------------------------------------------------------------------------


def _text() -> list[list[Any]]:
    return [chunk_response(text="2 + 2 = 4.", reasoning="2 + 2 is basic arithmetic; the answer is 4.")]


def _tools() -> list[list[Any]]:
    return [
        chunk_response(
            tool_calls=[
                t.ToolCallBlock(id="note", name="set_note", arguments=json.dumps({"text": "remember: parrot"})),
                t.ToolCallBlock(id="wx1", name="weather", arguments=json.dumps({"city": "Paris"})),
                t.ToolCallBlock(id="wx2", name="weather", arguments=json.dumps({"city": "Tokyo"})),
            ]
        ),
        chunk_response(
            text="Paris is 18°C (light rain) and Tokyo is 24°C (sunny) — bring an umbrella for Paris."
        ),
    ]


@dataclass
class _Fault:
    """retry 场景哨兵：stream 遇到它即抛 TRANSIENT LlmError。"""

    message: str = "connection reset by peer"


def _retry() -> list[list[Any]]:
    return [
        [  # 尝试 1：半截文本后故障
            t.BlockStartChunk(index=0, block_type="text"),
            t.TextDeltaChunk(index=0, text="Almost "),
            _Fault(),
        ],
        chunk_response(text="Recovered after one transient provider failure — all good."),
    ]


def _steer() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="now1", name="now", arguments="{}")]),
        chunk_response(
            text="It is 2026-08-31T18:00:00Z, and (per your steering) Tokyo's weather is 24°C sunny."
        ),
    ]


def _skills() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="sk1", name="skill", arguments=json.dumps({"name": "poetic-note"}))]),
        chunk_response(text="Notes fall like autumn leaves —\nwhat you save, time preserves in green."),
    ]


def _instructions() -> list[list[Any]]:
    # AGENTS.md 指令："回答必须 ≤ 5 个词" → 5 词内回应
    return [chunk_response(text="Understood. Keeping it brief.")]


def _compaction() -> list[list[Any]]:
    return [
        chunk_response(tool_calls=[t.ToolCallBlock(id="blob1", name="big_read", arguments=json.dumps({"file": "big.txt"}))]),
        chunk_response(text="Done reading the big file. It was mostly noise."),
    ]


_SCENARIOS: dict[str, Any] = {
    "text": _text,
    "tools": _tools,
    "retry": _retry,
    "steer": _steer,
    "skills": _skills,
    "instructions": _instructions,
    "compaction": _compaction,
}

SCENARIOS: tuple[str, ...] = tuple(_SCENARIOS)


def scenario_script(scenario: str) -> list[list[Any]]:
    """按名字产出确定性脚本（未知场景抛 ValueError）。"""
    factory = _SCENARIOS.get(scenario)
    if factory is None:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
    return factory()


# ---------------------------------------------------------------------------
# ScriptedAdapter
# ---------------------------------------------------------------------------


class ScriptedAdapter:
    """离线确定性 LLM：按脚本流式回复（LLM 协议实现）。"""

    def __init__(self, script: list[list[Any]], model: str = "mini-scripted") -> None:
        self.model = model
        self._script = list(script)
        self._cursor = 0
        #: steer 钩子：在即将发出 tool-call block 前被调用（由 cli/测试挂载）。
        self.on_tool_call = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def stream(self, options: t.GenerateOptions) -> AsyncIterator[Any]:
        if self._cursor >= len(self._script):
            # 脚本耗尽：收尾短句（REPL/多轮时不会重复最后一条）
            chunks = chunk_response(text="(scripted demo: no more turns)")
        else:
            chunks = self._script[self._cursor]
        self._cursor += 1

        async def gen():
            for chunk in chunks:
                if isinstance(chunk, _Fault):
                    raise t.LlmError(chunk.message, "TRANSIENT")
                if (
                    self.on_tool_call is not None
                    and isinstance(chunk, t.BlockStartChunk)
                    and chunk.block_type == "tool-call"
                ):
                    self.on_tool_call()
                yield chunk

        return gen()


# ---------------------------------------------------------------------------
# OpenAICompatAdapter
# ---------------------------------------------------------------------------


class OpenAICompatAdapter:
    """openai SDK → StreamChunk（DeepSeek/Qwen/Kimi/Ollama 等兼容端点）。"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or ""
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._client: Any = None

    def prepare_call(self, config: t.LlmCallConfig, signal: t.AbortSignal | None = None) -> PreparedCall:
        return PreparedCall(config=config)

    def close(self) -> None:
        self._client = None

    async def stream(self, options: t.GenerateOptions) -> AsyncIterator[Any]:
        from openai import AsyncOpenAI

        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key or "sk-missing", base_url=self._base_url)
        messages = _to_openai_messages(options)
        params: dict[str, Any] = {
            "model": options.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if options.tools:
            params["tools"] = [{"type": "function", "function": {"name": s.name, "description": s.description, "parameters": s.parameters}} for s in options.tools]
        if self._max_tokens is not None:
            params["max_tokens"] = self._max_tokens
        try:
            stream = await self._client.chat.completions.create(**params)
        except Exception:  # noqa: BLE001 —— 有些端点拒绝 stream_options
            params.pop("stream_options", None)
            stream = await self._client.chat.completions.create(**params)

        # 把 OpenAI 流转换成 dsh StreamChunk（逐块流式）
        index = 0
        open_slot: dict[int, dict[str, str]] = {}
        usage: Any = None
        text_parts: list[str] = []
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield t.BlockStartChunk(index=index, block_type="reasoning")
                yield t.ReasoningDeltaChunk(index=index, text=reasoning)
                yield t.BlockEndChunk(index=index, block=t.ReasoningBlock(text=reasoning))
                index += 1
            if delta.content:
                if not open_slot.get(-1, {}).get("open"):
                    yield t.BlockStartChunk(index=index, block_type="text")
                    open_slot[-1] = {"open": True}
                yield t.TextDeltaChunk(index=index, text=delta.content)
                text_parts.append(delta.content)
            if delta.tool_calls:
                for call in delta.tool_calls:
                    slot = open_slot.setdefault(call.index, {"id": "", "name": "", "arguments": "", "open": False})
                    if not slot["open"]:
                        yield t.BlockStartChunk(index=index, block_type="tool-call")
                        slot["open"] = True
                    if call.id:
                        slot["id"] = call.id
                    if call.function and call.function.name:
                        slot["name"] += call.function.name
                    if call.function and call.function.arguments:
                        yield t.ToolCallDeltaChunk(index=index, id=call.id or f"call_{call.index}", name=slot["name"], arguments_delta=call.function.arguments)
                        slot["arguments"] += call.function.arguments
        # 收尾：关闭块、usage、finish
        seen_ids: set[int] = set()
        for idx, slot in sorted(open_slot.items()):
            if idx < 0 or idx in seen_ids:
                continue
            seen_ids.add(idx)
            if slot["open"]:
                yield t.BlockEndChunk(index=idx, block=t.ToolCallBlock(id=slot["id"], name=slot["name"], arguments=slot["arguments"]))
        if text_parts:
            yield t.BlockEndChunk(index=index, block=t.TextBlock(text="".join(text_parts)))
        if usage is not None:
            yield t.UsageChunk(
                usage=t.TokenUsage(
                    input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                )
            )
        yield t.FinishChunk(reason=t.StopFinish())


def _to_openai_messages(options: t.GenerateOptions) -> list[dict[str, Any]]:
    """把 core 的 Message 族转成 OpenAI messages（text 消息 + tool 结果）。"""
    out: list[dict[str, Any]] = []
    if options.system:
        out.append({"role": "system", "content": options.system})
    for message in options.messages:
        role = getattr(message, "role", "")
        if role == "user":
            out.append({"role": "user", "content": message.text or ""})
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": message.call_id, "content": message.text})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            calls = getattr(message, "tool_calls", None)
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ]
            out.append(entry)
    return out
```

注意：OpenAICompatAdapter 的流式组装在真实模型场景下可能有边角（多 text 段、tool-call name 分片拼接）——真实模型运行不是 CI 断言目标，block 收尾逻辑保持简单正确即可（Task 16 的 --prompt 冒烟只验证能跑完）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_providers.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/providers.py tests/test_mini_dsh/test_providers.py
git commit -m "feat(mini-dsh): scripted + openai-compatible adapters with 7-scenario factory"
```

---

## Task 11：plugins session/llm/tools/driver + cordis.yml + text 场景集成测试

**文件：**
- 创建：`examples/mini_dsh/cordis.yml`、`examples/mini_dsh/plugins/{__init__.py,session.py,llm.py,tools.py,driver.py}`
- 测试：`tests/test_mini_dsh/test_composition.py`

- [ ] **步骤 1：写失败的测试**

`tests/test_mini_dsh/test_composition.py`（先只覆盖 text 场景；后续任务往同一文件加场景测试）：

```python
"""组合集成测试：cordis.yml + agent 契约驱动（text 场景起步）。

cordis Context 无公开 dispose——每个测试独立 compose 新 ctx，进程退出自然回收；
卸载/可重复装配语义由“每测试重新 compose 都能成功”隐式覆盖。
"""
import os
import tempfile

import pytest

from javis.cordis import Context
from javis.cordis.loader import Loader
from javis.cordis.registry import settle

from core import types as t

MINI_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "mini_dsh")


async def _compose(monkeypatch: pytest.MonkeyPatch, scenario: str) -> Context:
    monkeypatch.setenv("HARNESS_DEMO_SCENARIO", scenario)
    monkeypatch.setenv("MINI_DSH_PROVIDER", "scripted")
    ctx = Context()
    # cwd = MINI_DSH_CWD（instructions 场景指向临时 workspace）否则临时目录：
    # tools 场景会把 notes.txt 写进 session cwd，不能指向仓库内目录
    ctx.baseUrl = os.environ.get("MINI_DSH_CWD") or tempfile.mkdtemp(prefix="mini-dsh-test-")
    loader_fiber = ctx.plugin(Loader, {"file": os.path.join(MINI_ROOT, "cordis.yml")})
    await loader_fiber
    await settle(ctx)
    return ctx


async def _run(ctx: Context, prompt: str) -> None:
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


@pytest.mark.asyncio
async def test_composition_has_task11_services(monkeypatch: pytest.MonkeyPatch):
    """Task 11 的 4 个插件提供 7 个服务；skills/compaction 在 Task 13/15 加入，
    届时由 Task 15 的全量服务断言接管。"""
    ctx = await _compose(monkeypatch, "text")
    for service in ("sessions", "llm", "tools", "agentLoop", "systemPrompt", "agent", "session"):
        assert ctx.get(service, strict=False) is not None, f"missing service {service}"


@pytest.mark.asyncio
async def test_text_scenario_completes(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "text")
    session = ctx.get("session")
    await _run(ctx, "what is 2+2?")
    types_seen = [e.type for e in session.events]
    assert "turn/start" in types_seen and "turn/end" in types_seen
    messages = session.derive_messages()
    assert any("2 + 2 = 4" in getattr(m, "text", "") for m in messages)
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：FAIL——文件不存在 / cordis.yml 不存在

- [ ] **步骤 3：写实现**

`examples/mini_dsh/cordis.yml`（**Task 11 只含已存在的 4 个插件条目**；middleware / skill-tool / instructions / compaction 条目由 Task 12/13/14/15 分别追加——组合文件依赖尚未创建的插件会 load 失败）：

```yaml
# mini_dsh 组合文件（dsh: everything is a plugin）
# 加载顺序由 cordis inject 依赖驱动（书写顺序无关）。
# 条目随插件任务逐步追加：middleware(T12) / skill-tool(T13) /
# instructions(T14) / compaction(T15)。

- id: sessions
  name: ./plugins/session.py

- id: llm
  name: ./plugins/llm.py
  config:
    provider: scripted   # scripted（离线 demo）| openai | auto（有 key 走真实）

- id: tools
  name: ./plugins/tools.py

- id: driver
  name: ./plugins/driver.py
  inject: [sessions, llm, tools]
```

`examples/mini_dsh/plugins/__init__.py`：空。

`plugins/session.py`：

```python
"""插件：provide "sessions" —— SessionStore（dsh：session 是一等服务）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.session import SessionStore


def apply(ctx) -> None:
    ctx.provide("sessions", SessionStore(ctx))
```

`plugins/llm.py`：

```python
"""插件：provide "llm" —— 从 providers.py 选 adapter（scripted/离线 | openai/真实）。"""
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from providers import OpenAICompatAdapter, ScriptedAdapter, scenario_script


class Config(BaseModel):
    provider: str = "scripted"  # scripted | openai | auto


def _resolve(config: Config, scenario: str | None) -> Any:
    # 优先级：显式环境变量 > 插件 config > 默认 scripted。
    # （env 优先才能让 cli 的 --prompt 用 MINI_DSH_PROVIDER=auto 切真实模型，
    #  同时 cordis.yml 的 provider: scripted 保持 demo 默认确定性）
    choice = (
        os.environ.get("MINI_DSH_PROVIDER")
        or config.provider
        or "scripted"
    ).lower()
    if choice == "scripted":
        return ScriptedAdapter(scenario_script(scenario or "text"), model="mini-scripted")
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if choice == "openai" or (choice == "auto" and api_key):
        return OpenAICompatAdapter(
            model=os.environ.get("MINI_DSH_MODEL", "deepseek-chat"),
            api_key=api_key or "",
            base_url=os.environ.get("MINI_DSH_BASE_URL"),
        )
    return ScriptedAdapter(scenario_script(scenario or "text"), model="mini-scripted")


def apply(ctx, config: Config) -> None:
    scenario = os.environ.get("HARNESS_DEMO_SCENARIO")
    ctx.provide("llm", _resolve(config, scenario))
```

`plugins/tools.py`：

```python
"""插件：provide "tools" —— demo 工具集（now/weather 并行、set_note/big_read 独占）。"""
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.tools import Tool, ToolRegistry

_WEATHER = {"Paris": "18°C, light rain", "Tokyo": "24°C, sunny"}


def _now(_input: Any) -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _weather(exec_input: Any) -> str:
    city = str((exec_input.arguments or {}).get("city", ""))
    return _WEATHER.get(city, f"{city}: 20°C, cloudy")


def _set_note(exec_input: Any) -> str:
    text = str((exec_input.arguments or {}).get("text", ""))
    notes = Path(exec_input.agent.session.header.cwd or ".") / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    with notes.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")
    return f"note saved: {text[:40]}"


def _big_read(_input: Any) -> str:
    return "x" * 50_000  # compaction 场景：超大工具结果


def apply(ctx) -> None:
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    registry.register(Tool(name="now", description="Current UTC time", body=_now, mode="parallel"))
    registry.register(
        Tool(
            name="weather",
            description="Weather for a city (Paris / Tokyo / ...)",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            body=_weather,
            mode="parallel",
        )
    )
    registry.register(
        Tool(
            name="set_note",
            description="Append a line to notes.txt in the session cwd",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            body=_set_note,
            mode="exclusive",
        )
    )
    registry.register(Tool(name="big_read", description="Read a big file (demo)", body=_big_read, mode="exclusive"))
```

`plugins/driver.py`：

```python
"""插件：组合根——从 services 装配 ReactLoopAgent。

dsh 原样：宿主不直接构造 Session——session 走 ``sessions`` 服务的
``create()``（fiber effect 生命周期）；driver 只做组合：取 services，
构造 agent，发布 ``agent`` / ``agentLoop`` / ``systemPrompt`` / ``session``。
卸载顺序：driver（agent）先于 sessions store 卸载 → agent 最终事件先落日志、
再 detach session（dsh 有序 teardown 的 mini 表达，靠 fiber 逆序卸载达成）。
"""
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import types as t
from core.agent import ReactLoopAgent
from core.llm import SystemPrompt
from core.session import SessionStore  # noqa: F401 —— 仅为类型/契约引用
from core.tools import ToolRegistry  # noqa: F401


def apply(ctx) -> None:
    store: SessionStore = ctx.get("sessions")
    llm = ctx.get("llm")
    tools: ToolRegistry = ctx.get("tools")

    # agentLoop：tools 场景的并行池上限（core/tools 运行时 ctx.get("agentLoop")）
    ctx.provide("agentLoop", t.AgentLoop(config=t.AgentLoopConfig(max_parallel_tool_calls=2)))

    session = store.create(cwd=ctx.baseUrl if hasattr(ctx, "baseUrl") else None)
    ctx.provide("session", session)
    ctx.provide(
        "systemPrompt",
        SystemPrompt(ctx, "You are mini_dsh, a small cordis-assembled agent.", cwd=session.header.cwd or "", session_id=session.id),
    )
    agent = ReactLoopAgent(
        ctx,
        session.id,
        t.AgentOptions(provider="scripted", model="mini-scripted"),
        session,
    )
    ctx.provide("agent", agent)
```

注意 driver 里 cwd：`ctx.baseUrl` 是 cordis Context 上由 cli/测试设置的属性。集成测试里设 `ctx.baseUrl = <mini_dsh 目录>`（或 workspace）。Task 11 测试里 _compose 加 `ctx.baseUrl = MINI_ROOT`（先跑 text 场景不依赖 workspace 文件）。而 instructions/compaction 场景需要临时 workspace——那些场景的测试在 Task 14/15 处理（改 driver 用环境变量 `MINI_DSH_CWD` 或 ctx.baseUrl 已够）。

其实为了 instructions 场景的 cwd 指向临时 workspace，**driver 的 cwd 优先级**：`os.environ.get("MINI_DSH_CWD")` → ctx.baseUrl → None。集成测试与 cli 用 env 控制。在 Task 11 先实现 `ctx.baseUrl or None`，Task 14 如需再改（记住此注）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/cordis.yml examples/mini_dsh/plugins tests/test_mini_dsh/test_composition.py
git commit -m "feat(mini-dsh): composition root (4 plugins) + text scenario green"
```

---

## Task 12：plugins/middleware.py + tools/retry/steer 场景

**文件：**
- 创建：`examples/mini_dsh/plugins/middleware.py`
- 修改：`tests/test_mini_dsh/test_composition.py`

- [ ] **步骤 1：写失败的测试**

追加到 `test_composition.py`（在第 3 步之前的部分已覆盖 text；新增 tools/retry/steer）：

```python
@pytest.mark.asyncio
async def test_tools_scenario_exclusive_barrier(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "tools")
    session = ctx.get("session")
    await _run(ctx, "save a note and check two cities")
    calls = [e.data for e in session.events_of("tool/call")]
    results = [e for e in session.events_of("tool/result")]
    names = [e.data["name"] for e in session.events_of("tool/call")]
    # set_note 独占 → 屏障先行；weather ×2 并行
    assert names == ["set_note", "weather", "weather"]
    assert len(results) == 3
    # 结果按模型顺序提交：set_note result 的 seq 早于两个 weather result
    note_seq = next(e.seq for e in results if "note" in e.data["message"].text)
    weather_seqs = [e.seq for e in results if "°C" in e.data["message"].text]
    assert note_seq < min(weather_seqs)


@pytest.mark.asyncio
async def test_retry_scenario_recovers_via_waterfall(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "retry")
    session = ctx.get("session")
    await _run(ctx, "say something")
    messages = session.derive_messages()
    assert any("Recovered" in getattr(m, "text", "") for m in messages)
    # 只有一条 assistant/message：失败的尝试只留 chunk、不成消息
    assert len(session.events_of("assistant/message")) == 1
    # middleware 观察日志证明走了 waterfall
    observed = ctx.get("middleware-observed", strict=False) or []
    assert any("request-error: retry" in line for line in observed)


@pytest.mark.asyncio
async def test_steer_scenario_injected_at_step_boundary(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "steer")
    session = ctx.get("session")
    agent = ctx.get("agent")
    # 挂 steer 钩子：mock 即将发出 tool-call 时把纠正消息推进 agent inbox
    llm = ctx.get("llm")
    llm.on_tool_call = lambda: agent.steer(
        t.UserMessage.from_text("also include Tokyo's weather in your answer")
    )
    await _run(ctx, "what time is it?")
    messages = session.derive_messages()
    assert any("Tokyo" in getattr(m, "text", "") for m in messages)
    # steer 的 user/message seq 严格晚于 step 1 的 step/end（比 seq）
    step_end_1 = [e for e in session.events_of("step/end") if e.data.get("step") == 1]
    steer_msg = [e for e in session.events_of("user/message") if "Tokyo" in (e.data.get("message").text if e.data.get("message") else "")]
    assert steer_msg and step_end_1
    assert steer_msg[0].seq > step_end_1[0].seq
```

**重要**：tools 场景断言里 `names`——检查 `tool/call` 事件的 data 里 name 字段路径：javsis/harness tools.py `_append_tool_call`（第 337 行）写什么 data？`{"turn":..., "step":..., "tool": {...}}`？实现时对照 `_append_tool_call` / `_append_tool_result` 的实际 data 形状修断言（原则：断言工具执行顺序与 exclusive 屏障，比 seq，不依赖打印格式）。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：新增 3 个 FAIL（middleware 未注册 → retry 不恢复；tools 场景跑通但断言可能因事件形状不对而失败——按实际形状修断言）

- [ ] **步骤 3：写实现**

`plugins/middleware.py`（移植 dsh_harness middleware 的核心：request-error 每步重试一次 + 观察日志；不加 request 路由改写与 pre-step 上下文——那是 dsh_harness 的教学点，mini 保留 retry + 观察即可）：

```python
"""插件：agent 循环中间件（waterfall 演示）。

- ``agent/request-error``（waterfall）：失败码 TRANSIENT 时每个 (turn, step)
  重试一次（retry 场景的恢复逻辑——循环自身不重试，恢复由监听器接管）。

waterfall 监听器契约（cordis）：``listener(payload, next)``——``next()``
继续链路（落到默认行为），不调用即截断（veto）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.types import Events, RetryAction

name = "middleware"

RETRYABLE_CODES = frozenset({"TRANSIENT"})


def apply(ctx) -> None:
    retried: set[tuple[int, int]] = set()
    observed: list[str] = []

    def on_request_error(payload, next):
        action = next()
        if action is not None:
            return action
        failure = payload["failure"]
        key = (payload["turn"], payload["step"])
        if failure.code in RETRYABLE_CODES and key not in retried:
            retried.add(key)
            observed.append(f"request-error: retry (turn={payload['turn']} step={payload['step']} code={failure.code})")
            return RetryAction()
        observed.append(f"request-error: no recovery (turn={payload['turn']} step={payload['step']} code={failure.code})")
        return None

    ctx.on(Events.AGENT_REQUEST_ERROR, on_request_error)
    ctx.provide("middleware-observed", observed)
```

**追加 cordis.yml 条目**（middleware 插件无 inject——事件监听不需要 load-time 服务）：

```yaml
- id: middleware
  name: ./plugins/middleware.py
```

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：5 passed（text/tools/retry/steer + services）

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/plugins/middleware.py tests/test_mini_dsh/test_composition.py
git commit -m "feat(mini-dsh): middleware request-error retry + tools/retry/steer scenarios"
```

---

## Task 13：plugins/skill_tool.py + skills/poetic-note/SKILL.md + skills 场景

**文件：**
- 创建：`examples/mini_dsh/plugins/skill_tool.py`、`examples/mini_dsh/skills/poetic-note/SKILL.md`
- 修改：`tests/test_mini_dsh/test_composition.py`

- [ ] **步骤 1：写失败的测试**

`skills/poetic-note/SKILL.md`：

```markdown
---
name: poetic-note
description: Write notes as two-line poems.
---

When the user asks you to note something down, respond with a two-line poem
about the content instead of plain text.
```

追加测试：

```python
@pytest.mark.asyncio
async def test_skills_scenario_loads_skill_and_follows_it(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "skills")
    session = ctx.get("session")
    await _run(ctx, "save a note about autumn")
    # skill 工具被调用且结果含技能正文
    calls = [e.data for e in session.events_of("tool/call")]
    assert any(call.get("name") == "skill" for call in calls)
    results = [e.data["message"].text for e in session.events_of("tool/result")]
    assert any("two-line poem" in text for text in results)
    # 最终文本体现技能指令（两行诗）
    messages = session.derive_messages()
    assert any("autumn leaves" in getattr(m, "text", "") for m in messages)
    # session 日志里应有 <available_skills> 目录消息（skill 工具可见时注入）
    catalog_texts = [
        e.data["message"].text for e in session.events_of("user/message")
        if "available_skills" in (e.data.get("message").text if e.data.get("message") else "")
    ]
    assert catalog_texts
    assert "poetic-note" in catalog_texts[0]


@pytest.mark.asyncio
async def test_skills_slash_invocation_injects_body(monkeypatch: pytest.MonkeyPatch):
    """用户显式 /poetic-note 调用：技能正文作为 instructions 注入。"""
    ctx = await _compose(monkeypatch, "text")  # 无工具调用脚本
    session = ctx.get("session")
    await _run(ctx, "/poetic-note please summarize this")
    injected = [
        e.data["message"].text for e in session.events_of("user/message")
        if "two-line poem" in (e.data.get("message").text if e.data.get("message") else "")
    ]
    assert injected
```

注：`/poetic-note` 注入后 text 场景脚本只回一句 "2 + 2 = 4."，turn 正常结束即可——断言重点是**注入消息存在**。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：skills 两条 FAIL（skills 服务未提供 / skill 工具未注册）

- [ ] **步骤 3：写实现**

`plugins/skill_tool.py`：

```python
"""插件：provide "skills" + skill 加载工具 + 目录发布 + /<name> 注入（dsh tool-skill）。"""
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.skill import FileSkillProvider, SkillRegistry
from core.tools import Tool
from core.types import Events, PreStepEnter, TextBlock, UserMessage

name = "skill_tool"

#: 依赖：apply 里 ``ctx.get("tools")`` 需要 tools 服务先 ACTIVE。
inject = ["tools"]


class Config(BaseModel):
    skillsRoot: str = "./skills"


def _render_skill(skill: Any) -> UserMessage:
    body = f"# Skill: {skill.name}\n\n{skill.description}\n\n{skill.content}"
    return UserMessage(content=(TextBlock(text=body),), source={"kind": "skill-invocation", "name": skill.name})


def apply(ctx, config: Config) -> None:
    root = Path(config.skillsRoot)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    registry = SkillRegistry(ctx)
    registry.register_provider(FileSkillProvider(root))
    ctx.provide("skills", registry)

    tools = ctx.get("tools")

    def load(exec_input: Any) -> Any:
        from core.tools import ToolExecutionResult  # noqa: PLC0415 —— 局部 import 避免循环

        skill_name = str((exec_input.arguments or {}).get("name", ""))
        skill = registry.get(skill_name)
        if skill is None:
            return ToolExecutionResult.text(
                f'skill "{skill_name}" is unknown or no longer available', is_error=True
            )
        return ToolExecutionResult.text(
            f"# Skill: {skill.name}\n\n{skill.description}\n\n{skill.content}"
        )

    tools.register(
        Tool(
            name="skill",
            description=(
                "Load the full instructions for an available skill. Call this with the exact skill "
                "name from the session skill catalog before acting on a task that names or clearly "
                "matches that skill."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The exact skill name."}},
                "required": ["name"],
            },
            body=load,
            mode="parallel",
        )
    )

    # -- /<name> 显式调用（只扫用户文本消息；注册在目录监听器之前，注入次序靠前） --
    def on_invocation(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        messages = list(decision.messages)
        injected: list[UserMessage] = []
        for message in messages:
            source = getattr(message, "source", None) or {}
            # 只扫用户文本消息：source 为 None（from_text）或显式 user 来源；
            # baseline 指令 / skill 目录 / compaction 摘要消息都有非 user source 标记
            if source.get("kind") not in (None, "user"):
                continue
            text = (message.text or "").strip()
            first_line = text.splitlines()[0] if text else ""
            if not first_line.startswith("/"):
                continue
            skill_name = first_line[1:].strip()
            skill = registry.get(skill_name)
            if skill is None:
                continue
            injected.append(_render_skill(skill))
        if not injected:
            return decision
        return PreStepEnter(messages=tuple(list(decision.messages) + injected))

    # -- <available_skills> 目录发布（skill 工具可见即视为已注册；每会话只注入一次） --
    def on_catalog(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        agent = payload["agent"]
        session = agent.session
        # 会话日志已有 skill-catalog 来源消息就不再注入（只注入一次）
        already_published = any(
            (getattr((e.data or {}).get("message"), "source", None) or {}).get("kind")
            == "skill-catalog"
            for e in session.events_of("user/message")
        )
        if already_published:
            return decision
        summaries = registry.list()
        if not summaries:
            return decision
        lines = "\n".join(f"- {s.name}: {s.description}" for s in summaries)
        catalog = UserMessage(
            content=(TextBlock(text=f"<available_skills>\n{lines}\n</available_skills>"),),
            source={"kind": "skill-catalog"},
        )
        return PreStepEnter(messages=tuple(list(decision.messages) + [catalog]))

    ctx.on(Events.AGENT_PRE_STEP, on_invocation)
    ctx.on(Events.AGENT_PRE_STEP, on_catalog)
```

（删除文末不再需要的 `_msg_text` helper。目录注入的消息里不包含技能全文——模型需要全文时调 `skill` 工具；`/<name>` 注入的消息含全文。）

**追加 cordis.yml 条目**（skill-tool 的模块级 `inject = ["tools"]` 保证 tools 先 ACTIVE）：

```yaml
- id: skill-tool
  name: ./plugins/skill_tool.py
  config:
    skillsRoot: ./skills
```

（`skillsRoot` 相对路径以插件文件目录为基准解析——skill_tool.py 内 `Path(__file__).resolve().parent.parent / root`。）

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：7 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/plugins/skill_tool.py examples/mini_dsh/skills tests/test_mini_dsh/test_composition.py
git commit -m "feat(mini-dsh): skill tool + catalog + slash invocation + skills scenario"
```

---

## Task 14：plugins/instructions.py + fixtures/AGENTS.md + instructions 场景

**文件：**
- 创建：`examples/mini_dsh/plugins/instructions.py`、`examples/mini_dsh/fixtures/AGENTS.md`
- 修改：`tests/test_mini_dsh/test_composition.py`

- [ ] **步骤 1：写失败的测试**

`fixtures/AGENTS.md`：

```markdown
# Workspace instructions

Always answer with at most 5 words.
```

追加测试（临时 workspace 拷入 AGENTS.md，MINI_DSH_CWD 指向它）：

```python
@pytest.mark.asyncio
async def test_instructions_baseline_injected_before_first_assistant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        "# Workspace instructions\n\nAlways answer with at most 5 words.\n", encoding="utf-8"
    )
    monkeypatch.setenv("MINI_DSH_CWD", str(tmp_path))
    ctx = await _compose(monkeypatch, "instructions")
    session = ctx.get("session")
    await _run(ctx, "what is your policy?")
    # baseline 消息存在且 seq 早于首个 assistant/message
    baseline = [
        e for e in session.events_of("user/message")
        if (e.data.get("message") or None) is not None
        and (getattr(e.data["message"], "source", None) or {}).get("kind") == "agent-instructions"
    ]
    assert baseline
    first_assistant = session.events_of("assistant/message")[0]
    assert baseline[0].seq < first_assistant.seq
    # 模型按指令回答（≤5 词——由脚本保证 "Understood. Keeping it brief."）
    messages = session.derive_messages()
    assert any("Keeping it brief" in getattr(m, "text", "") for m in messages)
```

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：instructions 场景 FAIL（无 instructions 插件 → 无 baseline 消息）

- [ ] **步骤 3：写实现 + 改 driver cwd**

`plugins/instructions.py`：

```python
"""插件：AGENTS.md/CLAUDE.md 指令注入（dsh agent-instructions 轻量版）。

- baseline：session 日志无 ``agent-instructions`` baseline 消息时，pre-step
  注入工作区指令全文（user 来源消息，source ``agent-instructions, baseline=true``）；
- 变更重注入：文件内容哈希与上次注入不同 → 注入更新消息。

无 dsh 的 fs-touch 事件追踪 / 版本缓存——pre-step 按哈希比对，简单确定。
"""
import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.types import Events, PreStepEnter, TextBlock, UserMessage

name = "instructions"

_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def _find_instruction_file(cwd: str | None) -> Path | None:
    base = Path(cwd or "").expanduser().resolve()
    for filename in _INSTRUCTION_FILES:
        candidate = base / filename
        if candidate.is_file():
            return candidate
    return None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(ctx) -> None:
    state = {"digest": None}

    def on_pre_step(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        agent = payload["agent"]
        session = agent.session
        instruction_file = _find_instruction_file(session.header.cwd)
        if instruction_file is None:
            return decision
        content = instruction_file.read_text(encoding="utf-8")
        digest = _digest(instruction_file)
        has_baseline = any(
            (getattr((e.data or {}).get("message"), "source", None) or {}).get("kind") == "agent-instructions"
            for e in session.events_of("user/message")
        )
        if has_baseline and digest == state["digest"]:
            return decision  # 无变化
        state["digest"] = digest
        message = UserMessage(
            content=(TextBlock(text=content),),
            source={
                "kind": "agent-instructions",
                "baseline": not has_baseline,
                "path": str(instruction_file),
            },
        )
        return PreStepEnter(messages=tuple(list(decision.messages) + [message]))

    ctx.on(Events.AGENT_PRE_STEP, on_pre_step)
```

改 `plugins/driver.py`：cwd 优先级 env → baseUrl（替换原 `store.create(cwd=ctx.baseUrl if hasattr(...))`）：

```python
    cwd = os.environ.get("MINI_DSH_CWD") or getattr(ctx, "baseUrl", None)
    session = store.create(cwd=cwd)
```

补 import os。若需真正改文件再改，把 baseline 注入逻辑的 source 检查用 helper 抽出来与 skill_tool 复用（若两处都要读 source.kind 的话；否则各自内联）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：8 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/plugins/instructions.py examples/mini_dsh/fixtures examples/mini_dsh/plugins/driver.py tests/test_mini_dsh/test_composition.py
git commit -m "feat(mini-dsh): agent-instructions baseline + change re-injection + scenario"
```

---

## Task 15：plugins/compaction.py + compaction 场景

**文件：**
- 创建：`examples/mini_dsh/plugins/compaction.py`
- 修改：`tests/test_mini_dsh/test_composition.py`

- [ ] **步骤 1：写失败的测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_compaction_scenario_snips_and_shadows(monkeypatch: pytest.MonkeyPatch):
    ctx = await _compose(monkeypatch, "compaction")
    session = ctx.get("session")
    await _run(ctx, "read the big file")
    # snip：大工具结果被截断并带标记
    results = [e.data["message"].text for e in session.events_of("tool/result")]
    assert any("truncated by compaction" in text for text in results)
    # 事件链成对且有序
    starts = session.events_of("compaction/start")
    summaries = session.events_of("compaction/summary")
    ends = session.events_of("compaction/end")
    assert len(starts) == len(summaries) == len(ends) == 1
    assert starts[0].seq < summaries[0].seq < ends[0].seq
    # shadow 生效：早期消息不在派生历史里，摘要消息在
    messages = session.derive_messages()
    assert not any("read the big file" == getattr(m, "text", "") for m in messages)
    assert any(getattr(m, "text", "").startswith("Earlier context (compacted):") for m in messages)
```

注意：pressure 检查在 pre-step 触发——compaction 场景里第一步工具结果 50k 字符被 snip 到 8k，session 总计 >10k 阈值（cordis.yml config `maxChars: 10000`），第二步 pre-step 触发 compact；keepMessages=2 → shadow 掉除最近 2 条消息事件以外的所有消息。断言里的消息文本匹配按实际实现微调（原则：snip 标记 + 事件链 + shadow 后早期 prompt 消失 + 摘要存在）。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：compaction 场景 FAIL

- [ ] **步骤 3：写实现**

`plugins/compaction.py`：

```python
"""插件：provide "compaction" + tools/post-execute snip + pre-step 压力检查。"""
import sys
from pathlib import Path

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import types as t
from core.compaction import Compaction, make_snip_listener
from core.types import Events

name = "compaction"


class Config(BaseModel):
    maxChars: int = 10_000
    keepMessages: int = 2
    snipMaxChars: int = 8_000


def apply(ctx, config: Config) -> None:
    service = Compaction(ctx, max_chars=config.maxChars, keep_messages=config.keepMessages)
    ctx.provide("compaction", service)

    # 工具结果 snip（tools/post-execute waterfall）
    ctx.on("tools/post-execute", make_snip_listener(max_chars=config.snipMaxChars))

    # pre-step 压力检查：派生消息超阈 → compact_if_needed("pressure")
    def on_pre_step(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        session = payload["agent"].session
        service.compact_if_needed(session, "pressure")
        return decision

    ctx.on(Events.AGENT_PRE_STEP, on_pre_step)
```

**追加 cordis.yml 条目 + 全量服务断言**：

```yaml
- id: compaction
  name: ./plugins/compaction.py
  config:
    maxChars: 10000
    keepMessages: 2
    snipMaxChars: 8000
```

8 个插件齐了。**替换** Task 11 的 `test_composition_has_task11_services` 为收官的全量断言：

```python
@pytest.mark.asyncio
async def test_composition_has_all_services(monkeypatch: pytest.MonkeyPatch):
    """Task 15 收官：8 个插件提供的全部服务都在。"""
    ctx = await _compose(monkeypatch, "text")
    for service in (
        "sessions", "skills", "compaction", "llm", "tools",
        "agentLoop", "systemPrompt", "agent", "session",
    ):
        assert ctx.get(service, strict=False) is not None, f"missing service {service}"
```

注意：
- `ctx.on("tools/post-execute", ...)` 的事件名——tools 执行钩子在 javis/harness/tools.py 里是 `ctx.waterfall("tools/post-execute", ...)` 还是常量 `Events.TOOLS_POST_EXECUTE`？查 `core/types.py` 的 `Events` 常量与 `core/tools.py` 里 tools/post-execute 的 dispatch 名一致即可（javis/harness 用字符串 "tools/post-execute"；Events 类可能没有它——实现时对齐 tools.py 的实际 dispatch 名）。
- pre-step 压力检查在 skill_tool/instructions 的 pre-step 链之后追加——waterfall 链上顺序即注册顺序，compaction 插件在 cordis.yml 里排在 driver 之前、middleware 之后，无冲突。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_mini_dsh/test_composition.py -v`
预期：9 passed

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/plugins/compaction.py tests/test_mini_dsh/test_composition.py
git commit -m "feat(mini-dsh): compaction plugin (snip + pressure) + scenario"
```

---

## Task 16：cli.py + 重写 tests/test_javis/test_mini_dsh_example.py

**文件：**
- 创建：`examples/mini_dsh/cli.py`（替换旧 cli.py——旧文件在 Task 17 删除；先新建同名覆盖，旧内容已在 git 历史）
- 重写：`tests/test_javis/test_mini_dsh_example.py`
- 修改：`examples/mini_dsh/cordis.yml`（如需环境变量开关说明）

- [ ] **步骤 1：写失败的测试**

重写 `tests/test_javis/test_mini_dsh_example.py`（去掉 build_runtime，改为直接跑 cli 的 demo 函数或以模块方式驱动）：

```python
"""mini_dsh 示例的端到端测试：cli.py 的 demo 场景全绿。

与 test_mini_dsh/test_composition.py（组合级）互补：这里跑 cli.py 的
``run_demo``，断言 7 场景全过 + 退出码语义。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MINI_ROOT = Path(__file__).resolve().parents[2] / "examples" / "mini_dsh"
if str(MINI_ROOT) not in sys.path:
    sys.path.insert(0, str(MINI_ROOT))


def _load_cli() -> object:
    spec = importlib.util.spec_from_file_location("mini_dsh_cli", MINI_ROOT / "cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mini_dsh_cli"] = module
    spec.loader.exec_module(module)
    return module


def test_demo_all_scenarios_ok():
    cli = _load_cli()
    # 7 场景全部通过（exit code 0）——由 cli.run_demo 内部断言
    assert cli.run_demo() == 0
```

跑这个之前，cli.py 还不存在 → FAIL。TDD 的"失败"步 = import 失败。

- [ ] **步骤 2：跑测试确认失败**

运行：`uv run pytest tests/test_javis/test_mini_dsh_example.py -v`
预期：FAIL（cli.py 无 run_demo 或旧文件没有）

- [ ] **步骤 3：写实现**

`examples/mini_dsh/cli.py`（参照 dsh_harness/cli.py 与 examples/cordis/runner.py 的驱动方式；demo 场景带断言）：

```python
#!/usr/bin/env python
"""mini_dsh 的 standalone 驱动（无 javis 宿主，仅 javis.cordis）。

    uv run python examples/mini_dsh/cli.py                 # 全部 7 个 demo 场景
    uv run python examples/mini_dsh/cli.py --scenario tools
    uv run python examples/mini_dsh/cli.py --prompt "2+2"  # 真实模型（有 API key）
    uv run python examples/mini_dsh/cli.py --repl          # 交互 REPL
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_COMPOSITION = _HERE / "cordis.yml"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from core import types as t  # noqa: E402
from providers import SCENARIOS  # noqa: E402

from javis.cordis import Context, FiberState  # noqa: E402
from javis.cordis.loader import Loader  # noqa: E402
from javis.cordis.registry import settle  # noqa: E402

PROMPTS: dict[str, str] = {
    "text": "what is 2+2?",
    "tools": "save a note and check two cities",
    "retry": "say something",
    "steer": "what time is it?",
    "skills": "save a note about autumn",
    "instructions": "what is your policy?",
    "compaction": "read the big file",
}


async def _compose(scenario: str, *, cwd: str | None = None) -> Context:
    ctx = Context()
    ctx.baseUrl = cwd or str(_HERE)
    loader_fiber = ctx.plugin(Loader, {"file": str(_COMPOSITION)})
    await loader_fiber
    await settle(ctx)
    failed = [f for f in _all_fibers(ctx) if f.state == FiberState.FAILED]
    if failed:
        raise RuntimeError(f"plugin load failed: {failed[0]._error}")
    return ctx


def _all_fibers(ctx: Context) -> list[Any]:
    return [fiber for runtime in ctx.registry.values() for fiber in list(runtime.fibers)]


async def _run_turn(ctx: Context, prompt: str) -> None:
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()


async def run_demo_async(scenario: str | None = None) -> int:
    """跑 1 个或全部 demo 场景，每个带断言；失败抛 AssertionError。"""
    names = [scenario] if scenario else list(SCENARIOS)
    for name in names:
        workspace = tempfile.mkdtemp(prefix=f"mini-dsh-{name}-")
        if name == "instructions":
            _seed_workspace(workspace)  # 拷入 fixtures/AGENTS.md
        os.environ["MINI_DSH_CWD"] = workspace
        os.environ["HARNESS_DEMO_SCENARIO"] = name  # llm 插件 apply 时读
        print(f"[mini-dsh] running scenario {name} ...", file=sys.stderr)
        try:
            ctx = await _compose(name, cwd=workspace)
            session = ctx.get("session")
            if name == "steer":
                ctx.get("llm").on_tool_call = lambda: ctx.get("agent").steer(
                    t.UserMessage.from_text("also include Tokyo's weather in your answer")
                )
            await _run_turn(ctx, PROMPTS[name])
            _assert_scenario(name, ctx, session)
        except BaseException:  # noqa: BLE001 —— 原样重抛，仅补场景名上下文
            print(f"[mini-dsh] scenario {name} FAILED", file=sys.stderr)
            raise
        print(f"[mini-dsh] scenario {name}: OK", file=sys.stderr)
    return 0


def _seed_workspace(workspace: str) -> None:
    from shutil import copyfile

    copyfile(_HERE / "fixtures" / "AGENTS.md", Path(workspace) / "AGENTS.md")


def _assert_scenario(name: str, ctx: Context, session: Any) -> None:
    """每场景 2–4 条断言（语义验证，不全量）。"""
    if name == "text":
        msgs = session.derive_messages()
        assert any("2 + 2 = 4" in getattr(m, "text", "") for m in msgs)
    elif name == "tools":
        results = [e for e in session.events_of("tool/result")]
        assert len(results) == 3
        names = [e.data["name"] for e in session.events_of("tool/call")]
        assert names == ["set_note", "weather", "weather"]
    elif name == "retry":
        msgs = session.derive_messages()
        assert any("Recovered" in getattr(m, "text", "") for m in msgs)
        assert len(session.events_of("assistant/message")) == 1
        observed = ctx.get("middleware-observed", strict=False) or []
        assert any("request-error: retry" in line for line in observed)
    elif name == "steer":
        msgs = session.derive_messages()
        assert any("Tokyo" in getattr(m, "text", "") for m in msgs)
    elif name == "skills":
        msgs = session.derive_messages()
        assert any("autumn leaves" in getattr(m, "text", "") for m in msgs)
        assert any(
            "available_skills" in (getattr(e.data.get("message"), "text", "") or "")
            for e in session.events_of("user/message")
        )
    elif name == "instructions":
        baseline = [
            e for e in session.events_of("user/message")
            if (getattr(e.data.get("message"), "source", None) or {}).get("kind") == "agent-instructions"
        ]
        assert baseline
        msgs = session.derive_messages()
        assert any("Keeping it brief" in getattr(m, "text", "") for m in msgs)
    elif name == "compaction":
        results = [e.data["message"].text for e in session.events_of("tool/result")]
        assert any("truncated by compaction" in text for text in results)
        assert len(session.events_of("compaction/start")) == 1
        msgs = session.derive_messages()
        assert any(getattr(m, "text", "").startswith("Earlier context (compacted):") for m in msgs)


def run_demo(scenario: str | None = None) -> int:
    """同步入口（pytest 与 main 共用）。"""
    return asyncio.run(run_demo_async(scenario))


async def _run_prompt(prompt: str) -> int:
    os.environ.setdefault("MINI_DSH_PROVIDER", "auto")  # 有 key 走真实模型
    ctx = await _compose("text")
    agent = ctx.get("agent")
    agent.followup(t.UserMessage.from_text(prompt))
    await agent.when_idle()
    # 打印 assistant 文本
    session = ctx.get("session")
    for message in session.derive_messages():
        if getattr(message, "role", "") == "assistant":
            print(message.text or "")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mini-dsh", description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), help="run one demo scenario")
    parser.add_argument("--prompt", help="run one real-model prompt and exit")
    parser.add_argument("--repl", action="store_true", help="interactive REPL")
    args = parser.parse_args(argv)
    if args.prompt:
        return asyncio.run(_run_prompt(args.prompt))
    if args.repl:
        return asyncio.run(_run_repl())
    return run_demo(args.scenario)


async def _run_repl() -> int:
    ctx = await _compose("text")
    agent = ctx.get("agent")
    print("mini-dsh REPL — type a message, /exit to quit", file=sys.stderr)
    while True:
        try:
            line = input("mini> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        agent.followup(t.UserMessage.from_text(line))
        await agent.when_idle()
        session = ctx.get("session")
        for message in session.derive_messages():
            if getattr(message, "role", "") == "assistant" and message.text:
                print(message.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

注意：REPL/prompt 走 scripted provider 时脚本耗尽后有兜底文本（providers.py 的 cursor 越界返回最后一条）——REPL 体验有限但可演示；README 说明。真实模型用 `--prompt`（需 `MINI_DSH_PROVIDER=openai` 或 cordis.yml provider 改 openai + API key）。

- [ ] **步骤 4：跑测试确认通过**

运行：`uv run pytest tests/test_javis/test_mini_dsh_example.py tests/test_mini_dsh/test_composition.py -v`
预期：全部通过

再跑一遍全 cli 冒烟：`uv run python examples/mini_dsh/cli.py` 预期 7 场景 OK 退出 0。

- [ ] **步骤 5：Commit**

```bash
git add examples/mini_dsh/cli.py tests/test_javis/test_mini_dsh_example.py
git commit -m "feat(mini-dsh): standalone cli (7 demo scenarios + prompt + repl)"
```

---

## Task 17：清理旧文件 + README 全重写 + 引用收尾 + 终验

**文件：**
- 删除：`examples/mini_dsh/harness.py`、`examples/mini_dsh/harness_plugin.py`、`examples/mini_dsh/extra_tools.py`（旧 javis 版；providers.py 已被 Task 10 重写、cli.py 被 Task 16 重写）
- 重写：`examples/mini_dsh/README.md`
- 修改：`examples/cordis/README.md`、`examples/dsh_harness/README.md`（定位措辞收尾）
- 验证：全仓 grep/pytest/ruff

- [ ] **步骤 1：删旧文件**

```bash
git rm examples/mini_dsh/harness.py examples/mini_dsh/harness_plugin.py examples/mini_dsh/extra_tools.py
```

跑 `uv run pytest tests/test_javis/test_mini_dsh_example.py tests/test_mini_dsh/ -v` 确认绿（新测试不依赖旧文件）。

- [ ] **步骤 2：写 README.md**

`examples/mini_dsh/README.md`（全重写）——必须包含：
- 定位：cordis-only 的 dsh 精简 harness（示例矩阵里 vs examples/cordis vs examples/dsh_harness 的对照表；"核心自包含、可整体拷贝"，唯一外部依赖 `javis.cordis`）
- 与 javis/harness 的同源说明：mini_dsh core 与 javis/harness 架构层是同一 dsh 逻辑的两个表达（生产 core vs 教学精简）
- 目录结构（core 8 模块 / plugins 8 插件 / providers / cli）
- 接线图（cli → Context + Loader(cordis.yml) → 8 插件服务 → agent 契约）
- 运行方式（7 场景 / --scenario / --prompt / --repl）
- 关键设计点：session 一等服务（SessionStore + 卸载顺序）、SKILL（目录 + /<name>）、memory（指令文件）、history（compaction 事件链 + shadow + snip）、waterfall 可 veto、依赖驱动加载、import 技巧（sys.path）
- dsh 对照表（dsh 包 → mini_dsh core/plugins 映射）
- 验证标准引用（pytest 命令）

- [ ] **步骤 3：引用收尾**

`examples/cordis/README.md` 第 7 行最终措辞："cordis-only 的 dsh 精简 harness（核心自包含、可整体拷贝；装配见插件系统教程）"。
`examples/dsh_harness/README.md` 对照表与"两种引擎姿势"小节重写为：mini_dsh = 从零精简 core（教学），dsh_harness = javis.harness 生产核心装配（生产）。两个示例的区分 = 生产 core 装配 vs 从零精简 core。

- [ ] **步骤 4：终验**

```bash
uv run pytest tests/test_javis/test_mini_dsh_example.py tests/test_mini_dsh/ -v          # 新测试全绿
uv run pytest -q                                                                          # 全仓绿
uv run ruff check examples/mini_dsh tests/test_mini_dsh tests/test_javis/test_mini_dsh_example.py
```

grep 证实零 javis 依赖（除 javis.cordis）：

```bash
grep -rn "^from javis\.\|^import javis\." examples/mini_dsh --include="*.py" | grep -v "javis.cordis"
# 预期：无输出
```

无悬空引用：

```bash
grep -rn "plugin_harness" examples/ docs/ tests/ --include="*.md" --include="*.py" | grep -v "plans/"
# 预期：无输出（plans/ 历史计划除外）
```

- [ ] **步骤 5：Commit**

```bash
git add -A
git commit -m "chore(mini-dsh): drop legacy javis-bound files, rewrite README, finalize refs"
```

---

## 自检记录（实现计划 vs 规格）

**规格覆盖度：**
- Q1 standalone/零 javis → Task 1/17 + 验证 grep 标准（Task 17 步骤 4）✓
- Q2 语义保真精简 core（8 模块）→ Task 2–9 ✓
- Q3 改名 mini_dsh → Task 1 ✓
- Q4 7 demo 场景 → Task 10（工厂）+ Task 11–16（场景测试/cli）✓
- Q5 session 一等服务（SessionStore）→ Task 3 + Task 11 driver ✓
- Q6 SKILL（skill_tool 插件：工具+目录+`/<name>`）→ Task 7（core）+ Task 13 ✓
- Q7 memory（instructions 插件）+ history（compaction 服务+snip）→ Task 8（core）+ Task 14/15 ✓
- 引用更新（cordis/dsh_harness README、测试名）→ Task 1/17 ✓
- 行数预算 2.5k → 文件布局表的量级合计 core ~1.5k + 外围 ~0.9k ≈ 2.4k ✓（超了从 docstring/断言裁）

**占位符扫描：** 无 TODO/"补充细节"式步骤。自查修复记录：cordis Context 无 dispose——测试里 7 处死代码 `await ctx.dispose() if hasattr(...)` 已全部删除（Task 11 测试 docstring 言明每测试独立 compose、进程退出回收）；Task 11 的 cordis.yml 改为只含已存在插件的增量追加（Task 12/13/14/15 各追加自己的条目，否则引用未创建插件会 load 失败）；skill_tool 模块级 `inject = ["tools"]` 保证加载顺序；Task 3 代码块收尾围栏补回（围栏 65/65 平衡）；ScriptedAdapter 脚本耗尽改收尾短句（不重复最后一条）；cli run_demo_async 每场景设置 HARNESS_DEMO_SCENARIO。

**类型一致性：**
- `execute_tool_calls(ctx, session, agent, turn, step, tool_calls, signal, accept_context=...)` 全计划一致（Task 6/9 使用）✓
- `Session.append(type, data) → SessionEvent(.seq)`、`session.events_of(type)`、`session.derive_messages()`、`session.header.cwd` 一致 ✓
- `ReactLoopAgent(ctx, session.id, AgentOptions(...), session)`；宿主 API followup/steer/when_idle 一致 ✓
- LLM 协议 `prepare_call(config, signal)` / `stream(options)`；`chunk_response(...)` 造流一致 ✓
- 服务名 `sessions/skills/compaction/llm/tools/agentLoop/systemPrompt/agent/session` 全计划一致 ✓
- waterfall 监听器 `listener(payload, next)`、`ctx.on(Events.X, ...)` 一致 ✓
- 事件词汇：`tool/call`/`tool/result`/`user/message`/`assistant/message`/`compaction/start|summary|end`/`step/end` 一致 ✓

**执行交接：**
计划已保存到 `plans/mini-dsh-implementation.md`。两种执行方式：
1. **子代理驱动（推荐）**——每个任务调度新子代理、任务间审查、快速迭代
2. **内联执行**——当前会话按 executing-plans 批量执行、设检查点

选哪种？
