# LLM 层重构：port dsh 的 LlmRuntime adapter 设计（去中间转换层）

Status: **ready for review**

## Context

用户反馈（2026-09-01）：
1. `javis/plugins/` 是空目录（只剩旧 `__pycache__`，零引用）→ 删除。
2. LLM 调用太复杂：`contracts.llm`（LLMProvider 契约）、`harness.llm`（dsh seam）、
   `harness.llm_adapter`（JavisLLMAdapter 桥接）、`javis.llm`（providers 实现）四层叠床架屋。
   参考 deepseek-harness（本地 `/home/rensu/workspace/deepseek-harness`）的 adapter 设计重构。

## 决策（用户确认 2026-09-01）

- **Q1 → 最全 port LlmRuntime**：adapter 注册表 + configurable providers 目录 + model discovery
  + `llm/stream` waterfall + prepareCall（对齐 `packages/llm/llm/src/index.ts`）
- **Q2 → `javis/llm` 为 adapter 层**：LlmRuntime + LlmAdapter 抽象 + OpenAICompat/Scripted adapter
  + 定价表，依赖 `javis.harness.types`（单向）；新 provider 以后加模块
- **Q3（自定）**：定价表 `_PRICING`/`estimated_cost` → `javis/llm/pricing.py`；
  磁盘缓存保留为 OpenAICompatAdapter 构造选项；retryPolicy 注册时捕获（dsh 形状）
- `contracts/llm.py` 删除（LLMProvider/LLMRequest/LLMResponse/ToolCall 被 GenerateOptions/StreamChunk 取代；
  宿主层 app/session/tools/commands **零使用**，grep 证实）

## dsh 参考（已读源码）

- `LlmRuntime`（cordis Service，`ctx.llm`）：`registerAdapter(providers, adapter)` 原子注册/替换
  （disposer + `replace()`）、`registerConfigurableProviders` 目录、`registerModelDiscovery`/`discoverModels`、
  `listProviders`/`listModels`/`resolveCallConfig`、`prepareCall(config, signal) → PreparedLlmCall
  {config, retryPolicy, adapterDefaults, context, stream}`（dispatch 一次性 + callConfigEquals 校验）、
  `stream(options)` = `ctx.waterfall('llm/stream', options, adapterStream)`（adapter 选择/迭代失败
  归一化为 terminal `error`/`aborted` finish chunk）
- `LlmAdapter` 抽象：**唯一抽象方法 `stream(options: GenerateOptions) → AsyncIterable<StreamChunk>`**；
  可选 `providerInfo`/`providerRetryPolicy`/`listModels`/`resolveModel`/`prepareCall`
- adapter 直接消费 GenerateOptions（dsh 消息模型）→ StreamChunk，**无中间转换层**；序列化在 adapter 内部
- retry 不在 stream 层：`llm-retry` 挂 `agent/request-error`（javis engine/demo 已有同构机制）

## 目标结构

```
javis/llm/                          ← adapter 层（新）
  runtime.py        LlmRuntime（全量 port：注册表/目录/discovery/llm-stream waterfall/prepareCall）
  adapter.py        LlmAdapter 抽象基类（stream 唯一抽象 + 可选 provider_info/resolve_model/...）
  openai_compat.py  OpenAICompatAdapter —— 合并原 providers.py(SDK/解析) + llm_adapter.py(序列化/流转换)
  scripted.py       ScriptedAdapter（脚本 chunk 回放，测试/演示）
  pricing.py        _PRICING 定价表 + estimated_cost（从 contracts/llm.py 迁入）
  __init__.py       re-export LlmRuntime/LlmAdapter/OpenAICompatAdapter/ScriptedAdapter/estimated_cost

javis/harness/                      ← 消费方（本次尽量少动）
  llm.py            保留：LLM Protocol / PreparedCall（已是 dsh PreparedLlmCall 投影）/
                    normalized_stream / BlockAssembler / chunk_response（agent 循环消费）
  types.py          保留：GenerateOptions/StreamChunk/LlmCallConfig/LlmError/LlmFailure
                    （javis/llm 单向 import；迁移 type 面超范围，本次不动）
  engine.py         改：LlmRuntime(loop_ctx) 取代 JavisLLMAdapter；adapter 参数取代 provider
  build.py          改：构造 OpenAICompatAdapter
  llm_adapter.py    删除（逻辑并入 openai_compat.py）

javis/contracts/llm.py              删除；contracts/__init__.py 移除 4 个导出
javis/plugins/                      删除（空目录）
```

依赖方向：`javis/llm → javis/harness.types + javis/harness.llm(chunk_response) + javis.cordis(Service/Context/waterfall/effect)`；
`javis/harness/engine → javis.llm`。已确认 javis.cordis 具备全部条件：
`Service`（构造即注册 + fiber 自动移除）、`ctx.effect`、`ctx.waterfall(name, *args)`（最后参数为 next）、
EventsService 五种 dispatch 模式。

