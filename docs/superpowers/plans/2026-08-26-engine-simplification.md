# 引擎层简化实施计划 — 去掉 QueryEngine / create_agent_backend

日期：2026-08-26
依据：docs/superpowers/specs/2026-08-26-engine-simplification-design.md（D1 已确认）
状态：已实施（167 tests passed，javis/ ruff clean）

## 阶段 A：契约

- A1 `javis/contracts/engine.py`（新）：`AgentEngine(Protocol)` — messages / total_usage / model / system_prompt / max_turns / tool_metadata / submit_message / clear / load_messages / set_system_prompt / set_max_turns / set_model / set_effort
- A2 删 `javis/contracts/protocol.py`（AgentBackend 被取代）；`contracts/__init__.py` 更新导出
- A3 `javis/commands/registry.py`：删本地 `AgentEngine(Protocol)`，引用 `javis.contracts.engine.AgentEngine`；删 `CommandResult.continue_pending` / `continue_turns` 字段

## 阶段 B：实现

- B1 `javis/engines/corecoder/engine.py`（新）：`CoreCoderEngine`
  - 吸收 QueryEngine：历史（ConversationMessage 权威）、usage 累加、submit_message 事件流外壳、属性/setter、_build_context 删除（AgentContext 不再需要）
  - 吸收 CoreCoderBackend：queue 桥接（producer-consumer）、`_user_text` / `_to_corecoder_messages` 转换、llm 差值 usage
  - 吸收 build_corecoder_backend：`build()` 静态工厂（Config 拼装 + OpenAICompatProvider + Agent(tools=all_tools()) + 引擎）
  - `set_max_turns` 同步 `agent.max_rounds`；`load_messages`/`clear` 同步 `agent.load_messages`/`reset`
- B2 删 `javis/host/query_engine.py`、`javis/engines/corecoder/backend.py`、`javis/engines/registry.py`；`javis/engines/__init__.py` 空壳或删除（检查引用）
- B3 `javis/host/runtime.py`：`build_javis_runtime(engine=...)` 直接构建 `CoreCoderEngine.build(...)`（provider/api_key 解析保留，engine_config 手工拼装删除）；handle_line 删 continue_pending 分支；`_save_session` 不变
- B4 `javis/host/backend_host.py`：`_inject_permission_checker` 少一层 getattr（engine._agent 即 corecoder.Agent）

## 阶段 C：测试

- C1 `tests/test_javis/fake_backend.py` → `FakeEngine`（实现 AgentEngine：submit_message 产出文本 delta + turn end；messages/usage/clear 等）
- C2 `tests/test_query_engine.py` → `test_corecoder_engine.py`（直测 CoreCoderEngine：历史、usage 累加、事件透传、clear/load/set 同步 agent）
- C3 删 `tests/test_corecoder_backend.py`、`tests/test_engines.py`；`tests/test_runtime.py` / `test_backend_host.py` 改用 FakeEngine
- C4 全量测试 + ruff clean（javis/）

## 阶段 D：文档

- D1 `docs/agent-engine-guide.md` 更新（引擎 = AgentEngine 契约 + CoreCoderEngine 实现，未来插件提供）
- D2 `javis/__init__.py` docstring 更新
