import asyncio
from runtime import Context, PluginManager
from abstractions import Session, Message, BaseAgentLoop
from plugins import (
    apply_memory_inmem,
    apply_llm_agno,
    apply_tools_simple,
    apply_agent_react_loop
)


async def main():
    # 1. 初始化内核
    root_ctx = Context()
    pm = PluginManager(root_ctx)

    # 2. 批量加载插件（自动拓扑排序）
    plugins = [
        apply_agent_react_loop,
        apply_tools_simple,
        apply_llm_agno,
        apply_memory_inmem,
    ]
    pm.load_all(plugins)

    # 3. 演示：创建会话，运行 Agent
    agent_loop: BaseAgentLoop = root_ctx.services[BaseAgentLoop]
    session = Session(session_id="demo_001")
    session.add_message(Message(role="user", content="计算 123 + 456 等于多少"))

    print("\n===== Agent 开始运行 =====")
    await agent_loop.run(session)
    print("\n===== 最终回复 =====")
    print(session.messages[-1].content)

    # 4. 演示：卸载 LLM 服务，触发级联卸载
    print("\n===== 卸载 LLM 插件，触发级联卸载 =====")
    pm.unload(apply_llm_agno)


if __name__ == "__main__":
    asyncio.run(main())
