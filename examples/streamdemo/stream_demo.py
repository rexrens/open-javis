# stream_demo.py
"""
伪流式输出示例：
模拟一个 Agent 逐 token 返回事件，前端逐块渲染，形成打字机效果。
"""

import asyncio
import random
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 1. 事件定义
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """Agent 流式返回的一个事件。"""
    type: str          # "token" | "done" | "error"
    content: str = ""


# ---------------------------------------------------------------------------
# 2. 模拟的 Agent
# ---------------------------------------------------------------------------

class FakeAgent:
    """
    假的 Agent：submit_message 返回一个异步生成器，
    模拟真实 LLM 逐 token 吐字的过程。
    """

    async def submit_message(self, prompt: str) -> AsyncIterator[Event]:
        # 模拟"首字延迟"（TTFT, Time To First Token）
        await asyncio.sleep(0.5)

        reply = f"你说的是：{prompt}。我是一个伪流式输出的例子 🍒"

        # 逐字符吐出 token
        for ch in reply:
            await asyncio.sleep(0.03)     # 模拟每个 token 的网络延迟
            yield Event(type="token", content=ch)

        # 结束事件
        yield Event(type="done")


# ---------------------------------------------------------------------------
# 3. 渲染层
# ---------------------------------------------------------------------------

async def render_event(event: Event) -> None:
    """把单个事件渲染到终端，实现打字机效果。"""
    if event.type == "token":
        sys.stdout.write(event.content)
        sys.stdout.flush()                # 关键：强制立即输出，不缓冲
    elif event.type == "done":
        print()                           # 结尾换行
    elif event.type == "error":
        print(f"\n[错误] {event.content}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------

@dataclass
class Result:
    submit_prompt: str | None


# 准备 10 个 prompt，随机抽一个
PROMPTS = [
    "你好，介绍一下你自己",
    "用一句话解释什么是递归",
    "帮我写一首关于秋天的五言绝句",
    "Python 的 GIL 是什么，简单说说",
    "推荐三本值得读的书",
    "解释一下什么是闭包",
    "给我讲个程序员冷笑话",
    "如何优雅地拒绝一个不合理需求",
    "用比喻说明什么是数据库索引",
    "今天晚饭吃什么好呢",
]


async def run(result: Result, agent: FakeAgent) -> None:
    if result.submit_prompt:
        print(f"👤 用户：{result.submit_prompt}\n")   # 先回显问题
        print("🤖 助手：", end="", flush=True)
        async for event in agent.submit_message(result.submit_prompt):
            await render_event(event)


async def main() -> None:
    agent = FakeAgent()
    prompt = random.choice(PROMPTS)        # ← 随机抽一个
    result = Result(submit_prompt=prompt)
    await run(result, agent)


if __name__ == "__main__":
    asyncio.run(main())