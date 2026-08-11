# javis 多引擎架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 javis 支持可插拔的 agent 引擎：corecoder 双接口增补（async achat）+ CoreCoderBackend adapter + 引擎注册表/配置，默认引擎 corecoder，mock 保留给开发。

**Architecture:** 保持 turn 级 seam。corecoder 增补 AsyncLLM/achat/on_tool_result 后，`CoreCoderBackend`（`AgentBackend` 实现）用 asyncio task + queue 转发事件流，无线程桥。`javis/engines/registry.py` 提供 name→工厂注册，`javis/config.py` 按 CLI > env > config.json > 默认值 解析引擎，`build_javis_runtime(engine=...)` 接线。协议增补：`AgentTurnEnd.usage` 可选字段 + 引擎层可选钩子 `load_history`/`clear_history`（hasattr 检测，不加入 Protocol 类本体——`@runtime_checkable` 的 isinstance 会因缺成员失败，见 Task 1 说明）。

**Tech Stack:** Python 3.10+ / asyncio / pydantic / openai SDK（AsyncOpenAI）/ pytest-asyncio / typer

**设计文档:** `docs/superpowers/specs/2026-08-11-javis-multi-engine-design.md`
**对接文档:** `docs/agent-engine-guide.md`

---

### Task 1: javis 协议 v2 — `AgentTurnEnd.usage` + 引擎层消费

**Files:**
- Modify: `javis/engine/types.py:53-62`
- Modify: `javis/engine/mock_engine.py:124-145`
- Modify: `javis/engine/protocol.py:17-34`（docstring 说明可选钩子）
- Test: `tests/test_javis/test_mock_engine.py`

**背景说明（先读）：** `AgentBackend` 是 `@runtime_checkable` Protocol——`isinstance(obj, AgentBackend)` 会检查所有协议成员是否**存在**。若把 `load_history`/`clear_history` 加进 Protocol 类，`MockAgent`（不实现）的 isinstance 检查会失败。因此可选钩子只写进 docstring，引擎层用 `hasattr` 检测。

- [ ] **Step 1: 写失败测试——usage 消费 + clear 钩子**

在 `tests/test_javis/test_mock_engine.py` 末尾追加：

```python
class UsageBackend:
    """Backend that reports real usage in AgentTurnEnd."""

    async def run_turn(self, prompt, *, context):
        yield AgentTextDelta(text="hi")
        yield AgentTurnEnd(text="hi", usage=UsageSnapshot(input_tokens=5, output_tokens=7))


@pytest.mark.asyncio
async def test_submit_message_uses_backend_usage():
    engine = MockEngine(UsageBackend(), model="m", system_prompt="s", cwd="/tmp")
    [e async for e in engine.submit_message("hello")]
    assert engine.total_usage.input_tokens == 5
    assert engine.total_usage.output_tokens == 7


class HookBackend(UsageBackend):
    def __init__(self) -> None:
        self.cleared = False

    def clear_history(self) -> None:
        self.cleared = True


@pytest.mark.asyncio
async def test_clear_forwards_to_backend_hook():
    backend = HookBackend()
    engine = MockEngine(backend, model="m", system_prompt="s", cwd="/tmp")
    engine.clear()
    assert backend.cleared is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_javis/test_mock_engine.py -q`
Expected: 2 个新测试 FAIL（`total_usage.input_tokens == 0`、`backend.cleared is False`）

- [ ] **Step 3: 实现——types.py 加 usage 字段**

`javis/engine/types.py` 顶部 import 区加一行（现有 `from javis.messages import ConversationMessage` 之后）：

```python
from javis.usage import UsageSnapshot
```

`AgentTurnEnd` 类改为：

```python
@dataclass(frozen=True)
class AgentTurnEnd:
    """Marks the end of one assistant turn.

    ``text`` is the final assembled assistant text. If empty, the host uses
    whatever was accumulated from ``AgentTextDelta`` events.

    ``usage`` (optional) is the token consumption of THIS turn; when present
    the engine layer accumulates it, otherwise it falls back to word-count
    estimation. Consumption is an engine-layer concern, not the backend's.
    """

    text: str = ""
    usage: UsageSnapshot | None = None
```

- [ ] **Step 4: 实现——mock_engine 消费 usage + clear 转发**

`javis/engine/mock_engine.py` 的 `submit_message` 中 `AgentTurnEnd` 分支改为：

```python
            elif isinstance(event, AgentTurnEnd):
                final_text = event.text or accumulated_text
                self._append_assistant(final_text)
                if event.usage is not None:
                    self._usage = UsageSnapshot(
                        input_tokens=self._usage.input_tokens + event.usage.input_tokens,
                        output_tokens=self._usage.output_tokens + event.usage.output_tokens,
                    )
                else:
                    self._usage = UsageSnapshot(
                        input_tokens=self._usage.input_tokens + len(user_message.text.split()),
                        output_tokens=self._usage.output_tokens + len(final_text.split()),
                    )
```

`clear()` 改为（注意行 98-100 现有实现）：

```python
    def clear(self) -> None:
        self._messages.clear()
        self._usage = UsageSnapshot()
        if hasattr(self._agent, "clear_history"):
            self._agent.clear_history()
```

`javis/engine/protocol.py` 的 `AgentBackend` docstring 追加：

```python
    Optional hooks (documented here, NOT in the Protocol class — runtime_checkable
    isinstance() checks member presence, so optional members would break
    backends that don't implement them; the engine layer probes with hasattr):

        def load_history(self, messages: list[ConversationMessage]) -> None:
            """Rebuild engine-internal history from javis mirror messages."""

        def clear_history(self) -> None:
            """Clear engine-internal history."""
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_javis/test_mock_engine.py -q`
Expected: 全部 PASS（9 个）

- [ ] **Step 6: Commit**

```bash
git add javis/engine/types.py javis/engine/mock_engine.py javis/engine/protocol.py tests/test_javis/test_mock_engine.py
git commit -m "feat(javis): add AgentTurnEnd.usage and optional history hooks to engine layer"
```

---

### Task 2: corecoder — `on_tool_result` 回调（chat 同步路径）

**Files:**
- Modify: `corecoder/agent.py:49-99, 101-130`
- Test: `tests/test_corecoder/test_agent_callbacks.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_corecoder/test_agent_callbacks.py`：

