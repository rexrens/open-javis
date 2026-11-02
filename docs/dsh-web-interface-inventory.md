# dsh Web 接口清单（页面配置 · HTTP RPC · WebSocket 流）

> 本文件由 `scripts/inventory_dsh_web.py` 生成（`--check` 为漂移闸门）。dsh 仓库本身没有
> 这份清单；这里的内容全部来自对检出源码的只读扫描。

- 扫描的 dsh 版本：`0.1.6-alpha.2`，commit `ddefc45fbc`
- 覆盖范围：Remote 端点、流端点、精确 Fetch 路由、转发事件白名单、index 注入贡献者

## 0. 传输总览

| 载体 | 路径 | 方法 | 语义 | 鉴权 |
|---|---|---|---|---|
| 页面 | `/`、配置的 index 路径 | GET/HEAD | 返回注入后的 `index.html` | 需要浏览器会话（token→cookie） |
| 静态资产 | dist 内已存在的文件 | GET/HEAD | 直接返回；未知路径 404（无 SPA 回退） | 公开 |
| 插件 bundle | `/plugins/...` | GET/HEAD | combo 脚本 / source map，immutable | 公开 |
| 一元 RPC | `/api/<namespace>/<method>` | POST | Typert Remote 调用 | cookie + Host/Origin |
| 流通道 | `/api/remote.mux` | WebSocket 升级 | 所有逻辑流复用一条物理连接 | cookie + Host/Origin |
| 精确路由 | 见第 4 节 | GET/HEAD/POST | 非 JSON 响应（下载、原始上传、媒体） | cookie + Host/Origin |

其他约定：非 GET/HEAD 且未命中具名路由 → 405；越出 dist 根 → 403；响应压缩可选 gzip。
请求体默认整包缓冲（上限 300 MiB），仅显式声明流式的路由才边收边处理。

## 1. 页面配置（index 注入与启动清单）

### 1.1 加载时序

1. `GET /?token=<进程令牌>` → 校验令牌 → 写签名 cookie → 302 到干净的 `/`。
2. `GET /`：读取 `dist/index.html`，按注入表渲染后返回。
3. 注入行按 `head` / `body` 分组，插到 `<head>` / `<body>` 开标签之后；`global` 行固定进 head，
   `script-preload` 固定进 head。
4. 末尾追加结算脚本：`(globalThis.__DSH_BOOT_READY__ ??= Promise.withResolvers()).resolve()`。
5. 客户端入口等待该 deferred 后读取注入状态，装载模块表并创建 Cordis 应用。
6. 预加载（application 批次）与解析阻塞（bootstrap 批次）脚本按行顺序执行，注册插件工厂。
7. 依赖满足后 `ui-renderer` 挂载根视图；`$events` 流首帧 ready 后才算「已连接」。

### 1.2 注入行类型（共 6 种）

| kind | 字段 | 渲染 | 放置 |
|---|---|---|---|
| `global` | `name`, `value` | `<script>globalThis[<name>] = <json></script>`，JSON 中 `<` 转义 | head |
| `script` | `placement`, `text` | `<script>文本</script>` | head/body |
| `script-src` | `placement`, `src` | `<script src="..."></script>` | head/body |
| `script-preload` | `src` | `<link rel="preload" as="script" href="...">` | head |
| `style` | `text` | `<style>文本</style>` | head |
| `html` | `placement`, `html` | 原样片段 | head/body |

### 1.3 注入行贡献者

- `packages/client/connection/src/index.ts`
- `packages/client/modules/src/index.ts`
- `packages/client/ui-sidebar-documentpreview/src/index.ts`
- `packages/client/ui-theme/src/index.ts`
- `packages/experimental/inspector/src/host/plugin.ts`
- `packages/extensions/tool-cordis/src/api-catalog.ts`
- `packages/host/webserver/src/index.ts`

### 1.4 启动清单 `window.__DSH_BOOT__`

