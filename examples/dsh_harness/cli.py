#!/usr/bin/env python
"""通过 Cordis 插件系统运行 harness demo 的各场景。

本 demo（``examples/dsh_harness``）是一个 dsh 风格的 agent 循环——相位状态机、
inbox（收件箱）、会话事件日志、独占/并行工具调度、agent/* waterfall——完全由
Cordis 插件组合而成（``examples/dsh_harness/cordis.yml``）。循环本体是共享的
``javis.harness`` 架构层（与生产环境 ``javis.harness`` 引擎跑在同一份源码上）。
本入口做三件事：

1. 启动根 Context，把组合文件挂到 ``Loader`` 上（依赖驱动的加载顺序、
   fiber 生命周期、可回滚的 effect）；
2. 通过公开 agent API 驱动一个场景（``followup`` / ``steer`` / ``when_idle``）；
3. 打印实时转录（observer 插件）+ 持久化会话日志，并核对场景预期。

用法（从仓库根目录，``javis`` 可导入）::

    uv run python examples/dsh_harness/cli.py                 # 跑全部四个场景
    uv run python examples/dsh_harness/cli.py --scenario tools
    uv run python examples/dsh_harness/cli.py --scenario steer --verbose

mock provider 是脚本化的（``mock_llm.py``），不需要 API key。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

DEMO_ROOT = Path(__file__).resolve().parent
# demo 目录必须可导入：组合文件从这里加载插件，而插件（以及本文件）会
# import 同目录的兄弟模块（mock_llm、plugins.* 等）。
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from javis.cordis import Context, FiberState
from javis.cordis.loader import Loader
from javis.cordis.registry import settle
from javis.harness.types import UserMessage

COMPOSITION = DEMO_ROOT / "cordis.yml"
# 四个脚本化场景，各验证一项 harness 行为：
#   text  —— 纯生成（循环基线，不调工具）
#   tools —— 工具调度（独占屏障 + 并行对）
#   retry —— agent/request-error 恢复（脚本化瞬时失败）
#   steer —— 轮中收件箱注入（step 边界处认领 steering 消息）
SCENARIOS = ("text", "tools", "retry", "steer")

# 每个场景一条 prompt，措辞上配合被测行为（如 retry 场景提前说明
# "provider 今天不稳定"，让脚本化失败读起来顺理成章；steer 场景的提问
# 恰好能被轮中注入的 steering 消息合理纠正）。
PROMPTS = {
    "text": "What is 2+2?",
    "tools": "Compare the weather in Paris and Tokyo.",
    "retry": "Say hello (the provider is flaky today).",
    "steer": "What time is it?",
}


# ---------------------------------------------------------------------------
# 组合引导
# ---------------------------------------------------------------------------


async def compose(scenario: str) -> tuple[Context, Any, Any]:
    """挂载组合文件并等待 settle，返回 (ctx, agent, session)。

    场景名在插件运行前通过环境变量导出：脚本化 mock adapter 和 demo
    middleware 都会读 ``HARNESS_DEMO_SCENARIO`` 来选择本场景的行为
    （回放哪套脚本、是否注入 retry 失败）。
    """
    os.environ["HARNESS_DEMO_SCENARIO"] = scenario
    ctx = Context()
    # 插件按相对路径引用资源时的基准目录。
    ctx.baseUrl = str(COMPOSITION.parent)
    # 把 cordis.yml 组合挂到 Loader 上；await loader fiber 等待首次加载完成。
    loader_fiber = ctx.plugin(Loader, {"file": str(COMPOSITION)})
    await loader_fiber
    # 依赖驱动链（inject / provide）可能还在唤醒——settle 到没有待办插件为止。
    await settle(ctx)
    # FAILED fiber 意味着配置/apply 出错：报告并终止——在损坏的组合上
    # 继续跑场景，只会得到更难排查的断言失败。
    failed = [
        fiber
        for runtime in ctx.registry.values()
        for fiber in list(runtime.fibers)
        if fiber.state == FiberState.FAILED
    ]
    if failed:
        for fiber in failed:
            print(f"[error] fiber {fiber.name!r} FAILED: {fiber._error}", file=sys.stderr)
        raise SystemExit(1)
    # driver 插件把循环的公开面发布为服务，这里直接取用。
    return ctx, ctx.get("agent"), ctx.get("session")


# ---------------------------------------------------------------------------
# 场景预期（断言）
# ---------------------------------------------------------------------------


def final_assistant_text(session: Any) -> str:
    """会话事件日志中最后一条 assistant 消息的文本。"""
    messages = [event.data["message"] for event in session.events_of("assistant/message")]
    return messages[-1].text if messages else ""


def turn_end_reason(session: Any) -> Any:
    """最后一条 ``turn/end`` 事件的 ``reason`` 载荷（不存在则为 None）。"""
    event = session.find_last("turn/end")
    return event.data["reason"] if event else None


def seq_of(session: Any, type: str, predicate=None) -> int:
    """第一条匹配 ``predicate`` 的 ``type`` 事件的日志 seq（无匹配返回 -1）。

    会话日志是追加且有序的，比较 seq 就能证明事件的先后关系——场景断言
    用它来验证顺序（如"独占先于并行提交"、"steering 在 step 边界被认领"）。
    """
    for event in session.events:
        if event.type == type and (predicate is None or predicate(event.data)):
            return event.seq
    return -1


def check(scenario: str, ctx: Context, session: Any) -> list[str]:
    """场景级冒烟断言；返回失败清单（空列表 = 全部通过）。

    两层：所有场景都必须满足的不变量（turn/step 边界成对、每次工具调用
    都有结果），再叠加该场景自身的行为预期。
    """
    failures: list[str] = []

    def expect(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    text = final_assistant_text(session)
    reason = turn_end_reason(session)
    expect(reason is not None and reason.kind == "completed", f"turn ended completed (got {reason!r})")

    # 每个 step 边界必须成对，每次工具调用必须有结果
    expect(
        session.events_of("turn/start")
        and len(session.events_of("turn/start")) == len(session.events_of("turn/end")),
        "turn boundaries paired",
    )
    expect(
        len(session.events_of("step/start")) == len(session.events_of("step/end")),
        "step boundaries paired",
    )
    expect(
        len(session.events_of("tool/call")) == len(session.events_of("tool/result")),
        "every tool call has a result",
    )

    if scenario == "text":
        expect("4" in text, f"final text contains the answer (got {text!r})")
    if scenario == "tools":
        expect("Paris" in text and "Tokyo" in text, f"summary covers both cities (got {text!r})")
        expect(
            len(session.events_of("tool/call")) == 3,
            f"three tool calls (got {len(session.events_of('tool/call'))})",
        )
        calls = [event.data["name"] for event in session.events_of("tool/call")]
        expect(
            calls == ["set_note", "weather", "weather"],
            f"model-ordered calls (got {calls})",
        )
        # 调度证明：``set_note`` 是独占（exclusive），两个 weather 调用
        # 组成并行对——独占意味着它运行期间别的调用不得提交，所以它的
        # 结果必须先于两个 weather 结果写入日志（在追加日志上比 seq）。
        note_seq = seq_of(session, "tool/result", lambda d: "note saved" in _result_text(d))
        weather_seq = min(
            seq_of(session, "tool/result", lambda d: "Paris" in _result_text(d)),
            seq_of(session, "tool/result", lambda d: "Tokyo" in _result_text(d)),
        )
        expect(0 < note_seq < weather_seq, "exclusive barrier committed before the parallel pair")
    if scenario == "retry":
        expect("Recovered" in text, f"recovered text (got {text!r})")
        # middleware 插件记录了它观察到的 agent/request-error waterfall
        # 行；其中至少一行是 retry 决策，证明恢复走的是 waterfall
        # （而不是悄悄重掷）。
        observed: list[str] = ctx.get("middleware-observed", strict=False) or []
        expect(any("retry" in line for line in observed), "request-error waterfall retried once")
        # 失败的那次尝试不得留下自己的 assistant 消息。
        expect(
            len(session.events_of("assistant/message")) == 1,
            "only the successful attempt produced an assistant message",
        )
    if scenario == "steer":
        # steering 消息由 mock adapter 的 on_tool_call 钩子在轮中注入
        # （见 run_scenario），最终答案必须体现它已被吸收。
        expect("Tokyo" in text, f"answer absorbed the steering (got {text!r})")
        steer_seq = seq_of(
            session, "user/message", lambda d: "also include Tokyo" in d["message"].text
        )
        step1_end = seq_of(session, "step/end")
        # inbox 语义：step 中途提交的消息只能在下一个 step 边界被认领，
        # 即严格晚于 step 1 结束。
        expect(steer_seq > step1_end, "steered message claimed at the next step boundary")

    return failures


def _result_text(data: dict[str, Any]) -> str:
    """提取一条 ``tool/result`` 事件里的人类可读文本。

    tool-result 消息把载荷包在第一个 block（tool-result block）里，
    其 content 是文本 block 列表。
    """
    message = data["message"]
    block = message.content[0]
    from javis.harness.types import TextBlock

    return "".join(b.text for b in block.content if isinstance(b, TextBlock))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def run_scenario(scenario: str, verbose: bool) -> bool:
    """为一个场景启动全新组合、驱动一轮、核对预期。"""
    print(f"\n════ scenario: {scenario} " + "═" * 40)
    ctx, agent, session = await compose(scenario)
    if scenario == "steer":
        from mock_llm import steer_hook

        # 接线 steering 注入：脚本化 mock adapter 执行工具调用时，
        # steer_hook 会在轮中往 agent inbox 推一条纠正消息。
        # 延迟 import，因为只有这个场景需要这个 mock 专用钩子。
        ctx.get("mock-adapter").on_tool_call = steer_hook(agent)

    # 提交用户消息，然后跑循环直到本轮结束。
    agent.followup(UserMessage.from_text(PROMPTS[scenario]))
    await agent.when_idle()

    # observer 插件按发生顺序渲染实时转录，让运行过程可读，再核对预期。
    observer = ctx.get("observer")
    observer.report(session)

    failures = check(scenario, ctx, session)
    if failures:
        for failure in failures:
            print(f"  ✗ FAIL: {failure}")
        return False
    print("  ✓ scenario OK")
    return True


async def main_async(args: argparse.Namespace) -> int:
    """按顺序运行所选场景；0 = 全部通过，1 = 有失败。"""
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    results = {scenario: await run_scenario(scenario, args.verbose) for scenario in scenarios}
    failed = [scenario for scenario, ok in results.items() if not ok]
    print()
    for scenario, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {scenario}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("ALL SCENARIOS OK")
    return 0


def main() -> int:
    """argparse 入口（``--scenario`` / ``--verbose``）。"""
    parser = argparse.ArgumentParser(description="Harness demo (dsh-style loop on the Cordis plugin system)")
    parser.add_argument(
        "--scenario",
        choices=[*SCENARIOS, "all"],
        default="all",
        help="run one scenario (default: all)",
    )
    parser.add_argument("--verbose", action="store_true", help="reserved (verbose logging)")
    args = parser.parse_args()
    # 把 CLI 的 "all" 归一化成 None（表示跑全部场景）。
    if args.scenario == "all":
        args.scenario = None
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