```python
"""Tests for Agent.chat() on_tool_result callback and public history API."""

from __future__ import annotations

import pytest

from corecoder.agent import Agent
from corecoder.llm import LLMResponse, ScriptedLLM, ToolCall


def _agent(script, **kwargs) -> Agent:
    return Agent(llm=ScriptedLLM(script=script), **kwargs)


def test_on_tool_result_single_call(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("content", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="done"),
    ])
    agent.chat("read", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    name, args, out, err = calls[0]
    assert name == "read_file"
    assert args == {"file_path": str(target)}
    assert "content" in out
    assert err is False


def test_on_tool_result_parallel(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"file_path": str(a)}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ]),
        LLMResponse(content="done"),
    ])
    agent.chat("read both", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 2
    assert {c[0] for c in calls} == {"read_file"}


def test_on_tool_result_reports_error(tmp_path):
    missing = tmp_path / "nope.txt"
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(missing)})]),
        LLMResponse(content="done"),
    ])
    agent.chat("read", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    assert calls[0][3] is True
    assert "not found" in calls[0][2]


def test_on_tool_result_unknown_tool():
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
        LLMResponse(content="done"),
    ])
    agent.chat("x", on_tool_result=lambda n, a, out, err: calls.append((n, a, out, err)))

    assert len(calls) == 1
    assert calls[0][0] == "nope"
    assert calls[0][3] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_corecoder/ -q`
Expected: 4 个 FAIL（`TypeError: chat() got an unexpected keyword argument 'on_tool_result'`）

- [ ] **Step 3: 实现——`_exec_tool_with_status` + 回调**

`corecoder/agent.py`：把 `_exec_tool`（行 101-115）替换为：

```python
    def _exec_tool_with_status(self, tc) -> tuple[str, bool]:
        """Execute a single tool call, returning (result_text, is_error).

        is_error is True only when the tool could not be executed (unknown
        tool, bad arguments, raised exception). A tool that returns an
        "Error: ..." string of its own is a successful execution.
        """
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'", True
        # validate arguments first so a TypeError raised *inside* the tool isn't
        # mislabelled as a bad-arguments error from the caller
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}", True
        try:
            return tool.execute(**tc.arguments), False
        except Exception as e:
            return f"Error executing {tc.name}: {e}", True

    def _exec_tool(self, tc) -> str:
        return self._exec_tool_with_status(tc)[0]
```

`chat()` 签名（行 49）改为：

```python
    def chat(self, user_input: str, on_token=None, on_tool=None, on_tool_result=None) -> str:
```

`chat()` 单发分支（行 71-80）改为：

```python
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result, is_error = self._exec_tool_with_status(tc)
                    if on_tool_result:
                        on_tool_result(tc.name, tc.arguments, result, is_error)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    # parallel execution for multiple tool calls
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, (result, is_error) in zip(resp.tool_calls, results):
                        if on_tool_result:
                            on_tool_result(tc.name, tc.arguments, result, is_error)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
```

`_exec_tools_parallel`（行 117-130）改为返回 `(result, is_error)` 列表：

```python
    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[tuple[str, bool]]:
        """Run multiple tool calls concurrently using threads."""
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool_with_status, tc) for tc in tool_calls]
            return [f.result() for f in futures]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_corecoder/ tests/test_javis/test_corecoder_engine.py -q`
Expected: 全部 PASS（新增 4 + 现有 10）

- [ ] **Step 5: Commit**

```bash
git add corecoder/agent.py tests/test_corecoder/test_agent_callbacks.py
git commit -m "feat(corecoder): add on_tool_result callback to Agent.chat"
```

---

### Task 3: corecoder — 公开 `load_messages` / `set_system_prompt`

**Files:**
- Modify: `corecoder/agent.py`
- Test: `tests/test_corecoder/test_agent_callbacks.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_corecoder/test_agent_callbacks.py`：

```python
def test_load_messages_replaces_history():
    agent = _agent([LLMResponse(content="hi")])
    agent.chat("hello")
    assert len(agent.messages) == 2

    agent.load_messages([{"role": "user", "content": "restored"}])
    assert agent.messages == [{"role": "user", "content": "restored"}]


def test_set_system_prompt_updates_system():
    agent = _agent([LLMResponse(content="hi")])
    agent.set_system_prompt("custom system prompt")
    assert agent._system == "custom system prompt"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_corecoder/test_agent_callbacks.py -q`
Expected: 2 个 FAIL（`AttributeError: 'Agent' object has no attribute 'load_messages'`）

- [ ] **Step 3: 实现**

`corecoder/agent.py` 的 `reset()`（行 148-150）之后追加：

```python
    def load_messages(self, messages: list[dict]):
        """Replace conversation history (used when restoring a session)."""
        self.messages = list(messages)

    def set_system_prompt(self, prompt: str):
        """Replace the system prompt for subsequent rounds."""
        self._system = prompt
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_corecoder/ -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add corecoder/agent.py tests/test_corecoder/test_agent_callbacks.py
git commit -m "feat(corecoder): add public load_messages and set_system_prompt"
```

---

### Task 4: corecoder — `AsyncLLM` + `AsyncScriptedLLM`

**Files:**
- Modify: `corecoder/llm.py`
- Test: `tests/test_corecoder/test_async_llm.py`（新建）

**背景说明：** `AsyncLLM` 镜像同步 `LLM.chat` 的逻辑（流式累积、tool_calls 跨 chunk 合并、`stream_options` 回退、重试）。它需要真实网络，无法离线单测；用 `AsyncScriptedLLM`（离线回放）单测行为，`AsyncLLM` 的正确性靠 achat 集成测试与运行时验证。`AsyncScriptedLLM` 的 token 计数用**实例属性**（同步 `ScriptedLLM` 的类属性是历史 bug，不要照抄）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_corecoder/test_async_llm.py`：

```python
"""Tests for AsyncScriptedLLM — the offline double for AsyncLLM."""

from __future__ import annotations

import pytest

from corecoder.llm import AsyncScriptedLLM, LLMResponse


@pytest.mark.asyncio
async def test_async_scripted_plays_back_turns():
    llm = AsyncScriptedLLM([LLMResponse(content="first"), LLMResponse(content="second")])
    assert (await llm.chat(messages=[])).content == "first"
    assert (await llm.chat(messages=[])).content == "second"


@pytest.mark.asyncio
async def test_async_scripted_streams_through_on_token():
    seen = []
    llm = AsyncScriptedLLM([LLMResponse(content="hello world")])
    resp = await llm.chat(messages=[], on_token=seen.append)
    assert resp.content == "hello world"
    assert seen == ["hello world"]