```
WebBootGraph = { rev: string, entries: WebBootEntry[], batches: WebBootBatch[] }

WebBootEntry = {
  id: string             // 包名
  url: string            // 单资源 combo 端点（HMR）
  rev: string            // 产物修订，用于缓存失效
  inject?: string[]      // 包名依赖边：决定工厂到达与插件组合顺序
  immediately?: boolean  // 第一阶段预取
  external?: string[]    // 需要解析的非基线模块说明符
}

WebBootBatch = {
  phase: 'bootstrap' | 'application'
  url: string            // combo 端点
  rev: string            // 由批次内条目修订派生
  entries: string[]      // 该脚本注册工厂的条目 id，按执行顺序
}
```

- combo URL 形如 `/plugins/??<id>/client.js,<id2>/client.js&rev=<rev>`；脚本体是各包构建产物按序
  拼接（`\n;\n` 连接）。
- 包内私有分块（`client.<name>.js`）按需再从 `/plugins/...` 取。
- 先加载 `client/modules`（bootstrap 批次），再加载 application 批次。

### 1.5 页面全局变量

| 变量 | 来源 | 用途 |
|---|---|---|
| `__DSH_BOOT__` | `client/modules` | 启动清单 |
| `__ModuleLoader__` | 引导脚本 | 工厂注册与惰性物化（queue → ready） |
| `__DSH_BOOT_READY__` | `webserver` | 客户端等待注入完成的 deferred |
| `__DSH_CONNECTION_RECOVERY__` | `connection` | 重连退避与握手超时 |
| `__DSH_DOCUMENT_PREVIEW_CONFIG__` | 文档预览插件 | 预览能力配置 |
| `__DSH_TRANSPORT__` | 仅桌面（Electron） | 仅此时设置 `streamBaseUrl`；浏览器端固定同源 |

### 1.6 认证与信任栅栏

- 每次启动生成随机进程令牌，启动打印带 `?token=` 的根 URL。
- 令牌只在 `GET /`（或配置的 index 路径）被接受，换取签名 cookie（HttpOnly、`Path=/`、
  `SameSite=Strict`，默认 30 天，绑定 host+port），之后走干净路径。
- 每个 `/api/*` 先过 Host/Origin 校验：Host 必须是 loopback 或 `trustedHosts` 命中；带 Origin 时
  必须与 Host 相等；`sec-fetch-site: cross-site` 直接拒绝。
- 校验失败 403（`forbidden`），已信任但未认证 401（`unauthorized`）。
- 静态资产不校验；`dsh web` 只允许 loopback，`--host 0.0.0.0` 被明确拒绝。

## 2. WebSocket：`/api/remote.mux`

### 2.1 帧定义

浏览器 → 宿主：

```
{ "type": "open",   "streamId": "<uuid>", "endpoint": "<namespace>/<method>", "payload": { "args": {…} } }
{ "type": "cancel", "streamId": "<uuid>" }
```

宿主 → 浏览器：

```
{ "type": "item",  "streamId": "<uuid>", "value": <任意 JSON> }
{ "type": "error", "streamId": "<uuid>", "error": { "code": …, "message": …, "details": {} } }
{ "type": "end",   "streamId": "<uuid>" }
```

### 2.2 生命周期约定

- 一条物理 WebSocket 承载所有逻辑流，按 `streamId` 复用；每条流独立取消。
- 正常结束回 `end`；业务错误回 `error` 并终止该流；物理断线由连接代际机制整体恢复。
- 宿主每心跳周期发 WebSocket Ping，连续两次 pong 缺失即终止连接。
- 客户端取消（AbortSignal）发 `cancel`；流终止后不再接收该 `streamId` 的帧。

### 2.3 内部事件流 `$events`（连接代际的唯一来源）

`endpoint = "$events"`，`payload = { args: {} }`。下行帧：

