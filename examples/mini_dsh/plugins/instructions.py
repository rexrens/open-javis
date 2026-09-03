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