@pytest.mark.asyncio
async def test_async_scripted_out_of_turns_raises():
    llm = AsyncScriptedLLM([])
    with pytest.raises(RuntimeError, match="out of turns"):
        await llm.chat(messages=[])


@pytest.mark.asyncio
async def test_async_scripted_counts_tokens_per_instance():
    llm = AsyncScriptedLLM([LLMResponse(content="some words here")])
    assert llm.total_completion_tokens == 0
    await llm.chat(messages=[])
    assert llm.total_completion_tokens == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_corecoder/test_async_llm.py -q`
Expected: 4 个 FAIL（`ImportError: cannot import name 'AsyncScriptedLLM'`）

- [ ] **Step 3: 实现——`AsyncScriptedLLM`**

`corecoder/llm.py` 的 `ScriptedLLM` 类之后追加：

```python
class AsyncScriptedLLM:
    """Deterministic offline async LLM for tests and demos.

    Mirrors ScriptedLLM semantics over an async chat() interface. Token
    counters are instance attributes (ScriptedLLM's class attributes are a
    known bug — do not copy that pattern).
    """

    def __init__(self, script: list[LLMResponse], model: str = "scripted-demo"):
        self._turns = list(script)
        self.model = model
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    async def chat(self, messages, tools=None, on_token=None) -> LLMResponse:
        if not self._turns:
            raise RuntimeError("ScriptedLLM ran out of turns")
        resp = self._turns.pop(0)
        if on_token and resp.content:
            on_token(resp.content)
        self.total_completion_tokens += len(resp.content.split())
        return resp
```

- [ ] **Step 4: 实现——`AsyncLLM`**

`corecoder/llm.py` 顶部 import 改为：

```python
from openai import (
    OpenAI,
    AsyncOpenAI,
    APIError,
    BadRequestError,
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
)
```

`LiteLLM` 类之前插入（与 `LLM` 并列）：

```python
class AsyncLLM:
    """Async counterpart of LLM — same behavior over AsyncOpenAI.

    Mirrors LLM.chat: streamed content accumulation, cross-chunk tool-call
    merging, stream_options fallback, and the same retry policy. Requires an
    agent.achat()-compatible caller (CoreCoderBackend).
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def estimated_cost(self) -> float | None:
        """Rough cost estimate in USD. Returns None if model not in pricing table."""
        pricing = _PRICING.get(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1_000_000
            + self.total_completion_tokens * output_rate / 1_000_000
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        params["stream_options"] = {"include_usage": True}
        try:
            stream = await self._call_with_retry(params)
        except BadRequestError:
            params.pop("stream_options", None)
            stream = await self._call_with_retry(params)

        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0

        async for chunk in stream:
            # usage info comes in the final chunk
            if chunk.usage:
                # some providers send usage with null fields; coerce to 0 so the
                # running totals below don't blow up on int + None
                prompt_tok = chunk.usage.prompt_tokens or 0
                completion_tok = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # accumulate text
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # accumulate tool calls across chunks
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        # parse accumulated tool calls
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    async def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff (async sleep)."""
        for attempt in range(max_retries):
            try:
                return await self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except APIError as e:
                # retry 5xx server errors but not 4xx; base APIError has no
                # status_code so read it defensively
                status_code = getattr(e, "status_code", None)
                if status_code and status_code >= 500 and attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
```

注意：`corecoder/llm.py` 顶部需要 `import asyncio`（文件目前没有）。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_corecoder/ -q`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add corecoder/llm.py tests/test_corecoder/test_async_llm.py
git commit -m "feat(corecoder): add AsyncLLM and AsyncScriptedLLM"
```

---

### Task 5: corecoder — `async achat()`

**Files:**
- Modify: `corecoder/agent.py:49-99`
- Test: `tests/test_corecoder/test_achat.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_corecoder/test_achat.py`：

```python
"""Tests for Agent.achat() — the async chat loop."""

from __future__ import annotations

import asyncio

import pytest

from corecoder.agent import Agent
from corecoder.llm import AsyncScriptedLLM, LLMResponse, ToolCall


def _agent(script, **kwargs) -> Agent:
    return Agent(llm=AsyncScriptedLLM(script=script), **kwargs)


@pytest.mark.asyncio
async def test_achat_plain_text_reply():
    agent = _agent([LLMResponse(content="hello world")])
    reply = await agent.achat("hi")

    assert reply == "hello world"
    assert agent.messages[0] == {"role": "user", "content": "hi"}
    assert agent.messages[-1] == {"role": "assistant", "content": "hello world"}


@pytest.mark.asyncio
async def test_achat_tool_round_with_callbacks(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("line one", encoding="utf-8")
    calls = []
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="file read done"),
    ])
    reply = await agent.achat("read", on_tool_result=lambda n, a, out, err: calls.append((n, out, err)))

    assert reply == "file read done"
    assert len(calls) == 1
    assert calls[0][0] == "read_file"
    assert "line one" in calls[0][1]
    assert calls[0][2] is False
    assert agent.messages[2]["role"] == "tool"
    assert agent.messages[2]["tool_call_id"] == "c1"