```
{ "type": "ready",     "clientId": "<id>", "host": { "home": "<宿主 home>" } }
{ "type": "emit",      "event": "<事件名>", "args": [ … ] }
{ "type": "waterfall", "event": "<事件名>", "eventId": "<id>", "agentId": "<id>", "request": { … } }
{ "type": "cancel",    "eventId": "<id>" }
```

- 首帧必须是 `ready`，客户端据此发布连接代际；此前不发起基线读取。
- `waterfall` 需客户端回写：`POST /api/$events/result`，体为
  `{ clientId, eventId, outcome: { kind:'next' } | { kind:'result', value } | { kind:'rejected', error } }`。
- 未就绪、首帧非 ready、事件畸形或流结束都会作废当前代际并重连（500ms → 1s → 2s → 4s → 8s → 10s，
  带 50%~100% 抖动）。

### 2.4 转发事件白名单（`$events` 允许的 `event`）

| 事件 | 模式 |
|---|---|
| `agent-preset/selected` | emit |
| `approval/request` | waterfall |
| `api-session/activity` | emit |
| `api-session/added` | emit |
| `api-session/error` | emit |
| `api-session/removed` | emit |
| `api-session/status` | emit |
| `commands/change` | emit |
| `credentials/reference-updated` | emit |
| `goal/activation-changed` | emit |
| `cordis/request-run` | emit |
| `cordis/request-run-resolved` | emit |
| `cordis/dynamic-package` | emit |
| `cordis/dynamic-retract` | emit |
| `cordis/inspect-query` | emit |
| `cordis/inspect-query-resolved` | emit |
| `llm/adapters-updated` | emit |
| `permission-presets/catalog-changed` | emit |
| `plugin-manager/changed` | emit |
| `plugin-manager/install-log` | emit |
| `plugin-manager/install-state` | emit |
| `settings/document-updated` | emit |
| `user-questions/request` | waterfall |

## 3. HTTP：`/api/<namespace>/<method>`

### 3.1 信封

请求：

```
POST /api/session/prompt
{ "type": "client-request", "rpcId": "<id>", "method": "session/prompt", "payload": { "args": { … } } }
```

响应：

```
{ "type": "server-response", "rpcId": "<id>", "result": { "ok": true,  "value": <JSON> } }
{ "type": "server-response", "rpcId": "<id>", "result": { "ok": false, "error": { "code": …, "message": …, "details": {} } } }
```

约定：`method` 同时出现在路径与信封；`payload` 恒为 `{ args }`；`args` 是调用方位置参数的对象投影
（参数名 → 值），末尾 `AbortSignal` 不上线；带 `agent` / `session` 查找参数的方法在线上传身份字段
（如 `sessionId`），宿主解析回对象。未注册端点 404，其余失败统一走 `ok:false` 信封
（`gateway/bad-request`、`gateway/internal`、`gateway/lookup-unavailable`、`session/not-found`、
`session/writer-held`、`settings-conflict` 等）。

### 3.2 一元端点总表（★ = 最小对话闭环涉及的命名空间）