## Files to modify

**删除**：`javis/plugins/`、`javis/contracts/llm.py`、`javis/harness/llm_adapter.py`、`javis/llm/providers.py`
**新增**：`javis/llm/{runtime,adapter,openai_compat,scripted,pricing}.py`
**修改**：
- `javis/contracts/__init__.py` — 移除 LLMProvider/LLMRequest/LLMResponse/ToolCall
- `javis/llm/__init__.py` — 重写 re-export
- `javis/harness/engine.py` — 构造 `LlmRuntime(self._loop_ctx)` + `register_adapter(["javis"], adapter)`；
  `provider: LLMProvider` 参数 → `adapter: LlmAdapter`；`set_model` 委托 adapter；docstring
- `javis/harness/build.py` — `provider: LLMProvider | None` → `adapter: LlmAdapter | None`；
  默认构造 OpenAICompatAdapter(model/api_key/base_url/max_tokens, cache 选项)
- `javis/harness/__init__.py` — 移除 JavisLLMAdapter 导出
- `examples/dsh_harness/mock_llm.py` — MockLLM → `MockAdapter(LlmAdapter)`（stream 产出 chunk，
  复用 chunk_response；steer 钩子保留）
- `examples/dsh_harness/plugins/llm.py` — provide `LlmRuntime(ctx)` + `register_adapter(["mock"], MockAdapter)`
- `tests/test_harness/test_llm_adapter.py` — 重写为 LlmRuntime/OpenAICompatAdapter/ScriptedAdapter 测试
- `tests/test_harness/test_async_llm.py` — ScriptedAdapter/OpenAICompatAdapter/estimated_cost 测试
- `tests/test_harness/test_engine.py`、`test_agent_loop.py` — `ScriptedAdapter(script=[chunk_response(...)])`
- 扫尾 grep：`LLMProvider`/`LLMRequest`/`LLMResponse`/`JavisLLMAdapter`/`javis.llm.providers`

## Reuse

- `javis/harness/llm.py` — `chunk_response`（ScriptedAdapter 脚本生成）、`PreparedCall`（LlmRuntime.prepare_call 返回值）、
  `normalized_stream` 语义（并入 runtime.adapter_stream）
- `javis/llm/providers.py` — `_parse_delta`/`_parse_completion`/`is_fallback_trigger`/惰性双客户端（并入 openai_compat.py）
- `javis/harness/llm_adapter.py` — `_to_openai_message`/`_to_openai_tool`/`_map_finish`/block 边界 + tool diff 逻辑（并入 openai_compat.py）
- `javis/cordis` — Service/Context/waterfall/effect（LlmRuntime 基座）

## Steps

- [x] 1. 删除 `javis/plugins/`（空目录）
- [x] 2. `javis/llm/pricing.py`：从 contracts/llm.py 迁 `_PRICING` + `estimated_cost`
- [x] 3. `javis/llm/adapter.py`：`LlmAdapter` 抽象（stream 抽象 + provider_info/resolve_model/prepare_call 可选默认）
- [x] 4. `javis/llm/runtime.py`：`LlmRuntime(Service)` 全量 port（register_adapter + replace + 原子校验、
      configurable providers 目录、model discovery、list_*、prepare_call → PreparedCall、llm/stream
      waterfall + adapter_stream 归一化、llm/adapters-updated emit）
- [x] 5. `javis/llm/openai_compat.py`：OpenAICompatAdapter（合并 providers.py SDK 逻辑 + llm_adapter.py
      序列化/流转换；缓存选项保留；resolve_model → contextWindow/defaultMaxTokens）
- [x] 6. `javis/llm/scripted.py`：ScriptedAdapter（脚本 = 每次调用一个 chunk 序列，chunk_response 生成）
- [x] 7. 重写 `javis/llm/__init__.py`；删除 `javis/llm/providers.py`、`javis/harness/llm_adapter.py`、
      `javis/contracts/llm.py`；更新 `javis/contracts/__init__.py`、`javis/harness/__init__.py`
- [x] 8. `engine.py` / `build.py`：LlmRuntime + adapter 装配；set_model 委托；docstring
- [x] 9. demo：mock_llm.py → MockAdapter；plugins/llm.py → LlmRuntime + register_adapter
- [x] 10. 重写/更新 4 个测试文件
- [x] 11. grep 扫尾 + README/docs 引用更新

## Verification

- [x] `uv run pytest tests/ -q` 全绿
- [x] `uv run python examples/dsh_harness/cli.py` — 4 场景 OK
- [x] `uv run ruff check javis/ examples/ tests/` — 无新增
- [x] grep `LLMProvider|LLMRequest|LLMResponse|JavisLLMAdapter|javis.llm.providers` → 空