@pytest.mark.asyncio
async def test_achat_parallel_tool_calls(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    agent = _agent([
        LLMResponse(tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"file_path": str(a)}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": str(b)}),
        ]),
        LLMResponse(content="read both"),
    ])
    reply = await agent.achat("read both")

    assert reply == "read both"
    ids = [m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"]
    assert ids == ["c1", "c2"]


@pytest.mark.asyncio
async def test_achat_out_of_turns_raises():
    agent = _agent([LLMResponse(content="first")])
    assert await agent.achat("one") == "first"
    with pytest.raises(RuntimeError, match="out of turns"):
        await agent.achat("two")


@pytest.mark.asyncio
async def test_achat_max_rounds_exhausted():
    script = [
        LLMResponse(tool_calls=[ToolCall(id=f"c{i}", name="glob", arguments={"pattern": "*.py"})])
        for i in range(3)
    ]
    agent = _agent(script, max_rounds=2)
    assert await agent.achat("loop") == "(reached maximum tool-call rounds)"


@pytest.mark.asyncio
async def test_achat_cancel_fixes_history(tmp_path):
    """Cancelling mid-tool-round must leave a valid history (every assistant
    tool_calls answered)."""
    agent = _agent([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "sleep 0.3"})]),
        LLMResponse(content="done"),
    ])
    task = asyncio.create_task(agent.achat("go"))
    await asyncio.sleep(0.05)  # let round 1 start: assistant msg appended, tool running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    answered = {m.get("tool_call_id") for m in agent.messages if m.get("role") == "tool"}
    assert "c1" in answered
    # assistant tool_calls must all be answered
    for m in agent.messages:
        for tc in m.get("tool_calls", []):
            assert tc["id"] in answered
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_corecoder/test_achat.py -q`
Expected: 6 个 FAIL（`AttributeError: 'Agent' object has no attribute 'achat'`）

- [ ] **Step 3: 实现——`achat`**

`corecoder/agent.py`：
- 顶部 import 改为 `import asyncio`、`import concurrent.futures`、`import inspect`（现有已 import concurrent.futures/inspect，加 asyncio）
- `from .llm import LLM` 改为 `from .llm import LLM, ToolCall`

`chat()` 方法之后、`_exec_tool` 之前插入：

```python
    async def achat(self, user_input: str, on_token=None, on_tool=None, on_tool_result=None) -> str:
        """Async counterpart of chat(): same loop over awaitable LLM.chat().

        Cancellation semantics: a CancelledError raised at any await point
        triggers _answer_pending_tool_calls for the in-flight round, keeping
        the history valid for OpenAI-compatible APIs, then re-raises.
        Sync tool execution runs in a thread (asyncio.to_thread); cancellation
        does not take effect while a tool is running.
        """
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages)
        pending_tool_calls: list[ToolCall] = []

        try:
            for _ in range(self.max_rounds):
                resp = await self.llm.chat(
                    messages=self._full_messages(),
                    tools=self._tool_schemas(),
                    on_token=on_token,
                )

                # no tool calls -> LLM is done, return text
                if not resp.tool_calls:
                    self.messages.append(resp.message)
                    return resp.content

                # tool calls -> execute (parallel when multiple)
                self.messages.append(resp.message)
                pending_tool_calls = resp.tool_calls

                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result, is_error = await asyncio.to_thread(self._exec_tool_with_status, tc)
                    if on_tool_result:
                        on_tool_result(tc.name, tc.arguments, result, is_error)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    results = await asyncio.to_thread(self._exec_tools_parallel, resp.tool_calls, on_tool)
                    for tc, (result, is_error) in zip(resp.tool_calls, results):
                        if on_tool_result:
                            on_tool_result(tc.name, tc.arguments, result, is_error)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })

                # compress if tool outputs are big (extraction fallback only:
                # LLM-powered summarization is sync and would break the loop)
                self.context.maybe_compress(self.messages)

            return "(reached maximum tool-call rounds)"
        except asyncio.CancelledError:
            self._answer_pending_tool_calls(pending_tool_calls)
            raise
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_corecoder/ -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add corecoder/agent.py tests/test_corecoder/test_achat.py
git commit -m "feat(corecoder): add async achat loop with cancel-safe history"
```

---

### Task 6: javis — 引擎注册表 `javis/engines/`

**Files:**
- Create: `javis/engines/__init__.py`
- Create: `javis/engines/registry.py`
- Test: `tests/test_javis/test_engines.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_javis/test_engines.py`：

```python
"""Tests for the engine registry."""

from __future__ import annotations

import pytest

from javis.engine.mock_agent import MockAgent
from javis.engines import create_agent_backend, get_engine_config, list_engines, register_engine


def _dummy_factory(**kwargs):
    return MockAgent()


def test_register_and_list():
    register_engine("dummy-test", _dummy_factory)
    assert "dummy-test" in list_engines()


def test_create_agent_backend_by_name():
    register_engine("dummy-test-2", _dummy_factory)
    backend = create_agent_backend("dummy-test-2", cwd="/tmp")
    assert isinstance(backend, MockAgent)


def test_unknown_engine_raises():
    with pytest.raises(ValueError, match="Unknown engine 'nope'"):
        create_agent_backend("nope", cwd="/tmp")


def test_invalid_engine_name_rejected():
    with pytest.raises(ValueError, match="Invalid engine name"):
        register_engine("bad name!", _dummy_factory)


def test_get_engine_config_extracts_subsection():
    config = {"engine": "corecoder", "engines": {"corecoder": {"model": "x"}}}
    assert get_engine_config("corecoder", config) == {"model": "x"}
    assert get_engine_config("unknown", config) == {}


def test_builtin_mock_engine():
    backend = create_agent_backend("mock", cwd="/tmp")
    assert isinstance(backend, MockAgent)


