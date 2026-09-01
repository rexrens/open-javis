"""ReactLoopAgent: the turn/step driver over queued input and step-boundary work.

Port of ``packages/core/agent-loop/src/agent.ts`` (dsh ``ReactLoopAgent``).
Every request is derived from the session log; the agent owns a phase
state machine (idle / maintenance / running), an :class:`~javis.harness.inbox.Inbox`,
and the live event dispatch surface:

======================  ============  =====================================================
event                   mode          contract
======================  ============  =====================================================
``agent/status``        emit          lifecycle transition (``idle`` / ``running``)
``agent/error``         emit          failure at its live boundary (turn/step)
``agent/inbox/*``       emit          inserted / claimed / discarded
``agent/pre-step``      waterfall     may reject the step or rewrite its messages
``agent/request``       waterfall     may rewrite provider/model/config
``agent/request-error`` waterfall     may claim recovery (``{kind: "retry"}``)
``agent/turn-stopping`` serial       around the turn boundary
======================  ============  =====================================================

Turn loop (dsh ``kick → turn → step``)::

    user message (inbox: next-turn / next-step)
      └─ pre-step: claim + system-prompt assembly + agent/pre-step waterfall
           └─ buildRequest: agent/request waterfall + prepare_call
                │            + request/header & request/context change log
                └─ llm.stream → StreamChunk → BlockAssembler
                     │   (error/aborted finish → agent/request-error waterfall)
                     └─ assistant/message
                          └─ tool calls?
                              ├─ no  → turn completed (agent/turn-stopping)
                              └─ yes → execute_tool_calls (exclusive / parallel)
                                       → tool results → next step
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn

from .inbox import Inbox
from .llm import BlockAssembler, assemble_finish, normalized_stream
from .session import Session
from .tools import execute_tool_calls
from .types import (
    AbortController,
    AbortedFinish,
    AbortError,
    AbortSignal,
    AgentCancelCause,
    AgentOptions,
    AssistantMessage,
    ErrorFinish,
    Events,
    GenerateOptions,
    LlmCallConfig,
    LlmError,
    LlmFailure,
    PreStepDecision,
    PreStepEnter,
    PromptAssembly,
    RetryAction,
    SessionEvents,
    SessionId,
    ToolCallBlock,
    TurnAborted,
    TurnBlocked,
    TurnCompleted,
    TurnEndReason,
    TurnError,
    TurnMaxTokens,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Phase state machine (dsh Phase)
# ---------------------------------------------------------------------------


@dataclass
class IdlePhase:
    kind: str = "idle"
    last_turn: int = 0


@dataclass
class MaintenancePhase:
    kind: str = "maintenance"
    abort: AbortController = field(default_factory=AbortController)
    last_turn: int = 0
    wake_requested: bool = False


@dataclass
class RunningPhase:
    kind: str = "running"
    abort: AbortController = field(default_factory=AbortController)
    turn: int = 0
    step: int = 0
    wake_requested: bool = False


Phase = IdlePhase | MaintenancePhase | RunningPhase


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ReactLoopAgent:
    """Drives one session through turn and step boundaries (dsh ``Agent``)."""

    def __init__(
        self,
        loop_ctx: Any,
        id: SessionId,
        options: AgentOptions,
        session: Session,
    ) -> None:
        self._ctx = loop_ctx
        self.id = id
        self.options = options
        self.session = session
        self._activity_done: asyncio.Future | None = None
        last_turn = session.last_turn()
        self._phase: Phase = IdlePhase(last_turn=last_turn)
        self.inbox = Inbox(
            session,
            inserted=lambda message: self._dispatch_emit(Events.AGENT_INBOX_INSERTED, {"message": message}),
            claimed=lambda message, turn: self._dispatch_emit(
                Events.AGENT_INBOX_CLAIMED, {"message": message, "turn": turn}
            ),
            discarded=lambda message: self._dispatch_emit(Events.AGENT_INBOX_DISCARDED, {"message": message}),
        )
        #: Agent-scoped context (dsh ``scope.ctx.extend({agent})``).
        self.ctx = loop_ctx.extend({"agent": self})
        self._request_header_logged = False

    # -- identity ------------------------------------------------------------

    @property
    def status(self) -> str:
        return "idle" if self._phase.kind in ("idle", "maintenance") else "running"

    @property
    def last_turn(self) -> int:
        return self.session.last_turn()

    def _set_phase(self, next_phase: Phase) -> None:
        previous = self.status
        self._phase = next_phase
        if self.status != previous:
            self._dispatch_emit(Events.AGENT_STATUS, {"status": self.status})

    # -- event dispatch (dsh agentEvents) ------------------------------------

    def _dispatch_emit(self, name: str, payload: dict[str, Any]) -> None:
        self._ctx.emit(name, {**payload, "agent": self})

    async def _dispatch_serial(self, name: str, payload: dict[str, Any]) -> Any:
        return await self._ctx.serial(name, {**payload, "agent": self})

    def _dispatch_waterfall(self, name: str, payload: dict[str, Any], default: Callable[..., Any]) -> Any:
        return self._ctx.waterfall(name, {**payload, "agent": self}, default)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    # -- input ---------------------------------------------------------------

    def send(self, message: UserMessage, target: str, wakeup: bool) -> None:
        """Route identified input to an inbox boundary and optionally wake the driver.

        Waking input submitted after active cancellation is queued for the next
        turn and runs when the aborted activity converges to idle.
        """
        phase = self._phase
        waking_after_abort = wakeup and phase.kind != "idle" and phase.abort.signal.aborted
        resolved = "next-turn" if waking_after_abort else target
        length = len(self.inbox.next_step if resolved == "next-step" else self.inbox.next_turn)
        self.inbox.splice(resolved, length, 0, [message])
        if wakeup:
            self._wake_driver(waking_after_abort)

    def followup(self, message: UserMessage) -> None:
        """Queue an ordinary follow-up turn and wake the driver."""
        self.send(message, "next-turn", True)

    def steer(self, message: UserMessage) -> None:
        """Queue steering input for the next step of the active turn."""
        self.send(message, "next-step", True)

    def inject(self, message: UserMessage) -> None:
        """Queue input for the next step without waking an idle driver."""
        self.send(message, "next-step", False)

    def cancel(self, cause: AgentCancelCause, keep_inbox: bool = False) -> None:
        """Abort the active activity; the first cause wins for that activity."""
        if not keep_inbox:
            self.inbox.clear()
            if self._phase.kind != "idle":
                self._phase.wake_requested = False
        if self._phase.kind != "idle":
            self._phase.abort.abort(cause)

    # -- lifecycle -----------------------------------------------------------

    def run_maintenance(self, job: Callable[[AbortSignal], Awaitable[Any]]) -> asyncio.Task:
        """Run one non-turn maintenance task from the true idle phase (dsh)."""
        if self._phase.kind != "idle":
            raise RuntimeError(f'agent "{self.id}" already has active work')
        maintenance = MaintenancePhase(last_turn=self._phase.last_turn)
        self._set_phase(maintenance)

        async def _run() -> Any:
            loop = asyncio.get_running_loop()
            done = loop.create_future()
            self._activity_done = done
            try:
                result = await job(maintenance.abort.signal)
            except BaseException as error:
                if not done.done():
                    done.set_exception(error)
                raise
            else:
                if not done.done():
                    done.set_result(result)
            finally:
                self._set_phase(IdlePhase(last_turn=maintenance.last_turn))
                if maintenance.wake_requested and self.inbox.has_pending:
                    self._wake_driver()
            return None

        return asyncio.ensure_future(_run())

    async def when_idle(self) -> None:
        """Resolve after the current whole-agent activity reaches quiescence."""
        while True:
            activity = self._activity_done
            if activity is None:
                return
            await activity
            if activity is self._activity_done:
                return

    # -- driver --------------------------------------------------------------

    def _wake_driver(self, wake_after_abort: bool = False) -> None:
        """Start one driver, or latch its wake behind maintenance/abort."""
        phase = self._phase
        if phase.kind != "idle":
            reason = phase.abort.signal.reason
            if (reason is None or reason.kind != "disposed") and (
                phase.kind == "maintenance" or wake_after_abort
            ):
                phase.wake_requested = True
            return
        loop = asyncio.get_running_loop()
        driver: asyncio.Future = loop.create_future()
        self._activity_done = driver
        self._set_phase(RunningPhase(turn=phase.last_turn))

        async def _run() -> None:
            try:
                await self._kick()
            finally:
                if not driver.done():
                    driver.set_result(None)

        loop.create_task(_run())

    def _throw_error(self, error: BaseException) -> NoReturn:
        """Report one failure at its live boundary, then preserve it for containment."""
        if self._phase.kind == "running":
            turn, step = self._phase.turn, self._phase.step
        else:
            turn, step = self._phase.last_turn, 0
        self._dispatch_emit(Events.AGENT_ERROR, {"turn": turn, "step": step, "error": error})
        raise error

    async def _kick(self) -> None:
        try:
            while await self._turn():
                pass
        except asyncio.CancelledError:
            raise
        except BaseException:  # noqa: BLE001 S110 — reported; contained at the driver boundary (dsh kick())
            pass
        finally:
            if self._phase.kind == "running":
                turn = self._phase.turn
                wake = self._phase.wake_requested
                self._set_phase(IdlePhase(last_turn=turn))
                if wake and self.inbox.has_pending:
                    self._wake_driver()

    # -- turn ----------------------------------------------------------------

    async def _turn(self) -> bool:
        """Open one turn before claiming its first proposed step."""
        if self._phase.kind != "running":
            self._throw_error(RuntimeError(f'agent "{self.id}": turn without driver reservation'))
        phase: RunningPhase = self._phase
        signal = phase.abort.signal
        signal.throw_if_aborted()
        turn = phase.turn + 1
        try:
            self.session.append(SessionEvents.TURN_START, {"turn": turn})
        except BaseException as error:  # noqa: BLE001
            self._throw_error(error)
        phase.turn = turn
        turn_ends: TurnEndReason | None = None
        target = "next-turn"
        try:
            while True:
                signal.throw_if_aborted()
                max_steps = self._loop_max_steps()
                if phase.step >= max_steps:
                    # javis guard: the dsh loop has no bound; stop the turn
                    # once the per-turn step cap is reached (replaces the old
                    # corecoder ``max_rounds`` semantics).
                    self._dispatch_emit(
                        Events.AGENT_LIMIT, {"turn": turn, "kind": "max-steps", "limit": max_steps}
                    )
                    turn_ends = TurnCompleted()
                    return False
                step = phase.step + 1
                decision, assembly = await self._pre_step(target, turn, step)
                if decision.kind == "reject":
                    turn_ends = TurnBlocked()
                    return False
                if turn_ends is not None and not decision.messages:
                    break
                # A removed waking message or an enter decision rewritten to
                # empty still owns the initial turn boundary, but it spends no model call.
                if phase.step == 0 and not decision.messages:
                    turn_ends = TurnCompleted()
                    return False
                signal.throw_if_aborted()
                self.session.append(SessionEvents.STEP_START, {"turn": turn, "step": step})
                phase.step = step
                try:
                    for message in decision.messages:
                        self.session.append(SessionEvents.USER_MESSAGE, {"message": message})
                    step_end = await self._step(assembly, turn, step, signal)
                    # max-tokens is sticky: once any step hits the ceiling, later
                    # steps that complete normally must not downgrade the outcome.
                    if turn_ends is None or turn_ends.kind != "max-tokens":
                        turn_ends = step_end
                finally:
                    self.session.append(SessionEvents.STEP_END, {"turn": turn, "step": step})
                signal.throw_if_aborted()
                if turn_ends is not None and not self.inbox.next_step:
                    await self._dispatch_serial(Events.AGENT_TURN_STOPPING, {"turn": turn, "signal": signal})
                    signal.throw_if_aborted()
                if turn_ends is not None and not self.inbox.next_step:
                    break
                target = "next-step"
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if signal.aborted:
                turn_ends = TurnAborted(reason=signal.reason)
                raise
            turn_ends = TurnError(failure=_flatten_error(error))
            self._throw_error(error)
        finally:
            self.session.append(SessionEvents.TURN_END, {"turn": turn, "reason": turn_ends})
        if not self.inbox.has_pending:
            return False
        phase.abort = AbortController()
        phase.wake_requested = False
        phase.step = 0
        return True

    async def _pre_step(self, target: str, turn: int, step: int) -> tuple[PreStepDecision, PromptAssembly]:
        """Claim the boundary's messages, assemble context, propose the step."""
        if self._phase.kind != "running":
            raise RuntimeError(f'agent "{self.id}": pre-step outside running phase')
        signal = self._phase.abort.signal
        claimed = self.inbox.claim(target, turn)
        system_prompt = self._ctx.get("systemPrompt")
        assembly = system_prompt.assemble(agent=self, signal=signal)
        assembly = await self._maybe_await(assembly)
        signal.throw_if_aborted()
        context = self._context_message(assembly)

        def _default(payload: dict[str, Any], _next: Any) -> PreStepDecision:
            messages = list(payload["messages"])
            if context is not None:
                messages.append(context)
            return PreStepEnter(messages=tuple(messages))

        decision = self._dispatch_waterfall(
            Events.AGENT_PRE_STEP,
            {"messages": tuple(claimed), "turn": turn, "step": step, "signal": signal},
            _default,
        )
        decision = await self._maybe_await(decision)
        signal.throw_if_aborted()
        return decision, assembly

    def _context_message(self, assembly: PromptAssembly) -> UserMessage | None:
        """Render the assembly's context sections as one step-boundary message."""
        system_prompt = self._ctx.get("systemPrompt")
        text = system_prompt.render_context(assembly)
        if not text:
            return None
        return UserMessage.from_text(text)

    # -- step ----------------------------------------------------------------

    async def _step(
        self, assembly: PromptAssembly, turn: int, step: int, signal: AbortSignal
    ) -> TurnEndReason | None:
        if self._phase.kind != "running":
            raise RuntimeError(f'agent "{self.id}": step outside running phase')
        system_prompt = self._ctx.get("systemPrompt")
        system = system_prompt.render_prompt(assembly)
        while True:
            request, prepared = await self._build_request(assembly, turn, step, system, signal)
            assembler = BlockAssembler()
            chunk_seqs: list[int] = []
            llm = self._ctx.get("llm")
            if prepared is not None and prepared.stream is not None:
                stream = prepared.stream(request)
            else:
                stream = llm.stream(request)
            try:
                async for chunk in normalized_stream(stream, request, signal):
                    signal.throw_if_aborted()
                    seq = self.session.append(
                        SessionEvents.ASSISTANT_CHUNK, {"turn": turn, "step": step, "chunk": chunk}
                    ).seq
                    chunk_seqs.append(seq)
                    assembler.push(chunk)
            except AbortError:
                self._append_interrupted(assembler, request)
                raise
            finish = assembler.finish
            if finish is None:
                finish = assemble_finish(assembler.blocks, assembler.usage)
            if isinstance(finish, AbortedFinish):
                self._append_interrupted(assembler, request)
                raise AbortError(signal.reason)
            if isinstance(finish, ErrorFinish):
                action = await self._request_error_action(turn, step, request, finish, prepared, signal)
                signal.throw_if_aborted()
                if not isinstance(action, RetryAction):
                    raise LlmError(finish.failure.message, finish.failure.code, finish.failure)
                continue  # a listener owns recovery: retry the step

            message = AssistantMessage(
                content=tuple(assembler.blocks),
                source={"provider": request.provider, "model": request.model},
            )
            data: dict[str, Any] = {"turn": turn, "step": step, "message": message}
            if assembler.usage is not None:
                data["usage"] = assembler.usage
            self.session.append(SessionEvents.ASSISTANT_MESSAGE, data, sourceEventSeqs=tuple(chunk_seqs))
            if finish.kind == "max-tokens":
                return TurnMaxTokens()
            tool_calls = [block for block in assembler.blocks if isinstance(block, ToolCallBlock)]
            if not tool_calls:
                return TurnCompleted()
            concluded = await execute_tool_calls(
                self._ctx,
                self.session,
                self,
                turn,
                step,
                tool_calls,
                signal,
                accept_context=lambda msg: self.inbox.splice("next-step", len(self.inbox.next_step), 0, [msg]),
            )
            return TurnCompleted() if concluded else None

    def _append_interrupted(self, assembler: BlockAssembler, request: GenerateOptions) -> None:
        """Record the partially-assembled assistant message when interrupted (dsh)."""
        content = assembler.interrupted_blocks()
        if not content:
            return
        message = AssistantMessage(
            content=tuple(content),
            source={"provider": request.provider, "model": request.model},
            interrupted=True,
        )
        data: dict[str, Any] = {"message": message, "interrupted": True}
        if assembler.usage is not None:
            data["usage"] = assembler.usage
        self.session.append(SessionEvents.ASSISTANT_MESSAGE, data)

    async def _request_error_action(
        self,
        turn: int,
        step: int,
        request: GenerateOptions,
        finish: ErrorFinish,
        prepared: Any,
        signal: AbortSignal,
    ) -> Any:
        """``agent/request-error`` waterfall: a listener may claim recovery (retry)."""

        def _default(_payload: dict[str, Any], _next: Any) -> Any:
            return None

        action = self._dispatch_waterfall(
            Events.AGENT_REQUEST_ERROR,
            {
                "turn": turn,
                "step": step,
                "provider": request.provider,
                "failure": finish.failure,
                "retryPolicy": prepared.retry_policy if prepared is not None else None,
                "signal": signal,
            },
            _default,
        )
        return await self._maybe_await(action)

    # -- request -------------------------------------------------------------

    async def _build_request(
        self,
        assembly: PromptAssembly,
        turn: int,
        step: int,
        system: str,
        signal: AbortSignal,
    ) -> tuple[GenerateOptions, Any]:
        """Compose one frozen request and bind it to the adapter registration."""
        llm = self._ctx.get("llm")
        options = self.options
        # The loop starts from its declared route (dsh requestProposal seed).
        seed = LlmCallConfig(
            provider=options.provider or "",
            model=options.model or "",
            max_tokens=options.max_tokens,
        )

        def _default(_payload: dict[str, Any], _next: Any) -> LlmCallConfig:
            return seed

        proposed = self._dispatch_waterfall(
            Events.AGENT_REQUEST,
            {"turn": turn, "step": step, "signal": signal},
            _default,
        )
        proposed = await self._maybe_await(proposed)
        signal.throw_if_aborted()
        if not proposed.provider or not proposed.model:
            raise LlmError(
                f'agent "{self.id}" has no provider/model: set AgentOptions.provider and '
                "AgentOptions.model or supply both via the agent/request waterfall",
                "NO_ROUTE",
            )
        prepared: Any = None
        try:
            prepared = llm.prepare_call(proposed, signal)
            prepared = await self._maybe_await(prepared)
            config: LlmCallConfig = prepared.config
        except LlmError as error:
            # Middleware may serve an unregistered route; terminal dispatch
            # still requires an adapter.
            if error.code != "NO_ADAPTER":
                raise
            config = proposed
        signal.throw_if_aborted()

        header = _canonical_header(
            config,
            adapter_defaults=prepared.adapter_defaults if prepared is not None else None,
            system=system,
            tools=list(assembly.tools),
        )
        baseline = self.session.request_header()
        if not self._request_header_logged:
            self.session.append(
                SessionEvents.REQUEST_HEADER,
                {"header": header, "reason": "initial" if baseline is None else "resume"},
            )
            self._request_header_logged = True
        elif baseline is None or not _header_equals(baseline, header):
            self.session.append(SessionEvents.REQUEST_HEADER, {"header": header, "reason": "change"})

        context_window = prepared.context.get("contextWindow") if prepared is not None and prepared.context else None
        request_context = {
            "provider": config.provider,
            "model": config.model,
            "contextWindow": context_window,
        }
        previous = self.session.request_context()
        if (
            previous is None
            or previous.get("provider") != request_context["provider"]
            or previous.get("model") != request_context["model"]
            or previous.get("contextWindow") != context_window
        ):
            self.session.append(SessionEvents.REQUEST_CONTEXT, request_context)
        signal.throw_if_aborted()

        request = GenerateOptions(
            provider=config.provider,
            model=config.model,
            messages=tuple(self._compress_history(list(self.session.derive_messages()))),
            system=system or None,
            tools=tuple(assembly.tools) if assembly.tools else None,
            max_tokens=config.max_tokens,
            signal=signal,
        )
        return request, prepared

    # -- javis extensions ----------------------------------------------------

    def _loop_config(self) -> Any:
        """The ``agentLoop`` service's config object (dsh ``ctx.agentLoop.config``)."""
        service = self._ctx.get("agentLoop")
        return getattr(service, "config", None) or service

    def _loop_max_steps(self) -> int:
        value = getattr(self._loop_config(), "max_steps_per_turn", 20)
        return max(1, int(value))

    def _compress_history(self, messages: list[Any]) -> list[Any]:
        """Apply the optional ``history_compressor`` after deriving messages,
        before the request is built (javis compression middleware slot)."""
        compressor = getattr(self._loop_config(), "history_compressor", None)
        if compressor is None:
            return messages
        return list(compressor(messages))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_error(error: BaseException) -> LlmFailure:
    """dsh errorChain: an LlmError keeps its facts; anything else flattens to text."""
    if isinstance(error, LlmError):
        return error.failure
    chain: list[str] = []
    current: BaseException | None = error
    while current is not None:
        message = str(current)
        if not chain or chain[-1] != message:
            chain.append(message)
        current = current.__cause__ if current.__cause__ is not current else None
    return LlmFailure(message=": ".join(chain) or repr(error), code="UNKNOWN")


def _canonical_header(
    config: LlmCallConfig,
    adapter_defaults: dict[str, bool] | None = None,
    system: str = "",
    tools: list[Any] | None = None,
) -> dict[str, Any]:
    """One canonical request header snapshot (dsh ``canonicalHeader``)."""
    header: dict[str, Any] = {
        "config": {
            "provider": config.provider,
            "model": config.model,
            "reasoningEffort": config.reasoning_effort,
            "temperature": config.temperature,
            "maxTokens": config.max_tokens,
            "stop": list(config.stop) if config.stop else None,
        }
    }
    if adapter_defaults:
        header["adapterDefaults"] = dict(adapter_defaults)
    if system:
        header["system"] = system
    if tools:
        header["tools"] = [tool.name for tool in tools]
    return header


def _header_equals(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """dsh headerEquals: every field, including lists element-wise."""
    for key in ("config", "adapterDefaults", "system", "tools"):
        if a.get(key) != b.get(key):
            return False
    return True


__all__ = ["IdlePhase", "MaintenancePhase", "Phase", "ReactLoopAgent", "RunningPhase"]
