"""Plugin: agent-loop middleware over the three waterfalls.

This is the "plugins own the loop" story (dsh): the driver never changes,
and behavior is composed from listeners —

- ``agent/request`` (waterfall): rewrites the model route (``mock-mini`` →
  ``mock-mini-2026``) so the exact-model adapter resolution runs against the
  middleware's route.
- ``agent/pre-step`` (waterfall): appends one context message to every
  step's claimed messages (the default already carries the system-prompt
  context; this shows a plugin can rewrite the boundary).
- ``agent/request-error`` (waterfall): retries a step **once** per
  (turn, step) when the failure code is ``TRANSIENT`` (the retry scenario).

Waterfall listener contract (Cordis): ``listener(payload, next)`` — calling
``next()`` continues the chain (finally the built-in default); not calling
it vetoes the rest.
"""


from dataclasses import replace

from javis.dsh.contracts import (
    Events,
    PreStepEnter,
    PreStepReject,
    RetryAction,
    UserMessage,
)

name = "middleware"

RETRYABLE_CODES = frozenset({"TRANSIENT"})


def apply(ctx):
    retried: set[tuple[int, int]] = set()
    observed: list[str] = []

    # -- agent/request: rewrite the route -----------------------------------
    def on_request(payload, next):
        config = next()
        if config.model == "mock-mini":
            config = replace(config, model="mock-mini-2026")
        observed.append(f"request: route={config.provider}/{config.model} maxTokens={config.max_tokens}")
        return config

    # -- agent/pre-step: append a middleware context message -----------------
    def on_pre_step(payload, next):
        decision = next()
        if isinstance(decision, PreStepReject):
            return decision
        extra = UserMessage.from_text(
            f"[middleware] turn {payload['turn']} step {payload['step']}: proceeding with {len(decision.messages)} message(s)"
        )
        observed.append(f"pre-step: +context (turn={payload['turn']} step={payload['step']})")
        return PreStepEnter(messages=tuple(list(decision.messages) + [extra]))

    # -- agent/request-error: claim recovery (retry once per step) -----------
    def on_request_error(payload, next):
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

    ctx.on(Events.AGENT_REQUEST, on_request)
    ctx.on(Events.AGENT_PRE_STEP, on_pre_step)
    ctx.on(Events.AGENT_REQUEST_ERROR, on_request_error)
    ctx.provide("middleware-observed", observed)