def test_builtin_corecoder_engine_registered():
    assert "corecoder" in list_engines()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_javis/test_engines.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'javis.engines'`）

- [ ] **Step 3: 实现**

创建 `javis/engines/registry.py`：

```python
"""Engine registry — maps engine names to AgentBackend factories.

Third-party engines register themselves with register_engine(); javis
resolves the active engine via javis.config and builds the backend with
create_agent_backend(). The registry module itself never imports concrete
engines (factories import lazily), so a mock-only environment stays light.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from javis.engine.protocol import AgentBackend

BackendFactory = Callable[..., AgentBackend]

_ENGINES: dict[str, BackendFactory] = {}


def register_engine(name: str, factory: BackendFactory) -> None:
    if not name or not name.isidentifier():
        raise ValueError(f"Invalid engine name: {name!r}")
    _ENGINES[name] = factory


def list_engines() -> list[str]:
    return sorted(_ENGINES)


def get_engine_config(name: str, config: dict) -> dict:
    """Extract the per-engine config subsection (or {})."""
    return dict(config.get("engines", {}).get(name, {}))


def create_agent_backend(
    name: str,
    *,
    model: str | None = None,
    system_prompt: str = "",
    cwd: str,
    max_turns: int | None = None,
    tool_metadata: dict[str, Any] | None = None,
    engine_config: dict | None = None,
) -> AgentBackend:
    factory = _ENGINES.get(name)
    if factory is None:
        available = ", ".join(list_engines()) or "(none registered)"
        raise ValueError(f"Unknown engine {name!r}; available: {available}")
    return factory(
        model=model,
        system_prompt=system_prompt,
        cwd=cwd,
        max_turns=max_turns,
        tool_metadata=tool_metadata or {},
        engine_config=engine_config or {},
    )


def _build_mock_backend(**kwargs) -> AgentBackend:
    del kwargs
    from javis.engine.mock_agent import MockAgent

    return MockAgent()


def _build_corecoder_backend(**kwargs) -> AgentBackend:
    from javis.engines.corecoder_backend import build_corecoder_backend

    return build_corecoder_backend(**kwargs)


def _register_builtin_engines() -> None:
    register_engine("mock", _build_mock_backend)
    register_engine("corecoder", _build_corecoder_backend)


_register_builtin_engines()


__all__ = [
    "BackendFactory",
    "create_agent_backend",
    "get_engine_config",
    "list_engines",
    "register_engine",
]
```

创建 `javis/engines/__init__.py`：

```python
"""Engine registry and built-in agent backends."""

from javis.engines.registry import (
    BackendFactory,
    create_agent_backend,
    get_engine_config,
    list_engines,
    register_engine,
)

__all__ = [
    "BackendFactory",
    "create_agent_backend",
    "get_engine_config",
    "list_engines",
    "register_engine",
]
```

注意：`_build_corecoder_backend` 引用 `javis.engines.corecoder_backend`——该模块在 Task 7 创建。Task 6 结束时 registry 已注册 corecoder 但 factory 尚不存在；`test_builtin_corecoder_engine_registered` 只查名字，不调用 factory，所以测试通过。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_javis/test_engines.py -q`
Expected: 全部 PASS（`test_builtin_corecoder_engine_registered` 只查注册表，不触发 factory）

- [ ] **Step 5: Commit**

```bash
git add javis/engines/ tests/test_javis/test_engines.py
git commit -m "feat(javis): add engine registry with builtin mock and corecoder"
```

---

### Task 7: javis — `CoreCoderBackend` adapter

**Files:**
- Create: `javis/engines/corecoder_backend.py`
- Test: `tests/test_javis/test_corecoder_backend.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_javis/test_corecoder_backend.py`：

```python
"""Tests for CoreCoderBackend — the AgentBackend adapter over corecoder.Agent."""

from __future__ import annotations

import pytest

from corecoder.agent import Agent
from corecoder.llm import AsyncScriptedLLM, LLMResponse, ToolCall

from javis.engine.types import (
    AgentError,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.engines.corecoder_backend import CoreCoderBackend, _to_corecoder_messages
from javis.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock


def _backend(script, **kwargs) -> CoreCoderBackend:
    llm = AsyncScriptedLLM(script=script)
    agent = Agent(llm=llm)
    return CoreCoderBackend(agent, model="test-model", system_prompt="test system", **kwargs)


async def _collect(backend: CoreCoderBackend, prompt: str):
    return [e async for e in backend.run_turn(prompt, context=None)]  # context unused by backend


@pytest.mark.asyncio
async def test_plain_text_turn():
    backend = _backend([LLMResponse(content="hello world")])
    events = await _collect(backend, "hi")

    deltas = [e for e in events if isinstance(e, AgentTextDelta)]
    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert "".join(e.text for e in deltas) == "hello world"
    assert len(ends) == 1
    assert ends[0].text == "hello world"
    assert ends[0].usage is not None
    assert ends[0].usage.output_tokens == 2  # "hello world"


@pytest.mark.asyncio
async def test_tool_call_turn(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")
    backend = _backend([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"file_path": str(target)})]),
        LLMResponse(content="file read done"),
    ])
    events = await _collect(backend, "read the file")

    starts = [e for e in events if isinstance(e, AgentToolCallStart)]
    results = [e for e in events if isinstance(e, AgentToolCallResult)]
    assert len(starts) == 1
    assert starts[0].tool_name == "read_file"
    assert starts[0].tool_input == {"file_path": str(target)}
    assert len(results) == 1
    assert results[0].tool_name == "read_file"
    assert "line one" in results[0].output
    assert results[0].is_error is False

    ends = [e for e in events if isinstance(e, AgentTurnEnd)]
    assert len(ends) == 1
    assert ends[0].text == "file read done"


@pytest.mark.asyncio
async def test_llm_failure_yields_error_event():
    backend = _backend([LLMResponse(content="first")])
    await _collect(backend, "one")

    events = await _collect(backend, "two")  # script exhausted -> RuntimeError
    errors = [e for e in events if isinstance(e, AgentError)]
    assert len(errors) == 1
    assert "out of turns" in errors[0].message
    assert not any(isinstance(e, AgentTurnEnd) for e in events)


def test_load_history_converts_messages():
    backend = _backend([])
    backend.load_history([
        ConversationMessage.from_user_text("question"),
        ConversationMessage(role="assistant", content=[TextBlock(text="answer")]),
    ])
    assert backend.agent.messages == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]


def test_clear_history_resets_agent():
    backend = _backend([LLMResponse(content="hi")])
    backend.agent.chat("hello")  # sync path still works
    assert backend.agent.messages
    backend.clear_history()
    assert backend.agent.messages == []


def test_to_corecoder_messages_handles_images_and_tool_results():
    messages = [
        ConversationMessage(role="user", content=[
            TextBlock(text="look at this"),
            ImageBlock(media_type="image/png", data="AAAA", source_path="/x.png"),
        ]),
        ConversationMessage(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content="['a.py']", is_error=False),
        ]),
        ConversationMessage(role="assistant", content=[
            TextBlock(text="checking"),
        ]),
    ]
    converted = _to_corecoder_messages(messages)

    assert converted[0]["role"] == "user"
    assert "[image omitted" in converted[0]["content"]
    assert converted[1] == {"role": "tool", "tool_call_id": "call_1", "content": "['a.py']"}
    assert converted[2] == {"role": "assistant", "content": "checking"}


def test_build_corecoder_backend_applies_config():
    from javis.engines.corecoder_backend import build_corecoder_backend

    backend = build_corecoder_backend(
        model="deepseek-chat",
        system_prompt="sp",
        cwd="/tmp",
        max_turns=12,
        engine_config={"api_key": "k"},
    )
    assert isinstance(backend, CoreCoderBackend)
    assert backend.model == "deepseek-chat"
    assert backend.agent.max_rounds == 12
    assert backend.agent._system == "sp"
```

注意：`run_turn` 的 `context` 参数——adapter 不使用它（agent 已持有历史），测试传 None。`_backend` 里 `context=None` 即可。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_javis/test_corecoder_backend.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'javis.engines.corecoder_backend'`）

- [ ] **Step 3: 实现**

创建 `javis/engines/corecoder_backend.py`：

```python
"""CoreCoder engine adapter — drives corecoder.Agent.achat as an AgentBackend.

Producer-consumer pattern over asyncio.Queue: the achat task is the producer
(a native asyncio task, no thread bridge), run_turn is the consumer yielding
AgentEvents. Cancellation: cancelling run_turn's awaiting task propagates into
achat (its own CancelledError handling keeps history valid), then run_turn's
finally cancels the producer task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, AsyncIterator

