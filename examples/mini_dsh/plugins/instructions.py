"""插件：AGENTS.md/CLAUDE.md 指令注入（dsh agent-instructions 轻量版）。

- baseline：session 日志无 ``agent-instructions`` baseline 消息时，pre-step
  注入工作区指令全文（user 来源消息，source ``agent-instructions, baseline=true``）；
  判重扫描跳过被 compaction shadow 的 user/message 事件（同
  ``core/session.derive_messages`` 的 shadowedSeqs 读法）——否则 baseline 被
  compact 掉后从模型视野静默消失且不再重注入；
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


def _shadowed_seqs(session) -> set:
    """Compaction summary 里 shadowed 的事件 seq（同 derive_messages 读法）。"""
    shadowed: set = set()
    for event in session.events_of("compaction/summary"):
        shadowed.update((event.data or {}).get("shadowedSeqs", ()))
    return shadowed


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
    """装配：AGENTS.md/CLAUDE.md 指令注入的 pre-step 监听器。"""
    state = {"digest": None}

    def on_pre_step(payload, next):
        """baseline 注入 + 内容变更重注入（判重跳过 compaction shadowed）。"""
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
        shadowed = _shadowed_seqs(session)
        has_baseline = any(
            e.seq not in shadowed
            and (getattr((e.data or {}).get("message"), "source", None) or {}).get("kind")
            == "agent-instructions"
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
