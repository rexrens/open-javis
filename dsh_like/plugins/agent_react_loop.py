from runtime.context import Context
from abstractions import BaseAgentLoop, BaseLLM, BaseToolRegistry, Session, Message, ToolCall


class ReactAgentLoop(BaseAgentLoop):
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.llm: BaseLLM = ctx.services[BaseLLM]
        self.tools: BaseToolRegistry = ctx.services[BaseToolRegistry]

    async def run(self, session: Session):
        await self.ctx.async_emit("turn/start", session)

        while True:
            # 瀑布事件：插件可修改会话、拦截终止
            pre_ok = await self.ctx.waterfall("agent/pre-step", session)
            if pre_ok is False:
                break

            await self.ctx.async_emit("step/start", session)

            # 调用 LLM 流式生成
            full_msg = Message(role="assistant", content="")
            tool_calls: list[ToolCall] = []

            async for chunk in self.llm.stream(
                messages=session.messages,
                tools=self.tools.get_tool_definitions()
            ):
                await self.ctx.async_emit("assistant/chunk", chunk, session)
                if chunk.content:
                    full_msg.content += chunk.content
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)

            session.add_message(full_msg)

            # 有工具调用则执行
            if tool_calls:
                for tc in tool_calls:
                    await self.ctx.async_emit("tool/call", tc, session)
                    result = await self.tools.execute(tc)
                    await self.ctx.async_emit("tool/result", tc, result, session)
                    session.append_tool_result(tc, result)
                await self.ctx.async_emit("step/end", session)
                continue  # 进入下一轮 step

            # 无工具调用，结束循环
            await self.ctx.async_emit("step/end", session)
            break

        await self.ctx.async_emit("turn/end", session)


inject = [BaseLLM, BaseToolRegistry]
provides = [BaseAgentLoop]


def apply(ctx: Context):
    loop = ReactAgentLoop(ctx)
    ctx.provide(BaseAgentLoop, loop)
    print("✅ ReactAgentLoop 服务已注册")

    def dispose():
        print("❌ ReactAgentLoop 服务卸载")
    return dispose
