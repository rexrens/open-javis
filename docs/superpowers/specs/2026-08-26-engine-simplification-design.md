# javis 引擎层简化设计 — 去掉 QueryEngine / create_agent_backend 封装链

日期：2026-08-26
状态：已实施（D1 已确认：保留 corecoder.Agent 为内部组件；其余按推荐执行）
参考：2026-08-11-javis-multi-engine-design.md（本设计取代其"registry + 工厂"接线）

## 1. 背景与问题

当前引擎接线是一条 **4 层封装链 + 1 个工厂过程**：

```
宿主（backend_host / handle_line / commands / _save_session）
  → QueryEngine            javis/host/query_engine.py    历史 + usage + submit_message 事件流
    → AgentBackend(Protocol)  javis/contracts/protocol.py   run_turn 契约
      → CoreCoderBackend     javis/engines/corecoder/backend.py  queue 桥接 + 消息转换
        → corecoder.Agent    javis/engines/corecoder/agent.py    真实 agent loop（chat/achat/tools/llm）
          → LLMProvider
```

工厂过程（runtime 里 7 个步骤只为造一个对象）：

```
resolve_provider_and_model(cfg) → resolve_api_key() → 手工拼 engine_config dict
→ create_agent_backend(DEFAULT_ENGINE, ...)   # registry 查表
→ build_corecoder_backend(...)                 # Config.from_env + 重拼 Config + Provider + Agent + Backend
→ QueryEngine(agent_backend=...)
```

**问题清单：**

| # | 问题 | 证据 |
|---|---|---|
| 1 | **概念重复**：QueryEngine ≈ Agent（都有 messages / clear / load_messages / set_system_prompt / max_turns / reset） | query_engine.py vs agent.py |
| 2 | **双份历史**：QueryEngine._messages（ConversationMessage，权威）+ Agent.messages（dict）平行维护；restore 时双份同步（load_messages + load_history） | runtime.py:167-172 |
| 3 | **适配层无独立价值**：CoreCoderBackend 只是把 achat callbacks 转成 AgentEvent 流 + 消息格式转换——这是引擎自己该做的事 | backend.py |
| 4 | **工厂过程繁琐**：registry 查表 + engine_config 手工拼装 + Config 重建，全是间接层 | runtime.py:114-150 + engines/registry.py |
| 5 | **usage 双份**：QueryEngine._usage 累加 vs llm 的 total_prompt/completion_tokens 差值 | query_engine.py:130-143 |

## 2. 目标

1. 引擎 = **一个对象**：宿主只看到一个 `AgentEngine`（契约）+ 默认实现 `CoreCoderEngine`。4 层 → 1 层（契约 + 实现），corecoder.Agent 降级为实现内部组件（或并入）。
2. 删除：`query_engine.py`、`corecoder/backend.py`（CoreCoderBackend + build_corecoder_backend）、`engines/registry.py`（create_agent_backend / list_engines / register_engine / get_engine_config）。
3. runtime 直接构建引擎对象：config 解析保留在 runtime，但去掉 registry 间接层与 engine_config 手工拼装。
4. **插件化衔接**（延续"核心不接插件"决策，本次只做接口形状预留）：`AgentEngine` 契约 = 未来插件系统 "agent 服务" 的接口；替换实现 = 插件 `ctx.provide("engine", impl)`。本次不实现。

## 3. 目标架构

```
宿主 → AgentEngine（契约，javis/contracts/engine.py）
         ↑ 实现（默认）
      CoreCoderEngine（javis/engines/corecoder/engine.py）
         ├─ 历史：ConversationMessage 权威（session 持久化不动）
         ├─ usage：llm 差值累加（吸收 CoreCoderBackend 逻辑）
         ├─ 事件流：achat callbacks → AgentEvent（吸收 CoreCoderBackend 的 queue 桥接）
         └─ 内部：corecoder.Agent（纯 loop）或直接 LLM+tools
```

**AgentEngine 契约**（从 commands/registry.py 已有的 `AgentEngine(Protocol)` 提升 + 补全）：

```python
class AgentEngine(Protocol):
    @property
    def messages(self) -> list[ConversationMessage]: ...
    @property
    def total_usage(self) -> UsageSnapshot: ...
    @property
    def model(self) -> str: ...
    @property
    def system_prompt(self) -> str: ...
    @property
    def tool_metadata(self) -> dict[str, Any]: ...
    @property
    def max_turns(self) -> int | None: ...
    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[AgentEvent]: ...
    def clear(self) -> None: ...
    def load_messages(self, messages: list[ConversationMessage]) -> None: ...
    def set_system_prompt(self, prompt: str) -> None: ...
    def set_max_turns(self, max_turns: int | None) -> None: ...
    def set_model(self, model: str) -> None: ...
    def set_effort(self, effort: str | None) -> None: ...
```