| 命名空间 | 方法 | 备注 |
|---|---|---|
| `agentPresets` | `copy`、`deletePreset`、`list`、`read`、`select` | 来源：`packages/preset/agent-presets/src/index.ts` |
| `agentTeams` | `createTask`、`updateTask`、`view` | 来源：`packages/experimental/agent-team/src/index.ts` |
| `commands` | `execute`、`list` | 来源：`packages/interaction/commands/src/index.ts` |
| `credentials` ★ | `describe`、`set`、`unset` | 来源：`packages/api/settings-controller/src/credentials.ts` |
| `directoryPicker` | `createDirectory`、`list`、`pick` | 来源：`packages/api/workspace-controller/src/directory-picker.ts` |
| `dynamicCordisRunner` | `getClientCode`、`inventory`、`invoke`、`reportClientGuardFailure`、`reportRenderFailure`、`resolveInspectQuery`、`resolveRequestRun`、`runHostHalf`、`settleUserRun`、`stopFromPanel`、`syncInspectManifest`、`undefineFromPanel` | 来源：`packages/extensions/cordis-host-runner/src/index.ts` |
| `fileReferences` | `list` | 来源：`packages/api/session-controller/src/file-references.ts` |
| `fileUploads` | `upload` | 来源：`packages/client/file-upload/src/index.ts` |
| `goals` | `clear`、`complete`、`create`、`edit`、`get`、`pause`、`resume` | 来源：`packages/goal/goal/src/index.ts` |
| `llm` | `discoverModels`、`listConfigurableProviders`、`listProviders` | 来源：`packages/llm/llm/src/index.ts` |
| `messageFeedback` | `delete`、`list`、`put` | 来源：`packages/feedback/message-feedback/src/index.ts` |
| `officeToPdf` | `generation`、`render` | 来源：`packages/document/office-to-pdf/src/index.ts` |
| `permissionPresets` ★ | `catalog` | 来源：`packages/interaction/permission-presets/src/index.ts` |
| `pluginInventory` | `list` | 来源：`packages/host/plugin-inventory/src/index.ts` |
| `pluginManager` | `cancelInstall`、`inspect`、`installBundle`、`listBundles`、`listPlugins`、`removeBundle`、`setBundleEnabled`、`setPluginEnabled` | 来源：`packages/boot/plugin-manager/src/index.ts` |
| `session` ★ | `attachment`、`canOpenWorkspacePath`、`cancel`、`create`、`fork`、`list`、`modelCatalog`、`openWorkspacePath`、`page`、`prompt`、`rename`、`search`、`selectModel`、`updateQueue` | 来源：`packages/api/session-controller/src/index.ts` |
| `sessionFeedback` | `record` | 来源：`packages/feedback/command-feedback/src/index.ts` |
| `sessionReferenceResolver` | `candidates` | 来源：`packages/context/session-reference/src/index.ts` |
| `settings` ★ | `canOpenAgentPresetDirectory`、`describe`、`mutate`、`openAgentPresetDirectory`、`openSettingsDocument`、`replace`、`update` | 来源：`packages/api/settings-controller/src/index.ts` |
| `skills` | `list` | 来源：`packages/api/session-controller/src/skill-catalog.ts` |
| `subagents` | `interruptByParent`、`list`、`prompt` | 来源：`packages/subagent/subagent/src/index.ts` |
| `terminal` | `close`、`create`、`environment`、`list`、`rename`、`resize`、`shells`、`write` | 来源：`packages/api/terminal-controller/src/index.ts` |
| `workspace` ★ | `archiveSession`、`create`、`delete`、`insertBefore`、`insertSessionBefore`、`rename`、`unarchiveSession` | 来源：`packages/api/workspace-controller/src/index.ts` |
| `workspaceFiles` | `list`、`read`、`readAll`、`readBytes`、`readRelated`、`stat` | 来源：`packages/api/workspace-files/src/index.ts` |

### 3.3 流端点

| 流端点 | 来源 |
|---|---|
| `session/control` | `packages/api/session-controller/src/index.ts` |
| `session/follow` | `packages/api/session-controller/src/index.ts` |
| `terminal/follow` | `packages/api/terminal-controller/src/index.ts` |
| `terminal/retain` | `packages/api/terminal-controller/src/index.ts` |
| `workspace/follow` | `packages/api/workspace-controller/src/index.ts` |
| `workspaceFiles/changes` | `packages/api/workspace-files/src/index.ts` |

### 3.4 最小对话闭环的请求/响应形状