from javis.engine.protocol import AgentBackend
from javis.engine.types import (
    AgentContext,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from javis.usage import UsageSnapshot

_IMAGE_PLACEHOLDER = "[image omitted: engine does not process images]"


def _to_corecoder_messages(messages: list[ConversationMessage]) -> list[dict]:
    """Convert javis conversation history into OpenAI-style message dicts.

    Tool results live in ``user`` messages in javis; corecoder expects them
    as standalone ``tool`` messages with ``tool_call_id``.
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role == "user":
            tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text_parts = []
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ImageBlock):
                    text_parts.append(_IMAGE_PLACEHOLDER)
            text = "".join(text_parts).strip()
            if text:
                out.append({"role": "user", "content": text})
            for tr in tool_results:
                out.append({"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content})
        elif msg.role == "assistant":
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.text or None}
            if msg.tool_uses:
                assistant["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input),
                        },
                    }
                    for tu in msg.tool_uses
                ]
            out.append(assistant)
    return out


class CoreCoderBackend(AgentBackend):
    """AgentBackend adapter over a corecoder.Agent (async path)."""

    def __init__(
        self,
        agent: Any,
        *,
        model: str,
        system_prompt: str,
        max_turns: int | None = None,
    ) -> None:
        self._agent = agent
        self._model = model
        if system_prompt:
            agent.set_system_prompt(system_prompt)
        if max_turns is not None:
            agent.max_rounds = max(1, int(max_turns))

    @property
    def agent(self) -> Any:
        return self._agent

    @property
    def model(self) -> str:
        return self._model

    async def run_turn(
        self,
        prompt: str | ConversationMessage,
        *,
        context: AgentContext,
    ) -> AsyncIterator[AgentEvent]:
        del context  # the agent owns its history; context is informational
        prompt_text = prompt.text if isinstance(prompt, ConversationMessage) else prompt
        llm = self._agent.llm
        prompt_before = getattr(llm, "total_prompt_tokens", 0)
        completion_before = getattr(llm, "total_completion_tokens", 0)

        queue: asyncio.Queue[tuple] = asyncio.Queue()

        def emit(item: tuple) -> None:
            queue.put_nowait(item)

        async def producer() -> None:
            try:
                final = await self._agent.achat(
                    prompt_text,
                    on_token=lambda t: emit(("delta", t)),
                    on_tool=lambda name, args: emit(("tool_start", name, args)),
                    on_tool_result=lambda n, a, out, err: emit(("tool_result", n, a, out, err)),
                )
                emit(("done", final))
            except Exception as exc:
                emit(("error", exc))

        task = asyncio.create_task(producer())
        final_text: str | None = None
        try:
            while True:
                kind, *payload = await queue.get()
                if kind == "done":
                    final_text = payload[0]
                    break
                if kind == "error":
                    yield AgentError(message=str(payload[0]), recoverable=True)
                    return
                if kind == "delta":
                    yield AgentTextDelta(text=payload[0])
                elif kind == "tool_start":
                    yield AgentToolCallStart(tool_name=payload[0], tool_input=payload[1])
                elif kind == "tool_result":
                    yield AgentToolCallResult(
                        tool_name=payload[0],
                        output=payload[1],
                        is_error=payload[2],
                    )
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        input_tokens = max(0, getattr(llm, "total_prompt_tokens", 0) - prompt_before)
        output_tokens = max(0, getattr(llm, "total_completion_tokens", 0) - completion_before)
        yield AgentTurnEnd(
            text=final_text or "",
            usage=UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def load_history(self, messages: list[ConversationMessage]) -> None:
        self._agent.load_messages(_to_corecoder_messages(messages))

    def clear_history(self) -> None:
        self._agent.reset()


def build_corecoder_backend(
    *,
    model: str | None = None,
    system_prompt: str = "",
    cwd: str | None = None,
    max_turns: int | None = None,
    tool_metadata: dict[str, Any] | None = None,
    engine_config: dict | None = None,
) -> CoreCoderBackend:
    """Build a CoreCoderBackend from env + per-engine config."""
    del cwd, tool_metadata
    from corecoder.agent import Agent
    from corecoder.config import Config
    from corecoder.llm import AsyncLLM

    cfg = Config.from_env()
    if engine_config:
        cfg = Config(
            model=engine_config.get("model", cfg.model),
            api_key=engine_config.get("api_key", cfg.api_key),
            base_url=engine_config.get("base_url", cfg.base_url),
            max_tokens=engine_config.get("max_tokens", cfg.max_tokens),
            temperature=engine_config.get("temperature", cfg.temperature),
            max_context_tokens=engine_config.get("max_context_tokens", cfg.max_context_tokens),
            provider=engine_config.get("provider", cfg.provider),
        )

    resolved_model = model or cfg.model
    llm = AsyncLLM(model=resolved_model, api_key=cfg.api_key, base_url=cfg.base_url)
    agent = Agent(llm=llm, max_context_tokens=cfg.max_context_tokens)
    return CoreCoderBackend(
        agent,
        model=resolved_model,
        system_prompt=system_prompt,
        max_turns=max_turns,
    )


__all__ = ["CoreCoderBackend", "build_corecoder_backend"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_javis/test_corecoder_backend.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add javis/engines/corecoder_backend.py tests/test_javis/test_corecoder_backend.py
git commit -m "feat(javis): add CoreCoderBackend adapter and factory"
```

---

### Task 8: javis — 配置解析 `javis/config.py`

**Files:**
- Create: `javis/config.py`
- Test: `tests/test_javis/test_config.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_javis/test_config.py`：

```python
"""Tests for javis engine configuration resolution."""

from __future__ import annotations

import pytest

from javis.config import DEFAULT_ENGINE, load_config, resolve_engine_name


def test_default_engine_is_corecoder():
    assert DEFAULT_ENGINE == "corecoder"


def test_load_config_missing_file_returns_empty(tmp_path):
    assert load_config(tmp_path) == {}


def test_load_config_reads_json(tmp_path):
    (tmp_path / "config.json").write_text('{"engine": "mock"}', encoding="utf-8")
    assert load_config(tmp_path) == {"engine": "mock"}


def test_load_config_corrupt_json_returns_empty(tmp_path):
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    assert load_config(tmp_path) == {}


def test_resolve_priority_cli_over_env_over_config():
    config = {"engine": "from-config"}
    env = {"JAVIS_ENGINE": "from-env"}
    assert resolve_engine_name(None, config, env) == "from-env"
    assert resolve_engine_name("from-cli", config, env) == "from-cli"


def test_resolve_falls_back_to_default():
    assert resolve_engine_name(None, {}, {}) == "corecoder"
    assert resolve_engine_name(None, {"engine": "from-config"}, {}) == "from-config"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_javis/test_config.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'javis.config'`）

- [ ] **Step 3: 实现**

创建 `javis/config.py`：

```python
"""javis configuration — engine selection from config.json, env and CLI.

Priority: CLI --engine > env JAVIS_ENGINE > config.json "engine" > default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from javis.workspace import get_workspace_root

DEFAULT_ENGINE = "corecoder"

CONFIG_FILENAME = "config.json"


def load_config(workspace: str | Path | None = None) -> dict:
    """Read <workspace>/config.json. Missing or corrupt file -> {}."""
    config_path = get_workspace_root(workspace) / CONFIG_FILENAME
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_engine_name(
    cli: str | None = None,
    config: dict | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active engine name by priority: CLI > env > config > default."""
    env = env if env is not None else os.environ
    config = config or {}
    if cli:
        return cli
    if env.get("JAVIS_ENGINE"):
        return env["JAVIS_ENGINE"]
    if config.get("engine"):
        return str(config["engine"])
    return DEFAULT_ENGINE


__all__ = ["CONFIG_FILENAME", "DEFAULT_ENGINE", "load_config", "resolve_engine_name"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_javis/test_config.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add javis/config.py tests/test_javis/test_config.py
git commit -m "feat(javis): add engine config resolution (config.json / env / CLI)"
```

---

### Task 9: runtime / CLI 接线

**Files:**
- Modify: `javis/runtime.py:76-146`（build_javis_runtime）
- Modify: `javis/backend_host.py:521-546`（run_javis_backend）
- Modify: `javis/cli.py:31-92`（main）
- Modify: `javis/react_launcher.py:74-143`（build_backend_command / launch_react_tui）
- Test: `tests/test_javis/test_runtime.py`（适配 + 新增）

- [ ] **Step 1: 写失败测试——runtime engine 解析**

在 `tests/test_javis/test_runtime.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_build_javis_runtime_default_engine_is_corecoder(isolated_env):
    from javis.engines.corecoder_backend import CoreCoderBackend

    bundle = await build_javis_runtime(cwd=str(isolated_env), engine="corecoder")
    assert isinstance(bundle.engine._agent, CoreCoderBackend)


@pytest.mark.asyncio
async def test_build_javis_runtime_engine_mock(isolated_env):
    bundle = await build_javis_runtime(cwd=str(isolated_env), engine="mock")
    assert isinstance(bundle.engine._agent, MockAgent)


@pytest.mark.asyncio
async def test_build_javis_runtime_engine_and_backend_mutually_exclusive(isolated_env):
    with pytest.raises(ValueError, match="either engine= or agent_backend="):
        await build_javis_runtime(cwd=str(isolated_env), engine="mock", agent_backend=MockAgent())


@pytest.mark.asyncio
async def test_build_javis_runtime_unknown_engine_raises(isolated_env):
    with pytest.raises(ValueError, match="Unknown engine 'nope'"):
        await build_javis_runtime(cwd=str(isolated_env), engine="nope")


@pytest.mark.asyncio
async def test_build_javis_runtime_restore_calls_backend_load_history(isolated_env):
    class RecordingBackend(MockAgent):
        def __init__(self) -> None:
            super().__init__()
            self.history_calls = 0

        def load_history(self, messages) -> None:
            self.history_calls += 1

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "previous question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]},
    ]
    backend = RecordingBackend()
    bundle = await build_javis_runtime(
        cwd=str(isolated_env), agent_backend=backend, restore_messages=messages
    )
    assert backend.history_calls == 1
    assert len(bundle.engine.messages) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_javis/test_runtime.py -q`
Expected: 新 5 个 FAIL（`TypeError: build_javis_runtime() got an unexpected keyword argument 'engine'`）

- [ ] **Step 3: 实现——runtime.py**

`build_javis_runtime` 整体替换（行 76-146）。关键变化：新增 `engine` 参数与互斥校验；`agent_backend is None` 时走注册表；model 从 backend 回读；restore 时调用 `load_history` 钩子：

```python
async def build_javis_runtime(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    agent_backend: AgentBackend | None = None,
    engine: str | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_backend: JavisSessionBackend | None = None,
    workspace: str | Path | None = None,
) -> RuntimeBundle:
    """Assemble a ``RuntimeBundle``.

    The agent backend is resolved in one of two ways:
      - ``agent_backend=...`` — explicit backend (used by tests)
      - ``engine=...`` — named engine via the registry (config.json / env
        fall back to the default engine when omitted)
    Passing both raises ``ValueError``.
    """
    if engine is not None and agent_backend is not None:
        raise ValueError("Pass either engine= or agent_backend=, not both")

    cwd_resolved = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    workspace_root = initialize_workspace(workspace)
    system_prompt_text = system_prompt or build_javis_system_prompt(cwd_resolved, workspace=workspace_root)

    tool_metadata: dict[str, Any] = {
        "permission_mode": "default",
        "session_id": "",
    }
    if isinstance(restore_tool_metadata, dict):
        tool_metadata.update(restore_tool_metadata)

    session_id = uuid4().hex[:12]
    tool_metadata["session_id"] = session_id

    if agent_backend is None:
        from javis.config import load_config, resolve_engine_name
        from javis.engines import create_agent_backend, get_engine_config

        config_data = load_config(workspace_root)
        engine_name = resolve_engine_name(engine, config_data)
        agent_backend = create_agent_backend(
            engine_name,
            model=model,
            system_prompt=system_prompt_text,
            cwd=cwd_resolved,
            max_turns=max_turns,
            tool_metadata=tool_metadata,
            engine_config=get_engine_config(engine_name, config_data),
        )

    model_name = model or getattr(agent_backend, "model", None) or "javis-mock"

    engine = MockEngine(
        agent_backend=agent_backend,
        model=model_name,
        system_prompt=system_prompt_text,
        cwd=cwd_resolved,
        max_turns=max_turns,
        tool_metadata=tool_metadata,
    )

    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine.load_messages(restored)
        if hasattr(agent_backend, "load_history"):
            agent_backend.load_history(restored)

    app_state = AppStateStore(
        AppState(
            model=model_name,
            cwd=cwd_resolved,
            permission_mode="default",
            theme="default",
            provider="javis",
            auth_status="ok",
            effort="medium",
            passes=1,
            output_style="default",
        )
    )

    return RuntimeBundle(
        engine=engine,
        cwd=cwd_resolved,
        app_state=app_state,
        commands=create_default_command_registry(),
        session_backend=session_backend or JavisSessionBackend(workspace_root),
        session_id=session_id,
        system_prompt=system_prompt_text,
    )
```

`run_javis_print_mode`（行 228-235）签名加 `engine: str | None = None`，传给 `build_javis_runtime(engine=engine, ...)`。

- [ ] **Step 4: 实现——backend_host.py**

`run_javis_backend`（行 521-529）签名与调用改为：

```python
async def run_javis_backend(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    engine: str | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
) -> int:
    """Run the structured React backend host."""
    import os
    if cwd:
        os.chdir(cwd)
    bundle = await build_javis_runtime(
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        engine=engine,
        restore_messages=restore_messages,
        restore_tool_metadata=restore_tool_metadata,
        workspace=workspace,
    )
```

- [ ] **Step 5: 实现——cli.py**

`main` 参数区（行 34-41）加：

```python
    engine: str | None = typer.Option(None, "--engine", help="Agent engine (default: config.json or corecoder)"),
```

三处调用点透传：`run_javis_backend(..., engine=engine)`、`run_javis_print_mode(..., engine=engine)`、`launch_react_tui(..., engine=engine)`。

- [ ] **Step 6: 实现——react_launcher.py**

`build_backend_command`（行 74-91）签名与实现改为：

```python
def build_backend_command(
    *,
    cwd: str | None = None,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    engine: str | None = None,
) -> list[str]:
    """Return the command the React frontend will spawn to start the backend."""
    command = [sys.executable, "-m", "javis", "--backend-only"]
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace:
        command.extend(["--workspace", str(workspace)])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    if engine:
        command.extend(["--engine", engine])
    return command
```

`launch_react_tui`（行 94-100）签名加 `engine: str | None = None`，`build_backend_command(..., engine=engine)` 调用处传参。

- [ ] **Step 7: 适配现有测试——test_runtime.py**

现有无 `agent_backend`/`engine` 的测试全部加 `engine="mock"`（默认已变 corecoder）：
`test_build_javis_runtime_returns_bundle`、`test_build_javis_runtime_uses_mock_engine`、`test_build_javis_runtime_session_backend`、`test_build_javis_runtime_preserves_cwd`、`test_build_javis_runtime_restores_messages`、`test_build_javis_runtime_accepts_custom_model`、`test_build_javis_runtime_includes_commands` —— 调用处加 `engine="mock"`。

- [ ] **Step 8: 跑全量测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 9: Commit**

```bash
git add javis/runtime.py javis/backend_host.py javis/cli.py javis/react_launcher.py tests/test_javis/test_runtime.py
git commit -m "feat(javis): wire engine selection through runtime, CLI and frontend launcher"
```

---

### Task 10: 收尾验证

**Files:** 无新文件

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 2: 冒烟——print mode（mock）**

Run: `.venv/bin/python -m javis --print "hello" --engine mock`
Expected: 输出 `[mock] You said: hello ...`

- [ ] **Step 3: 冒烟——print mode（corecoder，脚本化验证事件流）**

Run: `.venv/bin/python -c "
import asyncio
from corecoder.agent import Agent
from corecoder.llm import AsyncScriptedLLM, LLMResponse
from javis.engines.corecoder_backend import CoreCoderBackend

async def main():
    backend = CoreCoderBackend(Agent(llm=AsyncScriptedLLM([LLMResponse(content='hi from corecoder')])), model='t', system_prompt='')
    events = [e async for e in backend.run_turn('hello', context=None)]
    print([type(e).__name__ for e in events])
    print(events[-1].text)

asyncio.run(main())
"`
Expected: `['AgentTextDelta', 'AgentTurnEnd']` + `hi from corecoder`

- [ ] **Step 4: 构建验证**

Run: `uv build`
Expected: `Successfully built dist/javis_ai-...`（javis、corecoder、javis/_frontend 都在 wheel 里）

- [ ] **Step 5: Commit（如有遗留改动）**

```bash
git status --short
git add -u
git commit -m "chore: final cleanup after multi-engine wiring"
```

---

## Self-Review

**Spec 覆盖：**
- §4 协议 v2（usage + 可选钩子）→ Task 1 ✓
- §5.1 AsyncLLM → Task 4 ✓；§5.2 achat → Task 5 ✓；§5.3 on_tool_result → Task 2 ✓；§5.4 公开接口 → Task 3 ✓；§5.5 AsyncScriptedLLM → Task 4 ✓
- §6 CoreCoderBackend → Task 7 ✓
- §7.1 registry → Task 6 ✓；§7.2 config → Task 8 ✓；§7.3 runtime/CLI → Task 9 ✓
- §8 数据流、§9 错误处理 → Task 7/9 测试覆盖 ✓
- §10 测试策略 → 各 Task + Task 10 收尾 ✓

**已知偏差（有意为之）：**
1. 可选钩子不进 Protocol 类本体（runtime_checkable isinstance 会因缺成员失败），改为 docstring + hasattr——Task 1 说明。
2. `maybe_compress` 在 achat 里不传 llm（同步 LLM 压缩会与 AsyncLLM 冲突），走提取回退——Task 5 实现已注明。
3. `_exec_tool` 保留为 1 行包装（`_exec_tool_with_status` 的兼容层），demo.py 及其余调用方不受影响。

**类型一致性检查：**
- `create_agent_backend` 工厂签名（model/system_prompt/cwd/max_turns/tool_metadata/engine_config）在 Task 6 定义、Task 7 factory 与 Task 9 runtime 调用一致 ✓
- `CoreCoderBackend.model` property 被 runtime 的 `getattr(agent_backend, "model", None)` 读取 ✓
- `AgentTurnEnd.usage: UsageSnapshot | None` 在 Task 1 定义，Task 7 构造时传 `UsageSnapshot(...)` ✓
- `load_history(messages: list[ConversationMessage])` / `clear_history()` 签名在 Task 1 docstring、Task 7 实现、Task 9 调用一致 ✓
- 测试 fixture `isolated_env`（test_runtime / test_backend_host）已删除 OPENHARNESS_* env 并设置 JAVIS_WORKSPACE ✓