- `continue_pending` **删除**（corecoder 一轮 achat 完成全部 step，无 pending 概念；无命令设置 continue_pending）
- 契约放 `javis/contracts/engine.py`；`protocol.py` 删除（AgentBackend 被 AgentEngine 取代）

## 4. 决策点

| # | 决策 | 推荐 |
|---|---|---|
| D1 | 引擎对象内部结构 | 保留 `corecoder.Agent` 为内部组件（两层的"实现内部组件"，宿主只见一层对象）；CoreCoderEngine 吸收 QueryEngine + CoreCoderBackend + build_corecoder_backend 的 Config 拼装 |
| D2 | 历史权威 | 保持 `ConversationMessage` 权威（`_save_session` / restore 不动）；dict 历史由引擎层同步（吸收 backend._to_corecoder_messages 反向转换） |
| D3 | `AgentEngine` 契约位置 | `javis/contracts/engine.py`（新文件）；commands/registry.py 的本地 Protocol 改引用它 |
| D4 | `continue_pending` | 删除（契约 + handle_line 分支 + CommandResult 字段） |
| D5 | `set_model` / `set_effort` | 保留在契约（host 有 model 选择器，effort 预留） |
| D6 | 插件化衔接 | 本次不接入插件系统；spec 注记未来 `ctx.provide("engine", impl)`，接口即 AgentEngine |

## 5. 影响面

| 文件 | 动作 |
|---|---|
| `javis/contracts/protocol.py` | 删除（AgentBackend 被 AgentEngine 取代） |
| `javis/contracts/engine.py` | 新增（AgentEngine 契约） |
| `javis/contracts/__init__.py` | 导出 AgentEngine，去 AgentBackend |
| `javis/host/query_engine.py` | 删除（职责并入 CoreCoderEngine） |
| `javis/engines/corecoder/backend.py` | 删除（queue 桥接 + 转换 + Config 拼装并入 engine.py） |
| `javis/engines/corecoder/engine.py` | 新增（CoreCoderEngine） |
| `javis/engines/registry.py` + `__init__.py` | 删除（create_agent_backend 等） |
| `javis/host/runtime.py` | 直接构建 CoreCoderEngine；engine_max_turns/config 解析保留；handle_line 去 continue_pending 分支 |
| `javis/commands/registry.py` | AgentEngine 引用改 contracts |
| `javis/__init__.py` docstring | 更新描述 |
| `tests/test_query_engine.py` | 改造 → test_corecoder_engine（直测 CoreCoderEngine） |
| `tests/test_corecoder_backend.py` | 删除/合并 |
| `tests/test_engines.py` | 删除 registry 测试 |
| `tests/test_runtime.py` / `fake_backend.py` | FakeBackend 实现 AgentEngine 形状 |

## 6. 落地步骤

```
阶段 A：契约
  A1  contracts/engine.py 新增 AgentEngine；protocol.py 删除；contracts/__init__ 更新
  A2  commands/registry.py 改引用

阶段 B：实现
  B1  corecoder/engine.py 新增 CoreCoderEngine（历史 + usage + 事件流 + Config 构建）
  B2  删 query_engine.py / corecoder/backend.py / engines/registry.py（+ engines/__init__）
  B3  runtime.py 直接构建 CoreCoderEngine；handle_line 去 continue_pending

阶段 C：测试
  C1  FakeBackend 改 AgentEngine 形状；test_query_engine → test_corecoder_engine
  C2  删 test_corecoder_backend / test_engines（registry 部分）
  C3  全量测试 + ruff

阶段 D：文档
  D1  docs/plugins.md 无改动（不涉插件）；agent-engine-guide.md 更新
  D2  spec 归档注记
```

## 7. 明确不做（YAGNI）

- 不接入插件系统（延续"核心不接插件"决策；接口形状预留即可）
- 不做多引擎运行时选择（registry 删除后默认 corecoder；未来引擎 = 插件 provide）
- 不重构 corecoder.Agent 内部 loop（它保持纯 chat/achat）
- 不引入 continue_pending / 会话恢复的 pending 概念