```
SessionAddress = { kind: 'session', sessionId } | { kind: 'subagent', parentSessionId, childSessionId, mode }

session/list        ← { cursor?: string }                                  → { items: SessionSummary[] }
SessionSummary      = { sessionId, updatedAt, running, blank, parentSessionId?, origin?, cwd?, projections? }
session/create      ← { workspaceId?, cwd?, sessionId?, agentPreset? }     → { sessionId, agentPreset? }
session/prompt      ← { requestId, sessionId, mode:'queue'|'steer', content: PromptContentPart[], clientTimeZone? }
                                                                          → { accepted: true }
session/cancel      ← { sessionId }                                       → { accepted: true }
session/page        ← { address, throughSeq, beforeSeq?, maxMessages? }   → { records, hasMore }
session/follow(流)   ← { address, maxMessages?, assistantStream?: true }
   帧1 { type:'snapshot', header, cursor, records, hasMore, projections, assistantStream? }
   帧n { type:'event', event:{ type, seq, time, data, surfaceOp?, sourceEventSeqs?, ignorable? } }
      { type:'assistant-stream', frame:{ type:'start'|'chunk'|'end', attemptId, revision, … } }
session/control(流)  ← {}
   帧1 { type:'baseline', value:{ jobs, projections } }
   帧n { type:'jobs', sessionId, jobs } | { type:'projection', sessionId, key, value, seq }
workspace/follow(流) ← {}
   帧1 { type:'baseline', value: … }，随后 upsert / remove / order / archived 增量
settings/describe   ← refs: string[]                                       → { writable, hasDocument, namespaces: […] }
settings/mutate     ← (namespace, operations, revision)                    → 新命名空间视图
permissionPresets/catalog → ()                                             → 预设目录
```

### 3.5 会话事件词表（`follow` / `page` 内事件的 `type`）

v1 必需子集（载荷即事件的 `data`）：

| 事件 | 载荷 | 由 javis 事件映射自 |
|---|---|---|
| `turn/start` | `{ turn }` | turn 开始 |
| `user/message` | `UserMessage`（role + content blocks） | 用户 prompt |
| `step/start` | `{ turn, step }` | 一次模型调用开始 |
| `assistant/message` | `{ turn, step, message, stream, usage?, interrupted? }` | 助手结算（`stream` 为紧凑流记录） |
| `assistant/attempt` | `{ turn, step, stream }` | 未提交消息的尝试 |
| `tool/call` | `{ turn, step, callId, name, arguments }` | `AgentToolCallStart` |
| `tool/result` | `{ turn, step, message, error?, meta? }` | `AgentToolCallResult` |
| `step/end` | `{ turn, step }` | 一步结束 |
| `turn/end` | `{ turn, reason }` | 正常 / 取消 / 失败 |

完整词表见 dsh 的 `docs/persistence-catalog.md`（`agent/*`、`approval/*`、`assistant/*`、
`compaction/*`、`deliverables/*`、`goal/*`、`llm/*`、`plan/*`、`request/*`、`session/*`、
`step/*`、`subagent/*`、`tool/*`、`turn/*`、`user/*`、`workspace/*` 等，且可 declaration merging 扩展）。

## 4. 精确 Fetch 路由（非 RPC）

| 路径 | 方法 | 来源 |
|---|---|---|
| `/api/changes.summary` | GET | `packages/client/ui-deliverables/src/present-open.ts` |
| `/api/file` | GET, HEAD | `packages/api/session-controller/src/media-references.ts` |
| `/api/present.host` | GET | `packages/client/ui-deliverables/src/present-open.ts` |
| `/api/session.export` | GET, HEAD | `packages/session-query/session-log-export/src/index.ts` |
| `/api/session/uploadFileBinary` | POST | `packages/client/file-upload/src/index.ts` |

## 5. 版本与再生成

- 本清单绑定 dsh 版本；升级后重新运行本脚本并核对：Remote 端点集合、流端点、`$events` 白名单、
  注入行贡献者、精确路由路径。
- 命令：`python scripts/inventory_dsh_web.py --dsh-root /path/to/deepseek-harness`（写入），
  `python scripts/inventory_dsh_web.py --check`（CI 漂移闸门）。
