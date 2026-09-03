"""插件：``"tools"`` 服务 + demo 的 mock 工具。

提供一个 :class:`~javis.harness.tools.ToolRegistry`，并注册覆盖调度器
全部语义的四个工具：

- ``now``         —— **parallel** 模式，平凡函数体（steer 场景的注入锚点）
- ``weather``     —— **parallel** 模式，mock 城市表（未知城市走 error 路径）
- ``set_note``    —— **exclusive** 模式：屏障，执行期间别的调用不得提交
- ``end_session`` —— **concludesTurn**：它的结果提交后 turn 立即完成

注册是可逆的：每次 ``register`` 返回 disposer 并登记到插件 fiber 的
effect 上——卸载 fiber 即回滚，恢复一个空注册表。
"""

from javis.harness.tools import Tool, ToolRegistry
from javis.harness.types import TextBlock, ToolExecutionResult

# 插件名：必须与 cordis.yml 组合文件里的条目名一致。
name = "demo-tools"

# weather 工具的 mock 数据表：city → (温度, 天气)。
CITIES: dict[str, tuple[int, str]] = {
    "Paris": (18, "light rain"),
    "Tokyo": (24, "sunny"),
    "London": (12, "cloudy"),
    "New York": (29, "humid"),
}

# now 工具的固定返回（确定性，不读真实时钟）。
FIXED_NOW = "2026-08-31T18:00:00Z"


def _now(_exec):
    # 工具体签名：(exec: ToolExecutionInput) → 结果；忽略入参，返回固定值。
    return FIXED_NOW


def _weather(exec):
    # 从入参里取 city；未知城市返回 is_error 结果（演示工具 error 路径）。
    city = str(exec.arguments.get("city", "")).strip()
    if city in CITIES:
        temp, condition = CITIES[city]
        return f"{city}: {temp}C, {condition}"
    return ToolExecutionResult.text(f"Error: unknown city {city!r}", is_error=True)


def _set_note(exec):
    # exclusive 工具：内容随意，重点在调度器把它当屏障执行。
    text = str(exec.arguments.get("text", ""))
    return ToolExecutionResult.text(f"note saved: {text}")


def _end_session(_exec):
    # concludesTurn：结果提交后 driver 立即停 turn（demo 不主动用，
    # 契约面展示）。
    return ToolExecutionResult(content=[TextBlock("session ended by tool")], concludes_turn=True)


def apply(ctx):
    # 注册表本身是服务：构造时绑定 ctx（effect 语义），
    # 发布为 "tools" 服务供 driver 装配循环时取用。
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    # 四个工具覆盖调度器的四种语义：
    # parallel × 2（now/weather）+ exclusive 屏障（set_note）
    # + exclusive + concludesTurn（end_session）。
    registry.register(
        Tool(
            "now",
            "Return the current UTC time (mock).",
            parameters={"type": "object", "properties": {}},
            mode="parallel",
            body=_now,
        )
    )
    registry.register(
        Tool(
            "weather",
            "Return the (mock) weather for a city.",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            mode="parallel",
            body=_weather,
        )
    )
    registry.register(
        Tool(
            "set_note",
            "Save a workspace note (exclusive tool: runs as a barrier).",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            mode="exclusive",
            body=_set_note,
        )
    )
    registry.register(
        Tool(
            "end_session",
            "End the session; the turn concludes right after this result.",
            parameters={"type": "object", "properties": {}},
            mode="exclusive",
            body=_end_session,
        )
    )
