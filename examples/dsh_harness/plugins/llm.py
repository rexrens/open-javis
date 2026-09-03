"""插件：模型 provider（服务 ``"llm"`` = 一个 ``LlmRuntime`` 注册表）。

按 dsh 的方式装配 LLM 服务：插件提供一个
:class:`~javis.llm.LlmRuntime`（adapter 注册表；构造即自动注册为
``"llm"`` 服务），并在 ``"mock"`` provider 路由下注册一个
:class:`~mock_llm.MockAdapter`。场景（script）从两个来源取：

1. 条目配置（``cordis.yml`` 的 ``config.scenario``）；
2. 环境变量 ``HARNESS_DEMO_SCENARIO``（``cli.py`` 每次运行前设置）；
3. 都没有则回退 ``text``。

**换成真实 adapter 是一文件的事**：实现 ``javis.llm.LLMAdapter.stream``，
在本插件里换成你的 adapter 类并注册到对应 provider 路由即可，引擎零改动。
adapter 实例同时以 ``"mock-adapter"`` 服务发布，供场景驱动挂钩子
（``on_tool_call`` / ``on_call``，见 steer 场景）。
"""

import os as _os

from mock_llm import MockAdapter, scenario_script
from pydantic import BaseModel

from javis.llm import LlmRuntime

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "llm"


class Config(BaseModel):
    """插件配置 schema（cordis.yml 的 ``config:`` 按此校验）。"""

    #: 场景名：``text`` / ``tools`` / ``retry`` / ``steer`` 之一；
    #: None 时回退 ``$HARNESS_DEMO_SCENARIO``，再回退 ``text``。
    scenario: str | None = None
    #: 脚本化模型的路由名（mock 不真的连网，名字只进请求头日志）。
    model: str = "mock-mini"


def apply(ctx, config):
    # 场景解析顺序：config > 环境变量 > text。
    scenario = config.scenario or _os.environ.get("HARNESS_DEMO_SCENARIO", "text")
    # 按场景取脚本（mock_llm.scenario_script），构造脚本化 adapter。
    adapter = MockAdapter(scenario_script(scenario), model=config.model)
    # LlmRuntime 构造即自动注册 "llm" 服务（Service 语义）；
    # 再把 adapter 挂到 "mock" provider 路由下。
    runtime = LlmRuntime(ctx)  # auto-registers the "llm" service
    runtime.register_adapter(["mock"], adapter)
    # 发布 adapter 实例本身：场景驱动（cli.py 的 steer 场景）靠它挂
    # on_tool_call 钩子做确定性注入。
    ctx.provide("mock-adapter", adapter)
