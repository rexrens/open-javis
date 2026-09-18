"""AgentLoop: the turn/step driver over queued input and step-boundary work.

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

# ---------------------------------------------------------------------------
# 中文阅读导览（译者注，配合上方英文 docstring 阅读；dsh = TypeScript 参考实现）
# ---------------------------------------------------------------------------
# 本文件是 dsh ``ReactLoopAgent`` 的 Python 仿写：一个「事件日志驱动 + 相位状态机」
# 的 agent 循环。抓住四件事就能读懂全篇：
#
# 1. 唯一真相源是 Session 事件日志（javis/harness/session.py），不是内存里的消息
#    列表。每次请求都由 ``session.derive_messages()`` 重新推导，循环只做「append
#    事件」这一种写操作，因此任何时刻都能从日志重放整段对话。
# 2. 模型/用户输入走 Inbox 两条队列（javis/harness/inbox.py）：``next-turn``
#    （followup，下一个 turn 开始时消费）与 ``next-step``（steer/inject，下一步
#    开始时消费）。每个边界都会清空 ``next-step``；turn 边界额外取**一条**
#    ``next-turn``（这就是空闲 steer 能被开轮第一步读到的原因）。消费动作叫
#    claim，固定发生在本文件的 _pre_step 边界上，且会被持久记录。
# 3. 相位机 IdlePhase / MaintenancePhase / RunningPhase 决定「谁可以动」：唤醒
#    输入要么立刻起一个 driver，要么 latch 成 phase.wake_requested 等当前活动
#    收敛；取消通过「每次活动一个 AbortController」传播。
# 4. 所有可扩展点都是 cordis 事件（名字见 types.Events）：emit（通知即忘）/
#    waterfall（中间件链，可改写或否决）/ serial（顺序询问，可 veto）。循环本身
#    不认识权限、压缩、模型路由——它们全都是挂在这些钩子上的中间件。
#
# 调用栈：Harness.submit_message → AgentLoop.followup → _wake_driver → _kick
#         → _turn（turn 边界）→ _pre_step（step 边界 + 钩子）→ _step（一次模型
#         往返 + 工具执行）→ _build_request（冻结一次请求）。

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, NoReturn

from .inbox import Inbox
from .session import Session
from .stream import BlockAssembler, assemble_finish, normalized_stream
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
    InboxTarget,
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
#
# 相位机回答「agent 现在允许做什么」，三者互斥：
#   - idle        ：无任何活动，可以立刻起 driver，也可以跑维护任务
#   - maintenance ：正在跑一次性维护任务（非 turn 逻辑），期间的唤醒被 latch
#   - running     ：driver 已占用，正在跑 turn/step
# 只有 running 允许进入 _turn/_step；维护任务与 turn 互斥（同一个 driver 槽位）。
# 相位切换统一走 _set_phase，因此 agent/status 事件天然去抖。
# ---------------------------------------------------------------------------


@dataclass
class IdlePhase:
    #: 相位判别标签：用 ``Literal`` 才能让类型检查器把 ``Phase`` 收窄成具体相位
    #: （对齐 dsh 的 tagged union；写成裸 ``str`` 的话所有收窄都会失效，
    #: ``self._phase.abort`` 这类访问会满屏报错）。
    kind: Literal["idle"] = "idle"
    #: 已完成的最后一个 turn 号（= 日志里 turn/start 的最大值）。空闲时把它带回
    #: 下一个 RunningPhase，保证进程重启 / session resume 后 turn 号仍然连续。
    last_turn: int = 0


@dataclass
class MaintenancePhase:
    kind: Literal["maintenance"] = "maintenance"
    #: 本次维护任务专属的取消信号（每次活动新建一个控制器，首个 cause 生效）。
    abort: AbortController = field(default_factory=AbortController)
    #: 同 IdlePhase.last_turn：维护不推进 turn 号，只负责把它原样带回去。
    last_turn: int = 0
    #: 维护期间到达的唤醒请求被 latch 在这里，等维护结束再补一次 driver——
    #: 不能让输入直接起 turn，否则会与维护任务抢同一个 driver 槽位。
    wake_requested: bool = False


@dataclass
class RunningPhase:
    kind: Literal["running"] = "running"
    #: 本次「活动」（一个 driver 直到收敛到 idle）的取消信号。一个 turn 结束后若
    #: 还有 pending 输入，_turn 会换一个全新的控制器再开下一个 turn——取消语义是
    #: 「每个活动一个信号」，而不是整个 agent 一个。
    abort: AbortController = field(default_factory=AbortController)
    #: 当前 turn 号（已 append turn/start 的那个）。
    turn: int = 0
    #: 本 turn 内已进入的 step 数；0 表示还没进入第一步。
    step: int = 0
    #: 运行中 latch 的唤醒标记。普通唤醒不需要它（当前 driver 自己会循环 _turn
    #: 把 pending 输入跑掉）；只有「取消之后到达的唤醒」才会 latch，等 aborted
    #: 活动收敛回 idle 后再起新 turn。
    wake_requested: bool = False


#: 三个相位互斥的联合类型。用 dataclass 而不是 Enum，是因为每相都要携带自己的
#: 状态（abort 控制器、turn/step 计数、latch 标记）。
Phase = IdlePhase | MaintenancePhase | RunningPhase


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AgentLoop:
    """Drives one session through turn and step boundaries (dsh ``Agent``)."""

    # 一个 AgentLoop 实例 = 一个 session 的驱动器。它不持有消息列表：全部对话事实
    # 都在 self.session 事件日志里（唯一真相源），self.inbox 只保存「尚未被消费的
    # 待办输入」。所以实例可以被丢弃重建（Harness._reset_session / clear），只要
    # 日志还在，状态就不会丢。

    def __init__(
        self,
        loop_ctx: Any,
        id: SessionId,
        options: AgentOptions,
        session: Session,
    ) -> None:
        self._ctx = loop_ctx  # 引擎（loop 侧）上下文：服务与事件都从这里取
        self.id = id  # SessionId，同时是 agent 身份
        self.options = options  # AgentOptions：声明的 provider/model 路由种子
        self.session = session  # append-only 事件日志（本类的唯一写出口）
        #: 当前「整体活动」（一次 driver 或一次维护任务）的收敛 Future。idle 时为
        #: None；running/maintenance 时非 None。when_idle() 就是等它。
        self._activity_done: asyncio.Future[None] | None = None
        # 从日志恢复 turn 计数：resume 之后新 turn 从 last_turn + 1 继续编号。
        last_turn = session.last_turn()
        self._phase: Phase = IdlePhase(last_turn=last_turn)
        # 构造 Inbox 时注入三个回调：队列的每次变更都转成 live 事件。
        # durable 的那一半（agent/inbox/spliced）由 Inbox 自己写进 session，
        # 因此「队列里当时有什么」也是可重放的。
        self.inbox = Inbox(
            session,
            inserted=lambda message: self._dispatch_emit(Events.AGENT_INBOX_INSERTED, {"message": message}),
            claimed=lambda message, turn: self._dispatch_emit(
                Events.AGENT_INBOX_CLAIMED, {"message": message, "turn": turn}
            ),
            discarded=lambda message: self._dispatch_emit(Events.AGENT_INBOX_DISCARDED, {"message": message}),
        )
        #: Agent-scoped context (dsh ``scope.ctx.extend({agent})``).
        # 所有事件 payload 都会带上 agent=self，监听器据此区分是哪个 agent。
        self.ctx = loop_ctx.extend({"agent": self})
        #: 每个 agent 只写一次「初始 request/header」事件；之后仅在 header 真的变化
        #: 时才落盘（去抖：否则每步都会刷一条无意义的事件）。
        self._request_header_logged = False

    # -- identity ------------------------------------------------------------

    @property
    def status(self) -> str:
        # 对外只有 idle / running 两种：maintenance 不是 turn 活动，因此也报 idle
        # （UI 不需要知道「正在跑维护任务」这个实现细节）。
        return "idle" if self._phase.kind in ("idle", "maintenance") else "running"

    @property
    def last_turn(self) -> int:
        # 直接从日志读，不靠内存镜像：这是「日志是唯一真相源」的一个体现。
        return self.session.last_turn()

    def _set_phase(self, next_phase: Phase) -> None:
        # 唯一入口：先算变更前的对外 status，再换相位。只有对外 status 真变了才
        # emit——idle→maintenance→idle 不会发两次 agent/status（去抖）。
        previous = self.status
        self._phase = next_phase
        if self.status != previous:
            self._dispatch_emit(Events.AGENT_STATUS, {"status": self.status})

    # -- event dispatch (dsh agentEvents) ------------------------------------

    # 三个封装都做同一件事：往 payload 里塞 agent=self，让监听器能区分 agent。
    # 至于走哪种分发模式（emit / serial / waterfall）由事件名和调用点决定。

    def _dispatch_emit(self, name: str, payload: dict[str, Any]) -> None:
        # emit：发射即忘，同步调用监听器，返回值被忽略（异步监听器被排成 task）。
        self._ctx.emit(name, {**payload, "agent": self})

    async def _dispatch_serial(self, name: str, payload: dict[str, Any]) -> Any:
        # serial：按注册顺序逐个 await，遇到 bail 值提前返回（用于 turn-stopping）。
        return await self._ctx.serial(name, {**payload, "agent": self})

    def _dispatch_waterfall(self, name: str, payload: dict[str, Any], default: Callable[..., Any]) -> Any:
        # waterfall：监听器签名是 (…dispatch 参数…, next)，调 next() 表示继续链，
        # 不调就是否决（veto）；default 是最内层默认行为（链尾）。
        return self._ctx.waterfall(name, {**payload, "agent": self}, default)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        # 所有扩展点（钩子 / 服务 / 监听器）都允许同步或异步实现，统一在这里归一。
        if inspect.isawaitable(value):
            return await value
        return value

    # -- input ---------------------------------------------------------------

    # 四种输入方式的区别只有两维：目标队列（next-turn / next-step）× 是否唤醒。
    #   followup → next-turn + 唤醒   （普通用户消息：开一个新 turn）
    #   steer    → next-step + 唤醒   （插话：当前 turn 的下一步执行）
    #   inject   → next-step + 不唤醒 （工具追加的上下文：空闲时只排队）

    def send(self, message: UserMessage, target: InboxTarget, wakeup: bool) -> None:
        """Route identified input to an inbox boundary and optionally wake the driver.

        Waking input submitted after active cancellation is queued for the next
        turn and runs when the aborted activity converges to idle.
        """
        # 关键重定向：若当前活动已被取消，而这条消息又要求唤醒，就把它归到
        # next-turn 排队——否则它会惊醒一个已经 abort 的活动（或者被当成当前
        # turn 的 steering 而在下一个 step 就被丢掉）。等活动收敛回 idle 后，
        # _kick 的 finally 会把它当成新的 turn 跑。
        phase = self._phase
        waking_after_abort = wakeup and phase.kind != "idle" and phase.abort.signal.aborted
        resolved: InboxTarget = "next-turn" if waking_after_abort else target
        # splice(start=队列长度, delete=0) 等价于 dsh 的 splice(target, Infinity,
        # 0, [message])：追加到队尾（保持等价的 FIFO 语义）。
        length = len(self.inbox.next_step if resolved == "next-step" else self.inbox.next_turn)
        self.inbox.splice(resolved, length, 0, [message])
        if wakeup:
            self._wake_driver(waking_after_abort)

    def followup(self, message: UserMessage) -> None:
        """Queue an ordinary follow-up turn and wake the driver."""
        self.send(message, "next-turn", True)

    def steer(self, message: UserMessage) -> None:
        """Queue steering input for the next step of the active turn."""
        # 当前 turn 尚未收尾时，_turn 的循环会因为 next-step 非空而继续下一步。
        self.send(message, "next-step", True)

    def inject(self, message: UserMessage) -> None:
        """Queue input for the next step without waking an idle driver."""
        # 不唤醒：agent 空闲时消息就静静排队，等下一次有人 followup/steer 时被捎带
        # 消费（工具通过 accept_context 追加的额外上下文就是这种）。
        self.send(message, "next-step", False)

    def cancel(self, cause: AgentCancelCause, keep_inbox: bool = False) -> None:
        """Abort the active activity; the first cause wins for that activity."""
        # 默认连排队输入一起丢弃（用户取消 = 这些输入也别跑了）；keep_inbox=True
        # 则保留队列（例如只是切换模型/停止当前回答）。
        if not keep_inbox:
            self.inbox.clear()
            if self._phase.kind != "idle":
                self._phase.wake_requested = False
        # "first cause wins"：AbortController 里先到的原因不会被后来的覆盖。
        # idle 时没有活动可取消，因此只清队列，不建控制器。
        if self._phase.kind != "idle":
            self._phase.abort.abort(cause)

    # -- lifecycle -----------------------------------------------------------

    def run_maintenance(self, job: Callable[[AbortSignal], Awaitable[Any]]) -> asyncio.Task[Any]:
        """Run one non-turn maintenance task from the true idle phase (dsh)."""
        # 只能从「真正的 idle」启动：running（已有 turn）或 maintenance（已有维护）
        # 都会抢同一个 driver 槽位，直接报错比静默排队更安全。
        if self._phase.kind != "idle":
            raise RuntimeError(f'agent "{self.id}" already has active work')
        # 继承 last_turn：维护任务不推进 turn 号，结束后原样带回 IdlePhase。
        maintenance = MaintenancePhase(last_turn=self._phase.last_turn)
        self._set_phase(maintenance)

        async def _run() -> Any:
            loop = asyncio.get_running_loop()
            done: asyncio.Future[Any] = loop.create_future()
            # 维护任务同样占用「活动」槽位：when_idle() 会等它结束。
            self._activity_done = done
            try:
                # 任务只拿到一个取消信号，不接触任何 turn/step 状态。
                result = await job(maintenance.abort.signal)
            except BaseException as error:
                # 异常同时传给等待者（done）并继续向上抛（task 上能看到）。
                if not done.done():
                    done.set_exception(error)
                raise
            else:
                if not done.done():
                    done.set_result(result)
            finally:
                # 无条件回 idle；维护期间 latch 的唤醒到这里补发（且要真有输入）。
                self._set_phase(IdlePhase(last_turn=maintenance.last_turn))
                if maintenance.wake_requested and self.inbox.has_pending:
                    self._wake_driver()
            return None

        return asyncio.ensure_future(_run())

    async def when_idle(self) -> None:
        """Resolve after the current whole-agent activity reaches quiescence."""
        # 循环两轮的原因：await 完旧活动后，可能已经有新活动被接管（latch 的唤醒
        # 或后续 followup），此时必须继续等。只有当「刚等完的活动仍是当前活动」
        # 时，agent 才是真正静止。兜底：_activity_done 为 None（比如 driver 异常
        # 未设置）时直接返回，避免死等。
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
            # 非 idle 不能起新 driver，只能决定「要不要记住这次唤醒」：
            #   - disposed 取消过的 agent 一律不 latch（已被拆除，不能再自我唤醒）；
            #   - 维护任务期间的唤醒一律 latch；
            #   - abort 之后到达的唤醒也 latch，等收敛回 idle 再开新 turn；
            #   - 运行中的普通唤醒不 latch（当前 driver 自己会循环 _turn 跑掉）。
            reason = phase.abort.signal.reason
            if (reason is None or reason.kind != "disposed") and (
                phase.kind == "maintenance" or wake_after_abort
            ):
                phase.wake_requested = True
            return
        loop = asyncio.get_running_loop()
        # driver Future 就是「本次活动」的收敛点：_kick 返回（或抛异常）时兑现。
        driver: asyncio.Future[None] = loop.create_future()
        self._activity_done = driver
        # 新相位从 last_turn 开始：step=0，且带一个全新的 abort 控制器。
        self._set_phase(RunningPhase(turn=phase.last_turn))

        async def _run() -> None:
            try:
                # 这里不 await：driver 在后台跑，调用方靠 when_idle() 等收敛。
                await self._kick()
            finally:
                if not driver.done():
                    driver.set_result(None)

        loop.create_task(_run())

    def _throw_error(self, error: BaseException) -> NoReturn:
        """Report one failure at its live boundary, then preserve it for containment."""
        # 关键：在「真实边界」上报错误——running 时用当前 turn/step，否则用
        # last_turn/step=0。先 emit 再 raise：emit 是通知（UI 能立刻看到），raise
        # 是把处置权交给上层（_kick 承载 / 调用方自己 catch）。
        if self._phase.kind == "running":
            turn, step = self._phase.turn, self._phase.step
        else:
            turn, step = self._phase.last_turn, 0
        self._dispatch_emit(Events.AGENT_ERROR, {"turn": turn, "step": step, "error": error})
        raise error

    async def _kick(self) -> None:
        # driver 主体：只要还有 pending 输入（_turn 返回 True）就再开一个 turn。
        try:
            while await self._turn():
                pass
        except asyncio.CancelledError:
            # asyncio 语义：取消必须原样上抛，不能被下面的兜底吃掉。
            raise
        except BaseException:  # noqa: BLE001 S110 — reported; contained at the driver boundary (dsh kick())
            # 故意吞掉：错误已在 _throw_error 走到真实边界并发过 agent/error，
            # 这里只做 containment——否则异常逃离 driver task，会变成 asyncio 的
            # "exception was never retrieved" 噪声，还会炸掉事件循环的任务。
            pass
        finally:
            # 无条件回 idle（turn 号取自当前相位），并补发运行期间 latch 的唤醒。
            if self._phase.kind == "running":
                turn = self._phase.turn
                wake = self._phase.wake_requested
                self._set_phase(IdlePhase(last_turn=turn))
                if wake and self.inbox.has_pending:
                    self._wake_driver()

    # -- turn ----------------------------------------------------------------

    # 方法名与职责（dsh kick → turn → step 三层）：
    #   _turn  : 开一个 turn，并在其中反复跑 step，直到本 turn 应该收尾；
    #            返回 True 表示「还有 pending 输入，请再调我一次」。
    #   _pre_step : 一个 step 的「进边界」阶段（claim 输入 + 组装上下文 + 钩子）。
    #   _step  : 一个 step 的「跑模型 + 跑工具」阶段。

    async def _turn(self) -> bool:
        """Open one turn before claiming its first proposed step."""
        # 不变式：只有 driver（_wake_driver）把相位置为 running 后才能进 turn。
        # 违反即编程错误，同样走 _throw_error 在真实边界上报。
        if self._phase.kind != "running":
            self._throw_error(RuntimeError(f'agent "{self.id}": turn without driver reservation'))
        phase: RunningPhase = self._phase
        signal = phase.abort.signal
        signal.throw_if_aborted()
        # turn 号 = 上一个 + 1（相位里的 turn 在进入时就已同步为 last_turn）。
        turn = phase.turn + 1
        try:
            # 先写日志再改内存：日志是真相源，写失败就不允许状态前进。
            self.session.append(SessionEvents.TURN_START, {"turn": turn})
        except BaseException as error:  # noqa: BLE001
            self._throw_error(error)
        phase.turn = turn
        #: 本 turn 的最终结束原因；None = 「还没定」（step 循环里逐步收敛）。
        turn_ends: TurnEndReason | None = None
        #: 消费目标：turn 第一步取 next-turn（followup），之后取 next-step（steer）。
        target: InboxTarget = "next-turn"
        try:
            while True:
                signal.throw_if_aborted()
                # javis 相对 dsh 的增补：turn 内 step 硬上限（dsh 循环无上限）。
                # 超限不发异常，而是温和结束本 turn：emit agent/limit 让 UI 能提示，
                # 然后按 TurnCompleted 收尾（同时丢弃本轮剩余 pending，返回 False）。
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
                # ← 进边界：在这里才 claim 输入（先看有没有输入、再决定要不要跑模型）。
                decision, assembly = await self._pre_step(target, turn, step)
                if decision.kind == "reject":
                    # 钩子拒绝进入这一步：turn 以 blocked 结束（不是错误）。
                    turn_ends = TurnBlocked()
                    return False
                # 已经决定收尾、且这一步又没有新消息 → 不再调模型，直接 break 去收尾。
                if turn_ends is not None and not decision.messages:
                    break
                # A removed waking message or an enter decision rewritten to
                # empty still owns the initial turn boundary, but it spends no model call.
                # 特例：turn 的第一步就空（唤醒消息被撤销 / enter 被改写成空）
                # —— 这个 turn 边界仍算占用（turn/start 已落盘），但不花模型调用。
                if phase.step == 0 and not decision.messages:
                    turn_ends = TurnCompleted()
                    return False
                signal.throw_if_aborted()
                # 先写 step/start，再推进 phase.step（同样是「先日志后内存」）。
                self.session.append(SessionEvents.STEP_START, {"turn": turn, "step": step})
                phase.step = step
                try:
                    # claim 到的消息必须落成 user/message：这才是模型看得到的输入。
                    # （每步注入的 context 消息已在 _pre_step 的默认行为里并进 messages）
                    for message in decision.messages:
                        self.session.append(SessionEvents.USER_MESSAGE, {"message": message})
                    step_end = await self._step(assembly, turn, step, signal)
                    # max-tokens is sticky: once any step hits the ceiling, later
                    # steps that complete normally must not downgrade the outcome.
                    # 粘性：一旦本 turn 某步撞到输出上限，后续正常完成的 step 不得
                    # 把它降级成 completed（否则调用方会误以为回答完整）。
                    if turn_ends is None or turn_ends.kind != "max-tokens":
                        turn_ends = step_end
                finally:
                    # step/end 必须与 step/start 配对——即使 _step 抛异常也要写。
                    self.session.append(SessionEvents.STEP_END, {"turn": turn, "step": step})
                signal.throw_if_aborted()
                # 收尾前问一次 serial 钩子（agent/turn-stopping），但仅在「本 turn 真的
                # 要结束且没有新的 next-step 输入」时问：还有 steering 就继续下一步，
                # 不该提前宣告 turn 结束。钩子抛 AbortError 也会在这里被捕获。
                if turn_ends is not None and not self.inbox.next_step:
                    await self._dispatch_serial(Events.AGENT_TURN_STOPPING, {"turn": turn, "signal": signal})
                    signal.throw_if_aborted()
                if turn_ends is not None and not self.inbox.next_step:
                    break
                target = "next-step"
        except asyncio.CancelledError:
            # asyncio 层取消（task.cancel()）原样上抛，且不改 turn_ends。
            # 注意：用户取消不走这条路径，而是 AbortError（见下）。
            raise
        except BaseException as error:
            if signal.aborted:
                # 真正的取消：把 cause 落成 turn 结束原因后仍上抛（让 _kick / 调用方
                # 感知），turn/end 由 finally 写入。
                # aborted ⇒ reason 必非 None（AbortSignal.abort 同时写这两个字段），
                # 断言既完成类型收窄，也把这个不变式写成可执行的文档。
                cause = signal.reason
                assert cause is not None
                turn_ends = TurnAborted(reason=cause)
                raise
            # 其他错误：拍平成 TurnError 作为结束原因，再交给 _throw_error 上报。
            turn_ends = TurnError(failure=_flatten_error(error))
            self._throw_error(error)
        finally:
            # 无论正常结束 / 阻塞 / 取消 / 报错，turn/end 一定写（reason 可为 None，
            # 例如 CancelledError 路径），保证日志中 turn 边界成对。
            self.session.append(SessionEvents.TURN_END, {"turn": turn, "reason": turn_ends})
        if not self.inbox.has_pending:
            return False
        # 还有 pending 输入 → 同一个 driver 接着开下一个 turn。换一个全新的 abort
        # 控制器（取消是「每个活动一个信号」），清掉 step 与 latch 标记。
        phase.abort = AbortController()
        phase.wake_requested = False
        phase.step = 0
        return True

    async def _pre_step(
        self, target: InboxTarget, turn: int, step: int
    ) -> tuple[PreStepDecision, PromptAssembly]:
        """Claim the boundary's messages, assemble context, propose the step."""
        # 不变式：进边界只能发生在 running 相位（与 _turn 同）。
        if self._phase.kind != "running":
            raise RuntimeError(f'agent "{self.id}": pre-step outside running phase')
        signal = self._phase.abort.signal
        # ① 消费边界：claim 清空全部 next-step；turn 首步（target=next-turn）额外
        # 取 1 条 next-turn（之后各步 target=next-step）。它同时写一条 durable 的
        # 纯删除 splice（认领不算取消）并 emit agent/inbox/claimed。
        # 这一步之后才叫「输入已被本轮接管」。
        claimed = self.inbox.claim(target, turn)
        # ② 组装提示词：persona 段（进 system 槽）+ context 段（进每步的 user 消息）
        # + 当下工具 schema。服务实现可以是同步或异步的。
        system_prompt = self._ctx.get("systemPrompt")
        assembly = system_prompt.assemble(agent=self, signal=signal)
        assembly = await self._maybe_await(assembly)
        signal.throw_if_aborted()
        context = self._context_message(assembly)

        # ③ waterfall 默认行为（链尾）：claim 到的消息 + 每步 context，进这一步。
        # 监听器可以：返回 PreStepReject 拒绝这一步，或改写 messages（例如追加
        # 提醒、去重、插入检索结果）。payload 里的 messages 是 tuple（不可变），
        # 默认行为自己 list() 复制一份再 append。
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
        # assembly 一并返回：_step 还要用它渲染 system prompt（同一步组装只做一次）。
        return decision, assembly

    def _context_message(self, assembly: PromptAssembly) -> UserMessage | None:
        """Render the assembly's context sections as one step-boundary message."""
        # context 段（cwd / workspace / session / 日期等）渲染成一条 user 消息，
        # 每步边界注入一次；为空则不注入，避免往日志里塞空消息。
        system_prompt = self._ctx.get("systemPrompt")
        text = system_prompt.render_context(assembly)
        if not text:
            return None
        return UserMessage.from_text(text)

    # -- step ----------------------------------------------------------------

    # 一个 step = 一次模型往返 + （可能的）一轮工具执行。
    # 返回值语义（很重要，调用方 _turn 靠它判断下一步）：
    #   None           → 本步跑完但 turn 未完，继续下一个 step（工具跑完且没结论）
    #   TurnCompleted  → 纯文本回复，或工具标记了 concludesTurn，turn 到此收尾
    #   TurnMaxTokens  → 撞上输出上限（粘性结果）
    # 异常路径：AbortError（取消）/ LlmError（模型失败且无人认领重试）都会上抛。

    async def _step(
        self, assembly: PromptAssembly, turn: int, step: int, signal: AbortSignal
    ) -> TurnEndReason | None:
        if self._phase.kind != "running":
            raise RuntimeError(f'agent "{self.id}": step outside running phase')
        # system prompt（persona 段）每个 step 渲染一次：中间件改过的 assembly 能
        # 立即生效；空字符串会被 _build_request 归一为 None 放进请求的 system 槽。
        system_prompt = self._ctx.get("systemPrompt")
        system = system_prompt.render_prompt(assembly)
        # while True 不是为了「重试整个 step」，而只是 agent/request-error 的恢复
        # 回路：监听器返回 RetryAction 时原地重跑同一个 (turn, step) 的模型请求。
        while True:
            request, prepared = await self._build_request(assembly, turn, step, system, signal)
            assembler = BlockAssembler()
            # 记录本步所有 chunk 事件的 seq，供 assistant/message 反向引用（可追溯）。
            chunk_seqs: list[int] = []
            llm = self._ctx.get("llm")
            # prepare_call 已解析到具体适配器时用它自己的 stream；否则回退到
            # llm 服务（例如中间件服务了一个未注册路由）。
            if prepared is not None and prepared.stream is not None:
                stream = prepared.stream(request)
            else:
                stream = llm.stream(request)
            try:
                # normalized_stream：把适配器抛出的任何异常归一成终止 finish chunk，
                # 让消费端总能看到一个「格式良好」的流（错误在下面统一处理）。
                async for chunk in normalized_stream(stream, request, signal):
                    signal.throw_if_aborted()
                    # 顺序很重要：先 append（落盘）再喂装配器——日志是真相源，
                    # 反过来的话崩溃时会丢掉「模型已经说过的话」。
                    seq = self.session.append(
                        SessionEvents.ASSISTANT_CHUNK, {"turn": turn, "step": step, "chunk": chunk}
                    ).seq
                    chunk_seqs.append(seq)
                    assembler.push(chunk)
            except AbortError:
                # 流中途被取消：把已装配的部分块落盘后原样上抛（turn 结束原因
                # 由 _turn 写成 TurnAborted）。
                self._append_interrupted(assembler, request)
                raise
            finish = assembler.finish
            if finish is None:
                # 适配器没给终止 finish 时兜底推断（有 tool-call 块 = tool-calls，
                # 否则 = stop），保证下面的分支总是明确的。
                finish = assemble_finish(assembler.blocks, assembler.usage)
            if isinstance(finish, AbortedFinish):
                # 终止原因是 aborted：先保留已经流给用户的部分块，再决定怎么抛。
                self._append_interrupted(assembler, request)
                cause = signal.reason
                if cause is None:
                    # 适配器自己报了 aborted：``javis/llm/runtime._failure_finish`` 在
                    # ``failure.code == "ABORTED"`` 时会返回 AbortedFinish，而此时我们
                    # 这边根本没有取消原因。当成普通失败上报，保住它的 failure 事实——
                    # 否则 AbortError(None) 会在构造时抛 AttributeError，把真正的失败
                    # 信息换成一句 "NoneType has no attribute 'kind'"。
                    raise LlmError(finish.failure.message, finish.failure.code, finish.failure)
                raise AbortError(cause)
            if isinstance(finish, ErrorFinish):
                # 模型/适配器失败：机会交给 agent/request-error waterfall。
                action = await self._request_error_action(turn, step, request, finish, prepared, signal)
                signal.throw_if_aborted()
                if not isinstance(action, RetryAction):
                    # 没人认领恢复 → 把结构化 failure 作为 LlmError 上抛。
                    raise LlmError(finish.failure.message, finish.failure.code, finish.failure)
                continue  # a listener owns recovery: retry the step

            # 成功：把装配好的助手消息落盘，并标注它来自哪个 provider/model，
            # usage 与 chunk 反引用一并写入（重放/计费/UI 都靠这三样）。
            message = AssistantMessage(
                content=tuple(assembler.blocks),
                source={"provider": request.provider, "model": request.model},
            )
            data: dict[str, Any] = {"turn": turn, "step": step, "message": message}
            if assembler.usage is not None:
                data["usage"] = assembler.usage
            self.session.append(SessionEvents.ASSISTANT_MESSAGE, data, sourceEventSeqs=tuple(chunk_seqs))
            if finish.kind == "max-tokens":
                # 撞上限：直接结束（_turn 里这个结果具有粘性）。
                return TurnMaxTokens()
            tool_calls = [block for block in assembler.blocks if isinstance(block, ToolCallBlock)]
            if not tool_calls:
                # 纯文本回复 = 本轮回答完成（无工具调用则不进入下一个 step）。
                return TurnCompleted()
            # 有工具调用：交给调度器（tools.py）执行 exclusive 屏障 / parallel 池。
            # 工具结果、以及被跳过调用的合成结果，全部由它自己落盘。
            # accept_context：工具声明的「额外上下文」被 splice 到 next-step 队尾，
            # 于是下一个 step 的 _pre_step 会 claim 到它（这就是工具反馈回循环的通道）。
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
            # concluded=True（工具要求收尾）→ completed；否则 None → 再跑一步。
            return TurnCompleted() if concluded else None

    def _append_interrupted(self, assembler: BlockAssembler, request: GenerateOptions) -> None:
        """Record the partially-assembled assistant message when interrupted (dsh)."""
        # 中断（用户取消 / 流异常）时，把已经装配到一半的块落盘成一条
        # interrupted=True 的助手消息——模型「已经说过的话」不能丢，否则重放出来的
        # 对话与用户当时看到的界面不一致（且会丢失已产生的 token）。
        # 一块都没装起来就不写（避免空消息污染日志）。
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

        # 默认返回 None = 「没有人认领恢复」，调用方据此抛 LlmError。
        # 监听器返回 RetryAction 就表示「我负责处理（通常自己也做了重试预算）」，
        # 循环会原地重跑同一个 step（step 号不推进，日志里 step/start 不重复）。
        def _default(_payload: dict[str, Any], _next: Any) -> Any:
            return None

        action = self._dispatch_waterfall(
            Events.AGENT_REQUEST_ERROR,
            {
                "turn": turn,
                "step": step,
                "provider": request.provider,
                "failure": finish.failure,  # 结构化失败事实（message / code / info）
                # 来自 prepare_call 的重试策略，监听器据此判断值不值得重试。
                "retryPolicy": prepared.retry_policy if prepared is not None else None,
                "signal": signal,
            },
            _default,
        )
        # 监听器同样允许同步/异步实现。
        return await self._maybe_await(action)

    # -- request -------------------------------------------------------------

    # 「冻结一次请求」：这里不发请求，只把所有决定项（路由 / 参数 / system / tools /
    # messages）确定下来并落盘变更日志。同一个请求只属于一个 step，不可变。

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
        # seed = 循环声明的路由（AgentOptions），作为 waterfall 的输入基线：
        # 「循环提供默认值，中间件负责改写」——这样循环不知道任何配置细节。
        seed = LlmCallConfig(
            provider=options.provider or "",
            model=options.model or "",
            max_tokens=options.max_tokens,
        )

        def _default(_payload: dict[str, Any], _next: Any) -> LlmCallConfig:
            return seed

        # agent/request waterfall：模型路由 / reasoning effort 在这里被改写
        # （例：Harness._request_middleware 让 set_model / set_effort 立即生效）。
        proposed = self._dispatch_waterfall(
            Events.AGENT_REQUEST,
            {"turn": turn, "step": step, "signal": signal},
            _default,
        )
        proposed = await self._maybe_await(proposed)
        signal.throw_if_aborted()
        if not proposed.provider or not proposed.model:
            # 路由不完整是配置错误，尽早上报（NO_ROUTE），不要带着空 model 去发请求。
            raise LlmError(
                f'agent "{self.id}" has no provider/model: set AgentOptions.provider and '
                "AgentOptions.model or supply both via the agent/request waterfall",
                "NO_ROUTE",
            )
        prepared: Any = None
        try:
            # prepare_call：解析适配器注册、重试策略、contextWindow 等运行时上下文；
            # 返回的 prepared 同时携带 config / stream / adapter_defaults。
            prepared = llm.prepare_call(proposed, signal)
            prepared = await self._maybe_await(prepared)
            config: LlmCallConfig = prepared.config
        except LlmError as error:
            # Middleware may serve an unregistered route; terminal dispatch
            # still requires an adapter.
            # 中间件可能自己服务一个未注册的路由（prepare 拿不到适配器），
            # 这种情况退回 proposed 继续构造请求；其他 LlmError 原样上抛。
            if error.code != "NO_ADAPTER":
                raise
            config = proposed
        signal.throw_if_aborted()

        # 请求头快照 + 变更日志（去抖）：
        #   首次  → reason=initial（或 resume：日志里已有旧 header，属会话恢复）
        #   之后  → 仅在真的变化时写一条 change
        # 这样「模型配置何时被改过」在日志里可精确重放，而不会每步刷一条。
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

        # /context 同理去抖（provider / model / 上下文窗口），供 UI 展示与压缩判断用。
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

        # 消息每次都从日志重新推导（derive_messages），再可选过一道压缩钩子
        # （javis 的 history_compressor，见 _compress_history）；最后冻结成 tuple，
        # 请求一旦构造完成就不再受后续状态变化影响。
        request = GenerateOptions(
            provider=config.provider,
            model=config.model,
            messages=tuple(self._compress_history(list(self.session.derive_messages()))),
            system=system or None,
            tools=tuple(assembly.tools) if assembly.tools else None,
            max_tokens=config.max_tokens,
            signal=signal,
        )
        # prepared 一并返回：_step 要用它的 stream 发请求。
        return request, prepared

    # -- javis extensions ----------------------------------------------------

    # 下面三个方法不是 dsh 端口的一部分，而是 javis 在循环里开的「配置读取缝」：
    # 循环不自带配置，而是每次从 agentLoop 服务上现读——因此 set_max_turns /
    # 热更新 / HMR 都能即时生效，无需重建 AgentLoop。

    def _loop_config(self) -> Any:
        """The ``agentLoop`` service's config object (dsh ``ctx.agentLoop.config``)."""
        # AgentLoopService 把配置放在 .config 上；但为了兼容「直接 provide 一个
        # 配置对象」的写法，拿不到 .config 时就把 service 自身当配置用。
        service = self._ctx.get("agentLoop")
        return getattr(service, "config", None) or service

    def _loop_max_steps(self) -> int:
        # javis 增补的 turn 内 step 上限。用 getattr 读属性（任何对象形状都能用），
        # 并夹紧到 >= 1：0 或负数会让 turn 永远走不出第一步。
        value = getattr(self._loop_config(), "max_steps_per_turn", 20)
        return max(1, int(value))

    def _compress_history(self, messages: list[Any]) -> list[Any]:
        """Apply the optional ``history_compressor`` after deriving messages,
        before the request is built (javis compression middleware slot)."""
        # javis 的压缩槽位：在 derive_messages() 之后、构造请求之前生效（默认
        # HistoryCompressor：保留最后 N 条，且不制造孤立的 tool 消息）。
        # 这里选择「读配置里的钩子」而不是发事件，是因为压缩必须发生在一个确定的
        # 位置（请求即将冻结的那一刻），而不是「谁愿意谁插进来」。
        compressor = getattr(self._loop_config(), "history_compressor", None)
        if compressor is None:
            return messages
        return list(compressor(messages))


