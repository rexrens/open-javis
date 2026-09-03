"""插件：实时事件观察器（transcript）。

订阅 agent 的**实时**事件（emit/serial 监听器；dsh 里这些监听在
agent 作用域内，demo 在共享 context 上分发、payload 里带 agent）
并打印运行中的 transcript：

- ``agent/status``              —— 生命周期状态迁移
- ``agent/inbox/inserted``      —— 输入进入收件箱排队
- ``agent/inbox/claimed``       —— step 边界消费了排队输入
- ``agent/inbox/discarded``     —— cancel 清空了队列
- ``tools/result``              —— 每次工具结果提交
- ``agent/turn-stopping``       —— turn 边界
- ``agent/error``               —— 失败（在其实时边界上）

``report(session)`` 渲染**持久化会话日志**（user / assistant / tool
消息、turn 结局、usage）——这部分等价于 UI 桥要回放的东西。
cli.py 先跑完场景，再调 report 打印完整日志。
"""

from javis.harness.types import (
    Events,
    TextBlock,
    ToolCallBlock,
)

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "observer"


class Observer:
    """实时监听 + 会话日志渲染，一身二职。"""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # 所有打印过的行留一份内存副本（测试可断言）。
        self.lines: list[str] = []

    def line(self, text: str) -> None:
        # 打印 + 记录（transcript 的每一行都走这里）。
        self.lines.append(text)
        print(text)

    # -- 实时监听器（运行中打印） -------------------------------------------

    def on_status(self, payload):
        # agent 生命周期状态迁移（running / idle / ...）。
        self.line(f"● agent status → {payload['status']}")

    def on_inbox_inserted(self, payload):
        # 输入进入 inbox 排队（如 steer 场景的 steering 消息入队时刻）。
        self.line(f"◦ inbox queued: {payload['message'].text!r}")

    def on_inbox_claimed(self, payload):
        # step 边界认领了排队输入（steer 场景断言的"边界认领"在此可见）。
        self.line(f"◦ inbox claimed (turn {payload['turn']}): {payload['message'].text!r}")

    def on_inbox_discarded(self, payload):
        # cancel 清空队列时触发（demo 场景不触发，契约面展示）。
        self.line(f"◦ inbox discarded: {payload['message'].text!r}")

    def on_tool_result(self, _exec, result):
        # 每次工具结果提交：✓/✗ 标错误路径；concludes_turn 标记展示。
        text = "".join(block.text for block in result.content if isinstance(block, TextBlock))
        flag = "✗" if result.is_error else "✓"
        extra = " [concludes-turn]" if result.concludes_turn else ""
        self.line(f"  {flag} tool result: {text}{extra}")

    def on_turn_stopping(self, payload):
        # turn 边界（step 结束 → turn-stopping → turn/end）。
        self.line(f"… turn {payload['turn']} stopping")

    def on_error(self, payload):
        # 失败在其实时边界上（retry 场景的失败尝试会走这里）。
        self.line(f"✗ agent error (turn {payload['turn']} step {payload['step']}): {payload['error']}")

    # -- 持久化报告（cli.py 场景结束后调用） ---------------------------------

    def report(self, session) -> None:
        """按 seq 顺序渲染会话事件日志（UI 桥回放的就是这份数据）。

        chunk / inbox splice / step-end / request-context 属于日志细节，
        这里不逐行打印（保持 transcript 可读）。
        """
        print()
        print("── session log " + "─" * 40)
        for event in session.events:
            data = event.data
            if event.type == "turn/start":
                print(f"  [{event.seq:>3}] turn {data['turn']} start")
            elif event.type == "step/start":
                print(f"  [{event.seq:>3}]   step {data['step']} start")
            elif event.type == "user/message":
                print(f"  [{event.seq:>3}]   user: {data['message'].text!r}")
            elif event.type == "assistant/message":
                # assistant 消息可能同时含文本和工具调用块，分别打印；
                # interrupted 标记（流中断的半截消息）原样展示。
                message = data["message"]
                calls = [block for block in message.content if isinstance(block, ToolCallBlock)]
                interrupted = " (interrupted)" if data.get("interrupted") else ""
                text = message.text
                if text:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: {text!r}")
                for call in calls:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: → {call.name}({call.arguments})")
                if not text and not calls:
                    print(f"  [{event.seq:>3}]   assistant{interrupted}: (no content)")
            elif event.type == "tool/call":
                print(f"  [{event.seq:>3}]   tool call: {data['name']}({data['arguments']})")
            elif event.type == "tool/result":
                # 工具结果包在第一个 block（tool-result block）里，
                # 其 content 是文本块列表。
                message = data["message"]
                block = message.content[0]
                text = "".join(b.text for b in block.content if isinstance(b, TextBlock))
                flag = "✗" if block.is_error else "✓"
                print(f"  [{event.seq:>3}]   tool result {flag}: {text}")
            elif event.type == "turn/end":
                # turn 结局：completed / blocked / error / aborted / max-tokens。
                print(f"  [{event.seq:>3}] turn {data['turn']} end: {data['reason'].kind}")
            elif event.type == "request/header":
                # 请求头变更日志（initial/change）：路由 + maxTokens。
                config = data["header"]["config"]
                print(
                    f"  [{event.seq:>3}] request/header ({data['reason']}): "
                    f"{config['provider']}/{config['model']} maxTokens={config['maxTokens']}"
                )
        # 全 session 的 token 用量合计。
        total_in, total_out = session.usage_total()
        print(f"  usage: {total_in} input / {total_out} output tokens")


def apply(ctx):
    # 挂上全部实时监听器，并发布 observer 服务
    # （cli.py 靠 ctx.get("observer") 调 report 打印会话日志）。
    observer = Observer(ctx)
    ctx.on(Events.AGENT_STATUS, observer.on_status)
    ctx.on(Events.AGENT_INBOX_INSERTED, observer.on_inbox_inserted)
    ctx.on(Events.AGENT_INBOX_CLAIMED, observer.on_inbox_claimed)
    ctx.on(Events.AGENT_INBOX_DISCARDED, observer.on_inbox_discarded)
    ctx.on(Events.TOOLS_RESULT, observer.on_tool_result)
    ctx.on(Events.AGENT_TURN_STOPPING, observer.on_turn_stopping)
    ctx.on(Events.AGENT_ERROR, observer.on_error)
    ctx.provide("observer", observer)
