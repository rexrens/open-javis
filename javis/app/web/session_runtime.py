"""Session registry and turn execution for the web host.

One dsh Session maps onto one javis ``RuntimeBundle``: the harness owns the
conversation, this module owns the dsh-visible event log, the live follow
subscribers, and the turn task that translates ``AgentEvent`` streams into dsh
Session events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from javis.app.runtime import RuntimeBundle, build_runtime
from javis.app.web import dsh_events as dsh
from javis.contracts.types import (
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolCallResult,
    AgentToolCallStart,
    AgentTurnEnd,
)
from javis.session.session_storage import JavisSessionBackend

log = logging.getLogger(__name__)

#: Follow window handed to a newly opened stream.
MAX_SNAPSHOT_RECORDS = 500


@dataclass
class SessionState:
    """One live dsh session and its dsh-visible event log."""

    session_id: str
    cwd: str
    created_at: int
    updated_at: int
    model: str
    summary: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)
    #: Cursor of the last appended event. dsh sequences are 0-based ("next seq
    #: is the log length"), so an empty log's cursor is -1 — the browser reads
    #: a 0 cursor as "one event already exists".
    seq: int = -1
    #: Wall-clock time of each stored message, in mirror order. Persisted inside
    #: the javis snapshot's ``tool_metadata`` so a resume can replay real
    #: durations instead of stamping the whole history with "now".
    message_times: list[int] = field(default_factory=list)
    turn: int = 0
    step: int = 0
    step_open: bool = False
    attempt_id: str | None = None
    attempt_revision: int = 0
    attempt_text: str = ""
    attempt_texts: list[str] = field(default_factory=list)
    attempt_times: list[int] = field(default_factory=list)
    attempt_reasoning: str = ""
    attempt_reasoning_texts: list[str] = field(default_factory=list)
    attempt_reasoning_times: list[int] = field(default_factory=list)
    #: Content-block slots the live frames claim. dsh places blocks by the
    #: chunk's `index`, so reasoning and text must never share one.
    attempt_reasoning_index: int | None = None
    attempt_text_index: int = 0
    attempt_frames: list[dict[str, Any]] = field(default_factory=list)
    attempt_index: int = 0
    attempt_started_after_seq: int = 0
    pending_calls: dict[str, list[str]] = field(default_factory=dict)
    last_error: str = ""
    running: bool = False
    task: asyncio.Task[None] | None = None
    bundle: RuntimeBundle | None = None
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)

    @property
    def blank(self) -> bool:
        """Whether no user turn has been recorded yet."""
        return self.turn == 0

    def summary_row(self) -> dict[str, Any]:
        """One ``session/list`` row."""
        return {
            "sessionId": self.session_id,
            "updatedAt": self.updated_at,
            "running": self.running,
            "blank": self.blank,
            "cwd": self.cwd,
            "projections": {
                "asOfSeq": self.seq,
                "values": {"title": self.summary or None},
            },
        }


class SessionNotFoundError(LookupError):
    """The addressed session is not live and has no stored snapshot."""


class SessionRuntime:
    """Owns every web session: creation, resume, turns, and subscriptions."""

    def __init__(
        self,
        *,
        cwd: str,
        workspace: str | Path | None = None,
        model: str | None = None,
        max_turns: int | None = None,
        plugins: str | Path | None = None,
    ) -> None:
        self.cwd = str(Path(cwd).resolve())
        self.workspace = workspace
        self.model = model
        self.max_turns = max_turns
        self.plugins = plugins
        self.sessions: dict[str, SessionState] = {}
        self.events_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.control_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.workspace_paths: list[str] = [self.cwd]
        self._load_workspaces()
        self._lock = asyncio.Lock()
        self._backend: JavisSessionBackend | None = None

    # -- workspaces -------------------------------------------------------

    def _workspaces_file(self) -> Path:
        root = Path(self.workspace) if self.workspace is not None else Path.home() / ".javis"
        return root / "web_workspaces.json"

    def _load_workspaces(self) -> None:
        """Restore registered workspaces, always keeping the javis cwd first."""
        try:
            raw = json.loads(self._workspaces_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        paths: list[str] = []
        if isinstance(raw, list):
            paths = [str(item) for item in raw if isinstance(item, str)]
        for candidate in [self.cwd, *paths]:
            if candidate and candidate not in self.workspace_paths:
                self.workspace_paths.append(candidate)

    def _save_workspaces(self) -> None:
        try:
            self._workspaces_file().parent.mkdir(parents=True, exist_ok=True)
            self._workspaces_file().write_text(
                json.dumps(self.workspace_paths, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            log.warning("could not persist the workspace list", exc_info=True)

    def workspace_rows(self) -> list[dict[str, Any]]:
        """Every registered workspace, with the sessions that run inside it."""
        rows: list[dict[str, Any]] = []
        sessions = self.list_rows()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for index, path in enumerate(self.workspace_paths):
            resolved = str(Path(path).expanduser())
            session_ids = [
                row["sessionId"] for row in sessions if str(row.get("cwd") or "") == resolved
            ]
            rows.append(
                {
                    "workspaceId": f"ws-{index}-{Path(resolved).name or resolved}",
                    "path": resolved,
                    "title": Path(resolved).name or resolved,
                    "sessionIds": session_ids,
                    "createdAt": stamp,
                    "updatedAt": stamp,
                }
            )
        return rows

    def workspace_path(self, workspace_id: str | None) -> str | None:
        """Resolve a workspace id back to its directory."""
        if not workspace_id:
            return None
        for row in self.workspace_rows():
            if row["workspaceId"] == workspace_id:
                return str(row["path"])
        return None

    def create_workspace(self, path: str) -> tuple[dict[str, Any], bool]:
        """Register one directory as a workspace; returns (row, created)."""
        target = Path(path).expanduser()
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        resolved = str(target.resolve())
        created = resolved not in self.workspace_paths
        if created:
            self.workspace_paths.append(resolved)
            self._save_workspaces()
        row = next(
            item for item in self.workspace_rows() if item["path"] == resolved
        )
        return row, created

    # -- storage ----------------------------------------------------------

    @property
    def backend(self) -> JavisSessionBackend:
        """Lazy session backend rooted in the javis workspace."""
        if self._backend is None:
            self._backend = JavisSessionBackend(self.workspace)
        return self._backend

    def stored_sessions(self) -> list[dict[str, Any]]:
        """Rows for stored javis snapshots, newest first."""
        try:
            return self.backend.list_snapshots(self.cwd, limit=50)
        except OSError:
            return []

    # -- lifecycle --------------------------------------------------------

    async def ensure_session(self, session_id: str) -> SessionState:
        """Return the live session, resuming a stored snapshot when needed."""
        existing = self.sessions.get(session_id)
        if existing is not None:
            return existing
        async with self._lock:
            existing = self.sessions.get(session_id)
            if existing is not None:
                return existing
            snapshot = self.backend.load_by_id(self.cwd, session_id)
            if snapshot is None:
                raise SessionNotFoundError(session_id)
            state = SessionState(
                session_id=session_id,
                cwd=str(snapshot.get("cwd") or self.cwd),
                created_at=int(float(snapshot.get("created_at") or 0) * 1000),
                updated_at=dsh.now_ms(),
                model=str(snapshot.get("model") or self.model or ""),
                summary=str(snapshot.get("summary") or ""),
            )
            state.bundle = await self._build_bundle(snapshot.get("messages") or [], session_id)
            stored_times = (snapshot.get("tool_metadata") or {}).get("web_message_times")
            if isinstance(stored_times, list):
                state.message_times = [
                    int(value)
                    for value in stored_times
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ]
            self._rebuild_records(state, snapshot.get("messages") or [])
            self.sessions[session_id] = state
            return state

    async def create_session(
        self, *, session_id: str | None = None, cwd: str | None = None
    ) -> SessionState:
        """Create one live session; javis mints the id when none is supplied."""
        bundle = await self._build_bundle([], session_id, cwd=cwd)
        state = SessionState(
            session_id=bundle.session_id,
            cwd=str(cwd or self.cwd),
            created_at=dsh.now_ms(),
            updated_at=dsh.now_ms(),
            model=bundle.engine.model or (self.model or ""),
        )
        state.bundle = bundle
        self.sessions[state.session_id] = state
        return state

    async def ensure_default_session(self) -> SessionState:
        """Return the session a blank browser conversation resolves to.

        The browser opens with no session selected, so the host owns the first
        one: the live session, else the newest stored snapshot, else a fresh
        session. Every blank-address request resolves here, which keeps the
        client's follow window and its prompt on the same identity.
        """
        if self.sessions:
            return max(self.sessions.values(), key=lambda state: state.updated_at)
        for stored in self.stored_sessions():
            session_id = str(stored.get("session_id") or "")
            if not session_id:
                continue
            try:
                return await self.ensure_session(session_id)
            except SessionNotFoundError:
                continue
        return await self.create_session()

    async def fork_session(
        self, source: SessionState, *, at_seq: int | None = None
    ) -> SessionState:
        """Create a new session seeded with the source's history.

        ``at_seq`` anchors the copy at the turn containing that event: dsh's
        fork keeps history through the selected completed turn, so the copied
        messages stop at that turn's last assistant reply.
        """
        messages = self._stored_messages(source.session_id)
        if at_seq is not None:
            messages = self._messages_through_turn(source, messages, at_seq)
        bundle = await self._build_bundle(messages, None, cwd=source.cwd)
        forked = SessionState(
            session_id=bundle.session_id,
            cwd=source.cwd,
            created_at=dsh.now_ms(),
            updated_at=dsh.now_ms(),
            model=bundle.engine.model or source.model,
            summary=source.summary,
        )
        forked.bundle = bundle
        forked.message_times = list(source.message_times)
        self.sessions[forked.session_id] = forked
        self._rebuild_records(forked, messages)
        self._persist(forked)
        return forked

    def _stored_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Raw javis messages for one session, live or stored."""
        state = self.sessions.get(session_id)
        bundle = state.bundle if state is not None else None
        if bundle is not None:
            return [
                message.model_dump(mode="json") for message in bundle.engine.messages
            ]
        snapshot = self.backend.load_by_id(self.cwd, session_id)
        raw = snapshot.get("messages") if snapshot else None
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _messages_through_turn(
        self, source: SessionState, messages: list[dict[str, Any]], at_seq: int
    ) -> list[dict[str, Any]]:
        """Cut the message list after the turn that contains ``at_seq``."""
        turns = [
            int(record["event"]["seq"])
            for record in source.records
            if record["event"]["type"] == "turn/start"
            and int(record["event"]["seq"]) <= at_seq
        ]
        if not turns:
            return []
        kept_turns = len(turns)
        human_indices = [
            index
            for index, message in enumerate(messages)
            if _is_human_turn(message)
        ]
        if kept_turns >= len(human_indices):
            return list(messages)
        end = human_indices[kept_turns]
        cut = list(messages[:end])
        while cut and not _has_text(cut[-1]):
            cut.pop()
        return cut

    async def _build_bundle(
        self,
        messages: list[dict[str, Any]],
        session_id: str | None,
        *,
        cwd: str | None = None,
    ) -> RuntimeBundle:
        bundle = await build_runtime(
            cwd=cwd or self.cwd,
            model=self.model,
            max_turns=self.max_turns,
            workspace=self.workspace,
            plugins=self.plugins,
            restore_messages=messages or None,
        )
        if session_id:
            bundle.session_id = session_id
        self._install_auto_permissions(bundle)
        return bundle

    @staticmethod
    def _install_auto_permissions(bundle: RuntimeBundle) -> None:
        """Answer the harness permission hook with allow for every tool call.

        v1 publishes a single Auto preset and has no approval card yet, so the
        host must not leave the harness's default deny in place: an allow-all
        checker is what makes the single published preset tell the truth.
        """

        async def allow_all(tool_name: str, arguments: dict[str, Any]) -> str:
            del tool_name, arguments
            return "allow"

        setter = getattr(bundle.engine, "set_permission_checker", None)
        if callable(setter):
            setter(allow_all)
            return
        agent = getattr(bundle.engine, "agent", None)
        if agent is not None and hasattr(agent, "permission_checker"):
            agent.permission_checker = allow_all

    def _rebuild_records(self, state: SessionState, messages: list[dict[str, Any]]) -> None:
        """Replay stored javis messages as the dsh turn/step scaffold.

        javis stores the message mirror: a human prompt, an assistant message
        that may carry ``tool_use`` blocks, and tool results as user-role
        messages. A faithful replay rebuilds what the live path emits — one step
        per model call, the tool calls that step advertised, and their results —
        and never mistakes a tool result for a new human turn.
        """
        turn = 0
        step = 0
        step_open = False
        open_turn = False
        # Recovered per-message times, aligned by index; a snapshot that predates
        # the timing journal falls back to the session's own creation instant
        # rather than pretending the history happened now.
        times = list(state.message_times)
        fallback = state.created_at or dsh.now_ms()
        consumed = 0
        last_time = fallback

        def next_time() -> int:
            nonlocal consumed, last_time
            value = times[consumed] if consumed < len(times) else fallback
            consumed += 1
            last_time = value
            return value

        def close_turn(*, answered: bool) -> None:
            nonlocal step_open, open_turn
            if not open_turn:
                return
            if step_open:
                self._append_at(state, "step/end", {"turn": turn, "step": step}, last_time)
                step_open = False
            self._append_at(
                state,
                "turn/end",
                {
                    "turn": turn,
                    "reason": {"kind": "completed" if answered else "interrupted"},
                },
                last_time,
            )
            open_turn = False

        for message in messages:
            message_time = next_time()
            raw = message.get("content")
            blocks = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            results = [item for item in blocks if item.get("type") == "tool_result"]
            visible = [item for item in blocks if item.get("type") != "tool_result"]
            role = message.get("role")
            if role == "user" and results and not _blocks_text(visible).strip():
                for block in results:
                    call_id = str(block.get("tool_use_id") or "")
                    data: dict[str, Any] = {
                        "turn": turn,
                        "step": step,
                        "message": dsh.tool_result_message_data(
                            call_id, _result_text(block), bool(block.get("is_error"))
                        ),
                    }
                    if block.get("is_error"):
                        data["error"] = dsh.tool_error_data()
                    self._append_at(state, "tool/result", data, message_time)
                continue
            if role == "user":
                close_turn(answered=step > 0)
                turn += 1
                step = 0
                self._append_at(state, "turn/start", {"turn": turn}, message_time)
                self._append_at(
                    state,
                    "user/message",
                    dsh.user_message_data(_blocks_text(visible)),
                    message_time,
                )
                open_turn = True
                continue
            if role == "assistant" and open_turn:
                if step_open:
                    self._append_at(
                        state, "step/end", {"turn": turn, "step": step}, message_time
                    )
                    step_open = False
                step += 1
                self._append_at(
                    state, "step/start", {"turn": turn, "step": step}, message_time
                )
                step_open = True
                text = _blocks_text(visible)
                self._append_at(
                    state,
                    "assistant/message",
                    dsh.assistant_message_data(
                        turn,
                        step,
                        text,
                        [],
                        state.model,
                        content=_wire_content(visible),
                    ),
                    message_time,
                )
                for block in visible:
                    if block.get("type") != "tool_use":
                        continue
                    tool_input = block.get("input")
                    self._append_at(
                        state,
                        "tool/call",
                        {
                            "turn": turn,
                            "step": step,
                            "callId": str(block.get("id") or ""),
                            "name": str(block.get("name") or ""),
                            "arguments": dsh.arguments_json(
                                tool_input if isinstance(tool_input, dict) else {}
                            ),
                        },
                        message_time,
                    )
        close_turn(answered=step > 0)
        state.turn = turn
        state.step = step
        state.step_open = False
        state.updated_at = dsh.now_ms()

    # -- event plumbing ---------------------------------------------------

    def _append(self, state: SessionState, event_type: str, data: dict[str, Any]) -> int:
        """Append one durable event and broadcast it to live followers."""
        return self._append_at(state, event_type, data, dsh.now_ms())

    def _append_at(
        self, state: SessionState, event_type: str, data: dict[str, Any], time_ms: int
    ) -> int:
        """Append one durable event pinned to an explicit wall-clock time."""
        state.seq += 1
        record = dsh.event_record(event_type, state.seq, data, time_ms=time_ms)
        state.records.append(record)
        state.updated_at = max(state.updated_at, time_ms)
        self._broadcast(state, record)
        return state.seq

    def _note_message_time(self, state: SessionState, time_ms: int) -> None:
        """Record the time of one message-producing event, in mirror order.

        The javis snapshot stores messages without timestamps, so this journal is
        the only way a later resume can replay real per-turn and per-tool
        durations.
        """
        state.message_times.append(time_ms)

    def _broadcast(self, state: SessionState, frame: dict[str, Any]) -> None:
        for queue in list(state.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

    def subscribe(self, state: SessionState) -> asyncio.Queue[dict[str, Any] | None]:
        """Register one live follower; closing the stream pushes None."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=4096)
        state.subscribers.add(queue)
        return queue

    def unsubscribe(self, state: SessionState, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        state.subscribers.discard(queue)

    def subscribe_events(self) -> asyncio.Queue[dict[str, Any]]:
        """Register one ``$events`` follower (forwarded host events)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4096)
        self.events_subscribers.add(queue)
        return queue

    def unsubscribe_events(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.events_subscribers.discard(queue)

    def subscribe_control(self) -> asyncio.Queue[dict[str, Any]]:
        """Register one ``session/control`` follower (jobs and projections)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        self.control_subscribers.add(queue)
        return queue

    def unsubscribe_control(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.control_subscribers.discard(queue)

    def control_baseline(self) -> dict[str, Any]:
        """The opening ``session/control`` baseline: one entry per live session."""
        return {
            "jobs": {},
            "projections": {
                state.session_id: {
                    # `asOfSeq` is a SessionSeq (non-negative); only the follow
                    # cursor uses -1 for "empty log".
                    "asOfSeq": max(state.seq, 0),
                    "values": self.projection_values(state),
                }
                for state in self.sessions.values()
            },
        }

    def projection_values(self, state: SessionState) -> dict[str, Any]:
        """Session projections the client reads.

        v1 publishes the model selection and the session title — the sidebar
        falls back to the workspace name for any session without a title.
        """
        selection = {
            "provider": dsh.PROVIDER,
            "model": state.model or self.current_model(),
        }
        return {
            "modelSelection": {"lastUsed": selection, "next": selection},
            "title": state.summary or None,
        }

    def emit_model_selection(self, state: SessionState) -> None:
        """Push one ``modelSelection`` projection update to control followers."""
        self._emit_projection(state, "modelSelection", self.projection_values(state)["modelSelection"])

    def _emit_projection(self, state: SessionState, key: str, value: Any) -> None:
        """Push one projection update to every control follower."""
        frame = {
            "type": "projection",
            "sessionId": state.session_id,
            "key": key,
            "value": value,
            # A projection watermark is a SessionSeq too: an empty log's -1 cursor
            # is not a legal durable position.
            "seq": max(state.seq, 0),
        }
        for queue in list(self.control_subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)

    def _learn_title(self, state: SessionState, text: str) -> None:
        """Derive the session title from the first human prompt, dsh-style."""
        if state.summary:
            return
        summary = text.strip().splitlines()[0][:80] if text.strip() else ""
        if not summary:
            return
        state.summary = summary
        self._emit_projection(state, "title", summary)

    def _emit_event(self, event: str, args: list[Any]) -> None:
        """Forward one host event to every ``$events`` listener."""
        for queue in list(self.events_subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait({"event": event, "args": args})

    def current_model(self) -> str:
        """The model reported as the catalog default."""
        if self.model:
            return self.model
        if self.sessions:
            state = max(self.sessions.values(), key=lambda candidate: candidate.updated_at)
            if state.model:
                return state.model
            if state.bundle is not None and state.bundle.engine.model:
                return state.bundle.engine.model
        return "deepseek-chat"

    def snapshot_frame(self, state: SessionState, *, assistant_stream: bool = False) -> dict[str, Any]:
        """The opening frame of one follow generation.

        ``assistant_stream`` mirrors the request's opt-in: the browser journal
        rejects an opening that omits the baseline it asked for, so an opted-in
        snapshot always carries one. A stream that opens mid-attempt restarts
        that attempt on a fresh revision instead of handing over an
        ``activeAttempt`` baseline, which the client abandons on sight.
        """
        records = state.records[-MAX_SNAPSHOT_RECORDS:]
        frame: dict[str, Any] = {
            "type": "snapshot",
            "header": {
                "version": dsh.SESSION_FORMAT_VERSION,
                "id": state.session_id,
                "createdAt": state.created_at,
                "cwd": state.cwd,
                "isSeeded": False,
            },
            "cursor": state.seq,
            "records": records,
            "hasMore": len(state.records) > len(records),
            "projections": {
                "asOfSeq": max(state.seq, 0),
                "values": self.projection_values(state),
            },
        }
        if assistant_stream or state.attempt_id is not None:
            # Announce the revision the *next* live frame will carry: an idle
            # session is about to start attempt `attempt_revision + 1`, and a
            # baseline whose revision does not match that start frame reads as a
            # revision gap, which makes the client abandon the generation.
            frame["assistantStream"] = {
                "revision": (
                    state.attempt_revision
                    if state.attempt_id is not None
                    else state.attempt_revision + 1
                )
            }
        return frame

    # -- turns ------------------------------------------------------------

    async def prompt(
        self,
        state: SessionState,
        text: str,
        *,
        request_id: str | None = None,
    ) -> None:
        """Accept one prompt and run its turn on a background task."""
        if state.task is not None and not state.task.done():
            raise RuntimeError("session is already running")
        state.running = True
        self._broadcast_status(state)
        self._learn_title(state, text)
        state.task = asyncio.create_task(self._run_turn(state, text, request_id=request_id))

    async def cancel(self, state: SessionState) -> bool:
        """Request cancellation of the live turn."""
        task = state.task
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    def _broadcast_status(self, state: SessionState) -> None:
        """Forward the session running flag to ``$events`` listeners."""
        self._emit_event("api-session/status", [state.session_id, state.running])

    async def _run_turn(
        self, state: SessionState, text: str, *, request_id: str | None = None
    ) -> None:
        """Translate one javis turn into the dsh event sequence."""
        bundle = state.bundle
        if bundle is None:
            state.running = False
            self._broadcast_status(state)
            return
        turn = state.turn + 1
        state.step = 0
        state.step_open = False
        self._append(state, "turn/start", {"turn": turn})
        prompt_time = dsh.now_ms()
        self._append_at(
            state,
            "user/message",
            dsh.user_message_data(text, rpc_id=request_id),
            prompt_time,
        )
        self._note_message_time(state, prompt_time)
        state.turn = turn
        reason = "completed"
        error_message = ""
        try:
            async for event in bundle.engine.submit_message(text):
                self._consume(state, turn, event)
        except asyncio.CancelledError:
            reason = "aborted"
        except Exception as exc:
            log.exception("web turn failed")
            reason = "error"
            error_message = str(exc)
        else:
            if state.last_error:
                reason = "error"
                error_message = state.last_error
        finally:
            state.last_error = ""
            self._settle_attempt(state, turn, abandoned=reason == "aborted")
            self._drain_pending_calls(state, turn, reason)
            self._close_step(state, turn)
            self._append(
                state,
                "turn/end",
                {"turn": turn, "reason": dsh.turn_end_reason(reason, message=error_message)},
            )
            state.running = False
            self._broadcast_status(state)
            self._persist(state)

    def _drain_pending_calls(self, state: SessionState, turn: int, reason: str) -> None:
        """Close every advertised tool call that never produced a result.

        dsh rejects a ``step/end`` or ``turn/end`` that leaves an unresolved
        tool call, so an aborted or failed turn records a synthetic failure
        result for each call still in flight.
        """
        pending = [call_id for call_ids in state.pending_calls.values() for call_id in call_ids]
        state.pending_calls.clear()
        for call_id in pending:
            output = (
                "Tool call was cancelled."
                if reason == "aborted"
                else "Tool call did not finish before the turn ended."
            )
            data: dict[str, Any] = {
                "turn": turn,
                "step": state.step,
                "message": dsh.tool_result_message_data(call_id, output, True),
                "error": dsh.tool_error_data(),
            }
            result_time = dsh.now_ms()
            self._append_at(state, "tool/result", data, result_time)
            self._note_message_time(state, result_time)

    def _persist(self, state: SessionState) -> None:
        """Write the javis session snapshot the sidebar and resume read.

        The TUI saves through ``runtime.handle_line``; the web host owns its own
        turns, so it must save here or a web session disappears on restart.
        """
        bundle = state.bundle
        if bundle is None:
            return
        try:
            bundle.session_backend.save_snapshot(
                cwd=bundle.cwd,
                model=bundle.engine.model,
                system_prompt=bundle.engine.system_prompt,
                messages=bundle.engine.messages,
                usage=bundle.engine.total_usage,
                session_id=state.session_id,
                tool_metadata={
                    **bundle.engine.tool_metadata,
                    "web_message_times": list(state.message_times),
                },
            )
        except OSError:
            log.warning("could not persist session %s", state.session_id, exc_info=True)

    def _open_step(self, state: SessionState, turn: int) -> None:
        state.step += 1
        state.step_open = True
        self._append(state, "step/start", {"turn": turn, "step": state.step})

    def _close_step(self, state: SessionState, turn: int) -> None:
        if not state.step_open:
            return
        self._append(state, "step/end", {"turn": turn, "step": state.step})
        state.step_open = False

    def _consume(self, state: SessionState, turn: int, event: AgentEvent) -> None:
        if isinstance(event, AgentTextDelta):
            self._open_attempt(state, turn)
            self._push_chunk(state, turn, dsh.text_delta_chunk(state.attempt_text_index, event.text))
            state.attempt_text += event.text
            state.attempt_texts.append(event.text)
            state.attempt_times.append(dsh.now_ms())
            return
        if isinstance(event, AgentReasoningDelta):
            self._open_attempt(state, turn)
            if state.attempt_reasoning_index is None:
                state.attempt_reasoning_index = 0
                # Reasoning occupies block 0, so visible text moves to block 1.
                state.attempt_text_index = 1
            self._push_chunk(
                state,
                turn,
                dsh.reasoning_delta_chunk(state.attempt_reasoning_index, event.text),
            )
            state.attempt_reasoning += event.text
            state.attempt_reasoning_texts.append(event.text)
            state.attempt_reasoning_times.append(dsh.now_ms())
            return
        if isinstance(event, AgentToolCallStart):
            call_id = f"call_{dsh.seq_id()}"
            arguments = dsh.arguments_json(event.tool_input)
            # dsh only admits a tool/call that an assistant message advertised,
            # so the model call that requested this tool settles here carrying
            # its tool-call block — even when the model wrote no text at all.
            self._open_attempt(state, turn)
            self._settle_attempt(
                state,
                turn,
                extra_content=[
                    {
                        "type": "tool-call",
                        "id": call_id,
                        "name": event.tool_name,
                        "arguments": arguments,
                    }
                ],
            )
            if not state.step_open:
                self._open_step(state, turn)
            state.pending_calls.setdefault(event.tool_name, []).append(call_id)
            self._append(
                state,
                "tool/call",
                {
                    "turn": turn,
                    "step": state.step,
                    "callId": call_id,
                    "name": event.tool_name,
                    "arguments": arguments,
                },
            )
            return
        if isinstance(event, AgentToolCallResult):
            calls = state.pending_calls.get(event.tool_name) or []
            call_id = calls.pop(0) if calls else f"call_{dsh.seq_id()}"
            data: dict[str, Any] = {
                "turn": turn,
                "step": state.step,
                "message": dsh.tool_result_message_data(call_id, event.output, event.is_error),
            }
            if event.is_error:
                data["error"] = dsh.tool_error_data()
            result_time = dsh.now_ms()
            self._append_at(state, "tool/result", data, result_time)
            self._note_message_time(state, result_time)
            return
        if isinstance(event, AgentError):
            state.last_error = event.message
            return
        if isinstance(event, AgentTurnEnd):
            if event.text and not state.attempt_text:
                self._open_attempt(state, turn)
                self._push_chunk(
                    state, turn, dsh.text_delta_chunk(state.attempt_text_index, event.text)
                )
                state.attempt_text = event.text
                state.attempt_texts.append(event.text)
                state.attempt_times.append(dsh.now_ms())
            self._settle_attempt(state, turn, usage=dsh.token_usage_data(event.usage))
            return

    def _open_attempt(self, state: SessionState, turn: int) -> None:
        if state.attempt_id is not None:
            return
        # One step is one model call plus the tools it requested, so a new model
        # call closes the previous step before this one opens.
        if state.step_open:
            self._close_step(state, turn)
        self._open_step(state, turn)
        state.attempt_revision += 1
        state.attempt_id = f"att_{dsh.seq_id()}"
        state.attempt_text = ""
        state.attempt_texts = []
        state.attempt_times = []
        state.attempt_frames = []
        state.attempt_index = 0
        state.attempt_started_after_seq = state.seq
        self._broadcast(
            state,
            {
                "type": "assistant-stream",
                "frame": {
                    "type": "start",
                    "attemptId": state.attempt_id,
                    "revision": state.attempt_revision,
                    "startedAfterSeq": state.attempt_started_after_seq,
                    "turn": turn,
                    "step": state.step,
                },
            },
        )

    def _push_chunk(self, state: SessionState, turn: int, chunk: dict[str, Any]) -> None:
        if state.attempt_id is None:
            return
        del turn
        timestamp = dsh.now_ms()
        frame = {
            "type": "assistant-stream",
            "frame": {
                "type": "chunk",
                "attemptId": state.attempt_id,
                "revision": state.attempt_revision,
                "index": state.attempt_index,
                "time": timestamp,
                "chunk": chunk,
            },
        }
        state.attempt_index += 1
        # The opening baseline carries *records*, not raw chunks: the client
        # expands it with the same reader that validates durable streams, where a
        # raw chunk must be wrapped as {type:'chunk', time, chunk}.
        state.attempt_frames.append({"type": "chunk", "time": timestamp, "chunk": chunk})
        self._broadcast(state, frame)

    def _settle_attempt(
        self,
        state: SessionState,
        turn: int,
        *,
        abandoned: bool = False,
        extra_content: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Settle the open model attempt into one durable assistant message.

        ``extra_content`` carries the tool-call blocks the model requested: dsh
        only admits a ``tool/call`` that an assistant message advertised, so the
        settlement must include them even when the model wrote no text.
        """
        attempt_id = state.attempt_id
        if attempt_id is None:
            return
        text = state.attempt_text
        reasoning = state.attempt_reasoning
        reasoning_index = state.attempt_reasoning_index
        content_blocks = (
            ([{"type": "reasoning", "text": reasoning}] if reasoning else [])
            + ([{"type": "text", "text": text}] if text else [])
            + list(extra_content or [])
        )
        stream: list[dict[str, Any]] = []
        if text or content_blocks:
            if reasoning and reasoning_index is not None:
                stream.append(
                    dsh.reasoning_chunks_record(
                        dsh.now_ms(),
                        state.attempt_reasoning_times,
                        state.attempt_reasoning_texts,
                        index=reasoning_index,
                    )
                )
            if text:
                stream.append(
                    dsh.text_chunks_record(
                        dsh.now_ms(),
                        state.attempt_times,
                        state.attempt_texts,
                        index=state.attempt_text_index,
                    )
                )
            settle_time = dsh.now_ms()
            data = dsh.assistant_message_data(
                turn,
                state.step,
                text,
                stream,
                state.bundle.engine.model if state.bundle else "",
                interrupted=abandoned,
                content=(content_blocks or None),
                usage=usage,
            )
            seq = self._append_at(state, "assistant/message", data, settle_time)
            self._note_message_time(state, settle_time)
            outcome: dict[str, Any] = {
                "kind": "committed",
                "eventType": "assistant/message",
                "seq": seq,
            }
        else:
            outcome = {"kind": "abandoned"}
        self._broadcast(
            state,
            {
                "type": "assistant-stream",
                "frame": {
                    "type": "end",
                    "attemptId": attempt_id,
                    "revision": state.attempt_revision,
                    "index": state.attempt_index,
                    "outcome": outcome,
                },
            },
        )
        state.attempt_id = None
        state.attempt_text = ""
        state.attempt_texts = []
        state.attempt_times = []
        state.attempt_reasoning = ""
        state.attempt_reasoning_texts = []
        state.attempt_reasoning_times = []
        state.attempt_reasoning_index = None
        state.attempt_text_index = 0
        state.attempt_frames = []
        state.attempt_index = 0

    # -- replies ----------------------------------------------------------

    def list_rows(self) -> list[dict[str, Any]]:
        """Merge live sessions with stored snapshots into ``session/list`` rows."""
        rows: dict[str, dict[str, Any]] = {}
        for stored in self.stored_sessions():
            session_id = str(stored.get("session_id") or "")
            if not session_id:
                continue
            summary = str(stored.get("summary") or "")
            rows[session_id] = {
                "sessionId": session_id,
                "updatedAt": int(float(stored.get("created_at") or 0) * 1000),
                "running": False,
                "blank": int(stored.get("message_count") or 0) == 0,
                "cwd": self.cwd,
                "projections": {"asOfSeq": 0, "values": {"title": summary or None}},
            }
        for state in self.sessions.values():
            rows[state.session_id] = state.summary_row()
        return sorted(rows.values(), key=lambda row: int(row["updatedAt"]), reverse=True)

    async def aclose(self) -> None:
        """Cancel live turns and dispose harness fibers."""
        for state in list(self.sessions.values()):
            if state.task is not None and not state.task.done():
                state.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await state.task
            if state.bundle is not None:
                await state.bundle.close()
        self.sessions.clear()


def _message_text(message: dict[str, Any]) -> str:
    """Extract the plain text of one stored javis message."""
    parts = message.get("content")
    if isinstance(parts, list):
        texts = [
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        if texts:
            return "".join(texts)
    text = message.get("text")
    return str(text) if isinstance(text, str) else ""


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    """Join the text of one stored content-block list."""
    return "".join(
        str(block.get("text", ""))
        for block in blocks
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )


def _is_human_turn(message: dict[str, Any]) -> bool:
    """Whether one stored message opens a human turn (not a tool result)."""
    if message.get("role") != "user":
        return False
    raw = message.get("content")
    blocks = raw if isinstance(raw, list) else []
    results = [item for item in blocks if isinstance(item, dict) and item.get("type") == "tool_result"]
    visible = [item for item in blocks if isinstance(item, dict) and item.get("type") != "tool_result"]
    return not (results and not _blocks_text([item for item in visible if isinstance(item, dict)]).strip())


def _has_text(message: dict[str, Any]) -> bool:
    """Whether one stored message carries model-visible text."""
    raw = message.get("content")
    blocks = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return _blocks_text(blocks).strip() != "" or any(
        block.get("type") == "tool_use" for block in blocks
    )


def _result_text(block: dict[str, Any]) -> str:
    """Read the model-facing text of one stored tool-result block."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )
    return str(content or "")


def _wire_content(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project stored javis blocks onto dsh wire content blocks."""
    wire: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text" and str(block.get("text", "")) != "":
            wire.append({"type": "text", "text": str(block["text"])})
        elif kind == "tool_use":
            tool_input = block.get("input")
            wire.append(
                {
                    "type": "tool-call",
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": dsh.arguments_json(
                        tool_input if isinstance(tool_input, dict) else {}
                    ),
                }
            )
        elif kind == "image":
            # Inline base64 is not a wire image block (those carry attachment ids).
            wire.append({"type": "text", "text": "[image attachment]"})
    return wire


def replay_should_skip(
    frame: Any,
    *,
    cursor: int,
    revision: int | None,
    next_index: int,
) -> bool:
    """Whether a frame queued during the snapshot handoff is already covered.

    A follow generation subscribes *before* it renders its opening snapshot, so
    events that land in between arrive twice. Durable records at or below the
    snapshot cursor are duplicates, and transient chunks the baseline already
    carries would look like a dense-index regression.
    """
    if not isinstance(frame, dict):
        return False
    if frame.get("type") == "event":
        event = frame.get("event")
        if isinstance(event, dict) and isinstance(event.get("seq"), int):
            return int(event["seq"]) <= cursor
        return False
    if frame.get("type") == "assistant-stream":
        inner = frame.get("frame")
        if not isinstance(inner, dict):
            return False
        if revision is not None and inner.get("revision") != revision:
            return False
        index = inner.get("index")
        if isinstance(index, int) and inner.get("type") == "chunk":
            return index < next_index
    return False
