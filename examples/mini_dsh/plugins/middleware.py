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
    """装配：agent/request-error waterfall——TRANSIENT 每个 (turn, step) 重试一次。"""
    retried: set[tuple[int, int]] = set()
    observed: list[str] = []

    def on_request_error(payload, next):
        """瀑布监听器：无人认领且码为 TRANSIENT 且未重试过 → 返回 RetryAction。"""
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
