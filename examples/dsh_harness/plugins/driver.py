"""插件：组合根——session + agent（服务 ``"session"`` / ``"agent"``）。

agent 需要的一切依赖都经 ``inject`` 到达（依赖驱动的加载顺序由 Cordis
决定，与组合文件的书写顺序无关）：

    llm · tools · systemPrompt · agentLoop

driver 从不直接构造引擎的部件——它从 context 里取出这些服务，组合成
``ReactLoopAgent``，正如 dsh 的 runtime 在其 ``Context`` 服务之上
构建 agent 一样。宿主（cli.py）最后只认 ``agent`` 服务的公开契约：
followup / steer / inject / cancel / when_idle。
"""

import os as _os
import uuid

from javis.harness.agent import ReactLoopAgent
from javis.harness.session import Session
from javis.harness.types import AgentOptions

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "driver"

#: 服务依赖清单——四个服务任一未 ACTIVE 前，本 fiber 保持 PENDING。
#: 这就是"依赖驱动加载"：组合文件里 driver 写在最前面也照样等。
inject = ["llm", "tools", "systemPrompt", "agentLoop"]


def apply(ctx):
    # 新建一个会话（事件日志的载体）；id 带随机后缀避免重复运行时
    # 日志串号。cwd 记录工作区，进 session context 渲染。
    session = Session(f"demo-{uuid.uuid4().hex[:8]}", cwd=_os.getcwd())
    ctx.provide("session", session)
    # 装配 ReactLoopAgent：循环从 context 取 llm/tools/systemPrompt/
    # agentLoop 四个服务（上面 inject 保证它们已 ACTIVE）。
    # provider/model 是初始路由 seed——真正的最终路由由
    # agent/request waterfall 决定（见 middleware.py 的路由改写）。
    agent = ReactLoopAgent(
        ctx,
        session.id,
        AgentOptions(provider="mock", model="mock-mini"),
        session,
    )
    # 发布 agent 服务：宿主只认这一层公开契约。
    ctx.provide("agent", agent)