# ---------------------------------------------------------------------------
# Helpers —— 循环之外的无状态纯函数（错误拍平 + 请求头快照）
# ---------------------------------------------------------------------------


def _flatten_error(error: BaseException) -> LlmFailure:
    """dsh errorChain: an LlmError keeps its facts; anything else flattens to text."""
    # dsh 的 errorChain：LlmError 已经携带结构化 failure（code / info），直接用；
    # 其他异常沿 __cause__ 链拍平成 "外层: 内层: 根因" 一条消息，相邻重复去重。
    if isinstance(error, LlmError):
        return error.failure
    chain: list[str] = []
    current: BaseException | None = error
    while current is not None:
        message = str(current)
        if not chain or chain[-1] != message:
            # 自引用时停下（current.__cause__ is current），避免死循环。
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
    # 可比较的请求头表示：只留「会影响模型行为」的字段（配置 + 适配器默认 +
    # system 文本 + 工具名列表），方便 request/header 事件做差异判定。
    # 注意工具只记名字：schema 的细微变化不在本快照语义内。
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
    # 逐字段比较；dict/list 用 == 递归比较（tools 是名字列表，顺序敏感）。
    # 缺字段按 None 处理：刚 resume 的日志可能没有 adapterDefaults。
    for key in ("config", "adapterDefaults", "system", "tools"):
        if a.get(key) != b.get(key):
            return False
    return True


__all__ = ["AgentLoop", "IdlePhase", "MaintenancePhase", "Phase", "RunningPhase"]
