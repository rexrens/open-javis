"""插件：provide "compaction" + tools/post-execute snip + pre-step 压力检查。

dsh ``ctx.compaction`` 服务 + ``compaction-tool-result-pruner`` + 自动压缩
（pressure）的 mini 组装：

- ``tools/post-execute`` waterfall 监听器：截断超限工具结果（snip）；
- ``agent/pre-step`` waterfall 监听器：压力检查——派生消息超阈则
  ``compact_if_needed(session, "pressure")``（在 reject 透传之后，追加在
  skill_tool/instructions 等既有 pre-step 监听器之后注册）。
"""
import sys
from pathlib import Path

from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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

    # 工具结果 snip（tools/post-execute waterfall；事件名对齐 core/tools.py）
    ctx.on(Events.TOOLS_POST_EXECUTE, make_snip_listener(max_chars=config.snipMaxChars))

    # pre-step 压力检查：派生消息超阈 → compact_if_needed("pressure")
    def on_pre_step(payload, next):
        decision = next()
        if getattr(decision, "kind", None) == "reject":
            return decision
        session = payload["agent"].session
        service.compact_if_needed(session, "pressure")
        return decision

    ctx.on(Events.AGENT_PRE_STEP, on_pre_step)
