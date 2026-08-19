"""Core agent loop.

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import asyncio
import concurrent.futures
import inspect
from collections.abc import Awaitable, Callable
from .llm import LLM, ToolCall
from .tools import ALL_TOOLS
from .tools.base import Tool
from .tools.agent import AgentTool
from .prompt import system_prompt
from .context import ContextManager


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        permission_checker: Callable[[str, dict], Awaitable[str]] | None = None,
    ):
        """``permission_checker`` is an async hook called before each tool
        execution (event-loop thread): receives ``(tool_name, arguments)`` and
        returns ``"allow"`` or a deny reason. When denied the tool is not
        executed and the reason is recorded in the conversation.
        """
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self.permission_checker = permission_checker
        self._system = system_prompt(self.tools)

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None, on_tool_result=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            try:
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
            except KeyboardInterrupt:
                # Ctrl+C mid-execution would leave the assistant tool_calls
                # message without replies, poisoning the next request; backfill
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    async def achat(self, user_input: str, on_token=None, on_tool=None, on_tool_result=None) -> str:
        """Async counterpart of chat(): same loop over awaitable LLM.chat().

        Requires an async LLM (AsyncLLM/AsyncScriptedLLM); building the Agent
        with a sync LLM (LLM/ScriptedLLM/LiteLLM) raises TypeError on the
        first call because llm.chat() is not awaitable.  on_tool and
        on_tool_result fire on the event-loop thread (never from a worker
        thread), so they may safely use put_nowait-style asyncio APIs.

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
                    denied = await self._check_permission(tc)
                    if denied is not None:
                        result, is_error = denied
                    else:
                        result, is_error = await asyncio.to_thread(self._exec_tool_with_status, tc)
                    if on_tool_result:
                        on_tool_result(tc.name, tc.arguments, result, is_error)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    # fire on_tool on the event-loop thread (NOT inside
                    # _exec_tools_parallel, which the to_thread worker runs);
                    # put_nowait-style callbacks are only safe here
                    if on_tool:
                        for tc in resp.tool_calls:
                            on_tool(tc.name, tc.arguments)
                    results: dict[int, tuple[str, bool]] = {}
                    to_execute: list[tuple[int, ToolCall]] = []
                    for i, tc in enumerate(resp.tool_calls):
                        denied = await self._check_permission(tc)
                        if denied is not None:
                            results[i] = denied
                        else:
                            to_execute.append((i, tc))
                    if to_execute:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                            futures = {
                                i: pool.submit(self._exec_tool_with_status, tc)
                                for i, tc in to_execute
                            }
                            for i, future in futures.items():
                                results[i] = future.result()
                    for i, tc in enumerate(resp.tool_calls):
                        result, is_error = results[i]
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

    async def _check_permission(self, tc: ToolCall) -> tuple[str, bool] | None:
        """Run the permission hook; return ``(result, is_error)`` when denied."""
        if self.permission_checker is None:
            return None
        decision = await self.permission_checker(tc.name, tc.arguments)
        if decision == "allow":
            return None
        return (f"[permission denied: {decision}]", True)

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

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[tuple[str, bool]]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool_with_status, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    def _answer_pending_tool_calls(self, tool_calls):
        """Backfill a tool reply for every call that didn't get one.

        OpenAI-compatible APIs reject a request where an assistant message has
        tool_calls without a matching tool reply for each id, so this keeps the
        history valid when execution is interrupted partway through.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted]",
                })

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()

    def load_messages(self, messages: list[dict]):
        """Replace conversation history (used when restoring a session)."""
        self.messages = list(messages)

    def set_system_prompt(self, prompt: str):
        """Replace the system prompt for subsequent rounds."""
        self._system = prompt
