"""插件：agent 循环中间件（三个 waterfall）。

这就是"插件拥有循环"的故事（dsh）：driver 永远不写死行为，行为由
监听器组合出来——

- ``agent/request``（waterfall）：改写模型路由（``mock-mini`` →
  ``mock-mini-2026``），让 adapter 的精确模型解析跑在中间件的路由上；
- ``agent/pre-step``（waterfall）：给每个 step 的已认领消息追加一条
  上下文消息（默认逻辑已带 system-prompt 上下文，这里演示插件可以在
  step 边界改写消息集）；
- ``agent/request-error``（waterfall）：失败码为 ``TRANSIENT`` 时，
  每个 (turn, step) **重试一次**（retry 场景的恢复逻辑）。

waterfall 监听器契约（Cordis）：``listener(payload, next)``——调用
``next()`` 继续链路（最终落到内建默认行为）；不调用即截断（veto）。
"""

from dataclasses import replace

from javis.harness.types import (
    Events,
    PreStepEnter,
    PreStepReject,
    RetryAction,
    UserMessage,
)

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "middleware"

# 可重试的失败码白名单：只认 TRANSIENT（瞬时故障）；
# 4xx 这类配置/凭据错误重试没有意义，不在名单里。
RETRYABLE_CODES = frozenset({"TRANSIENT"})


def apply(ctx):
    # 跨调用的闭包状态：
    # - retried：已经重试过的 (turn, step)，保证每步至多重试一次；
    # - observed：观察日志（发布为 "middleware-observed" 服务，
    #   cli.py 的断言靠它证明"恢复确实走了 waterfall"）。
    retried: set[tuple[int, int]] = set()
    observed: list[str] = []

    # -- agent/request：改写路由 -------------------------------------------
    def on_request(payload, next):
        # 先走链路拿默认路由（seed），再按规则改写：
        # mock-mini → mock-mini-2026（演示"中间件决定最终路由"）。
        config = next()
        if config.model == "mock-mini":
            config = replace(config, model="mock-mini-2026")
        observed.append(f"request: route={config.provider}/{config.model} maxTokens={config.max_tokens}")
        return config

    # -- agent/pre-step：追加一条中间件上下文消息 ---------------------------
    def on_pre_step(payload, next):
        # 先走链路拿默认决策；若默认（或上游监听器）整步 reject，
        # 原样放行 reject，不做改写。
        decision = next()
        if isinstance(decision, PreStepReject):
            return decision
        extra = UserMessage.from_text(
            f"[middleware] turn {payload['turn']} step {payload['step']}: proceeding with {len(decision.messages)} message(s)"
        )
        observed.append(f"pre-step: +context (turn={payload['turn']} step={payload['step']})")
        return PreStepEnter(messages=tuple(list(decision.messages) + [extra]))

    # -- agent/request-error：接管恢复（每步重试一次） ----------------------
    def on_request_error(payload, next):
        # 先问链路：上游监听器若已给出恢复动作，原样放行；
        # 没有才由本插件按白名单决策。
        action = next()
        if action is not None:
            return action
        failure = payload["failure"]
        key = (payload["turn"], payload["step"])
        # 白名单内 + 本 (turn, step) 未重试过 → RetryAction（同一步重放）。
        if failure.code in RETRYABLE_CODES and key not in retried:
            retried.add(key)
            observed.append(f"request-error: retry (turn={payload['turn']} step={payload['step']} code={failure.code})")
            return RetryAction()
        # 其余情况不接管：循环按默认语义把失败升级为 turn 错误。
        observed.append(f"request-error: no recovery (turn={payload['turn']} step={payload['step']} code={failure.code})")
        return None

    # 三个 waterfall 监听器一次性挂上；
    # 观察日志发布为服务，供 cli.py 断言读取。
    ctx.on(Events.AGENT_REQUEST, on_request)
    ctx.on(Events.AGENT_PRE_STEP, on_pre_step)
    ctx.on(Events.AGENT_REQUEST_ERROR, on_request_error)
    ctx.provide("middleware-observed", observed)
