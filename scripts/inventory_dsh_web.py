#!/usr/bin/env python3
"""Generate (and verify) the dsh Web interface inventory document.

The inventory is derived from a DeepSeek Harness checkout with a read-only
source scan: no file in the checkout is written. Run it whenever the dsh
version changes, then commit the regenerated document.

    python scripts/inventory_dsh_web.py --dsh-root /path/to/deepseek-harness
    python scripts/inventory_dsh_web.py --check          # drift gate

The document lives at ``docs/dsh-web-interface-inventory.md``; prose is owned
by this script so the whole file stays reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DOC_PATH = Path("docs/dsh-web-interface-inventory.md")

SERVICE_RE = re.compile(r"export\s+class\s+\w+\s+extends\s+TypertRemoteService")
SUPER_RE = re.compile(r"super\(ctx,\s*'([^']+)'")
NS_RE = re.compile(r"namespace:\s*'([^']+)'")
DECORATOR_RE = re.compile(r"@RemoteScope\(([^)]*)\)|@Remote\b(?:\(([^)]*)\))?")
METHOD_RE = re.compile(r"^\s*(?:public\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*[<(]")
CONST_RE = re.compile(r"export\s+const\s+([A-Za-z_$][\w$]*)\s*=\s*'([^']+)'")
FETCH_BLOCK_RE = re.compile(r"connection\.fetch\.register\(\{|\bfetch\.register\(\{")

#: The v1 minimum viable set, marked with a star in the generated table.
V1_NAMESPACES = frozenset(
    {"session", "workspace", "settings", "permissionPresets", "credentials"}
)


@dataclass(frozen=True)
class Endpoint:
    """One generated Remote endpoint row."""

    path: str
    mode: str
    source: str


def _source_files(root: Path) -> list[Path]:
    files = sorted((root / "packages").glob("*/*/src/**/*.ts"))
    return [
        path
        for path in files
        if "/tests/" not in path.as_posix()
        and ".spec." not in path.name
        and "/fixtures/" not in path.as_posix()
    ]


def collect_endpoints(root: Path) -> tuple[list[Endpoint], list[Endpoint]]:
    """Return (unary, stream) endpoints extracted from `@Remote` declarations."""
    unary: list[Endpoint] = []
    streams: list[Endpoint] = []
    for path in _source_files(root):
        lines = path.read_text(encoding="utf-8").splitlines()
        if not any(SERVICE_RE.search(line) for line in lines):
            continue
        namespace: str | None = None
        for line in lines:
            match = SUPER_RE.search(line)
            if match is None:
                continue
            namespace = match.group(1)
            scoped = NS_RE.search(line)
            if scoped is not None:
                namespace = scoped.group(1)
        if namespace is None:
            continue
        source = path.relative_to(root).as_posix()
        for index, line in enumerate(lines):
            match = DECORATOR_RE.search(line)
            if match is None:
                continue
            arg = (match.group(1) or match.group(2) or "").strip()
            name: str | None
            if arg.startswith("{"):
                found = re.search(r"name:\s*'([^']+)'", arg)
                name = found.group(1) if found is not None else None
                mode = "stream" if "stream" in arg else "unary"
            elif arg:
                name = arg.split(",")[0].strip().strip("'\"")
                mode = "unary"
            else:
                name = None
                mode = "unary"
            for offset in range(index + 1, min(index + 8, len(lines))):
                method = METHOD_RE.match(lines[offset])
                if method is None:
                    continue
                if name is None:
                    name = method.group(1)
                row = Endpoint(path=f"{namespace}/{name}", mode=mode, source=source)
                (streams if mode == "stream" else unary).append(row)
                break
    return sorted(unary, key=lambda row: row.path), sorted(streams, key=lambda row: row.path)


def collect_fetch_routes(root: Path) -> list[tuple[str, str, str]]:
    """Return (path, source, methods) rows for exact non-RPC fetch routes."""
    rows: list[tuple[str, str, str]] = []
    packages = sorted({path.parents[2] for path in _source_files(root)})
    for package in packages:
        sources = sorted(package.glob("src/**/*.ts"))
        # Route paths are often exported from a sibling module in the same package.
        constants: dict[str, str] = {}
        for source in sources:
            constants.update(dict(CONST_RE.findall(source.read_text(encoding="utf-8"))))
        for source in sources:
            text = source.read_text(encoding="utf-8")
            if "fetch.register(" not in text:
                continue
            relative = source.relative_to(root).as_posix()
            for match in FETCH_BLOCK_RE.finditer(text):
                block = text[match.start() : match.start() + 600]
                found = re.search(r"path:\s*(?:'([^']+)'|([A-Za-z_$][\w$]*))", block)
                if found is None:
                    continue
                route = found.group(1) or constants.get(found.group(2) or "", "")
                if not route.startswith("/api"):
                    continue
                methods = re.search(r"methods:\s*\[([^\]]*)\]", block)
                verb = (
                    ", ".join(part.strip().strip("'\"") for part in methods.group(1).split(","))
                    if methods is not None
                    else "GET"
                )
                rows.append((route, verb, relative))
    return sorted(set(rows))


def collect_forwarded_events(root: Path) -> list[tuple[str, str]]:
    """Return (event, mode) rows from the forwarded-event allowlist."""
    path = root / "packages/api/remotes/src/remote-events.ts"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    body = text.split("API_REMOTE_FORWARDED_EVENTS = [", 1)
    if len(body) != 2:
        return []
    table = body[1].split("] as const", 1)[0]
    rows = re.findall(r"event:\s*'([^']+)',\s*mode:\s*'([^']+)'", table)
    return [(event, mode) for event, mode in rows]


def collect_injection_contributors(root: Path) -> list[str]:
    """Return packages that push rows into the index injection table."""
    contributors: list[str] = []
    for path in _source_files(root):
        actions = path.read_text(encoding="utf-8")
        if "index-inject" in actions or "tapIndex(" in actions:
            contributors.append(path.relative_to(root).as_posix())
    return sorted(set(contributors))


def dsh_version(root: Path) -> tuple[str, str]:
    """Return (version, short commit) for the scanned checkout."""
    version = "unknown"
    manifest = root / "package.json"
    if manifest.exists():
        try:
            version = str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "unknown"))
        except (OSError, ValueError):
            pass
    commit = "unknown"
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return version, commit


def _marker(path: str) -> bool:
    return path in V1_NAMESPACES


def render(root: Path) -> str:
    """Render the complete inventory document for one dsh checkout."""
    unary, streams = collect_endpoints(root)
    routes = collect_fetch_routes(root)
    events = collect_forwarded_events(root)
    contributors = collect_injection_contributors(root)
    version, commit = dsh_version(root)

    by_namespace: dict[str, list[Endpoint]] = {}
    for row in unary:
        by_namespace.setdefault(row.path.split("/", 1)[0], []).append(row)

    endpoint_lines = [
        "| 命名空间 | 方法 | 备注 |",
        "|---|---|---|",
    ]
    for namespace in sorted(by_namespace):
        rows = by_namespace[namespace]
        star = " ★" if _marker(namespace) else ""
        methods = "、".join(f"`{row.path.split('/', 1)[1]}`" for row in rows)
        sources = ", ".join(sorted({row.source for row in rows}))
        endpoint_lines.append(f"| `{namespace}`{star} | {methods} | 来源：`{sources}` |")

    stream_lines = ["| 流端点 | 来源 |", "|---|---|"]
    stream_lines += [f"| `{row.path}` | `{row.source}` |" for row in streams]

    route_lines = ["| 路径 | 方法 | 来源 |", "|---|---|---|"]
    route_lines += [f"| `{route}` | {verbs} | `{source}` |" for route, verbs, source in routes]

    event_lines = ["| 事件 | 模式 |", "|---|---|"]
    event_lines += [f"| `{event}` | {mode} |" for event, mode in events]

    contributor_lines = [f"- `{path}`" for path in contributors]

    return f"""# dsh Web 接口清单（页面配置 · HTTP RPC · WebSocket 流）

> 本文件由 `scripts/inventory_dsh_web.py` 生成（`--check` 为漂移闸门）。dsh 仓库本身没有
> 这份清单；这里的内容全部来自对检出源码的只读扫描。

- 扫描的 dsh 版本：`{version}`，commit `{commit}`
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

{os.linesep.join(contributor_lines)}

### 1.4 启动清单 `window.__DSH_BOOT__`

```
WebBootGraph = {{ rev: string, entries: WebBootEntry[], batches: WebBootBatch[] }}

WebBootEntry = {{
  id: string             // 包名
  url: string            // 单资源 combo 端点（HMR）
  rev: string            // 产物修订，用于缓存失效
  inject?: string[]      // 包名依赖边：决定工厂到达与插件组合顺序
  immediately?: boolean  // 第一阶段预取
  external?: string[]    // 需要解析的非基线模块说明符
}}

WebBootBatch = {{
  phase: 'bootstrap' | 'application'
  url: string            // combo 端点
  rev: string            // 由批次内条目修订派生
  entries: string[]      // 该脚本注册工厂的条目 id，按执行顺序
}}
```

- combo URL 形如 `/plugins/??<id>/client.js,<id2>/client.js&rev=<rev>`；脚本体是各包构建产物按序
  拼接（`\\n;\\n` 连接）。
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
{{ "type": "open",   "streamId": "<uuid>", "endpoint": "<namespace>/<method>", "payload": {{ "args": {{…}} }} }}
{{ "type": "cancel", "streamId": "<uuid>" }}
```

宿主 → 浏览器：

```
{{ "type": "item",  "streamId": "<uuid>", "value": <任意 JSON> }}
{{ "type": "error", "streamId": "<uuid>", "error": {{ "code": …, "message": …, "details": {{}} }} }}
{{ "type": "end",   "streamId": "<uuid>" }}
```

### 2.2 生命周期约定

- 一条物理 WebSocket 承载所有逻辑流，按 `streamId` 复用；每条流独立取消。
- 正常结束回 `end`；业务错误回 `error` 并终止该流；物理断线由连接代际机制整体恢复。
- 宿主每心跳周期发 WebSocket Ping，连续两次 pong 缺失即终止连接。
- 客户端取消（AbortSignal）发 `cancel`；流终止后不再接收该 `streamId` 的帧。

### 2.3 内部事件流 `$events`（连接代际的唯一来源）

`endpoint = "$events"`，`payload = {{ args: {{}} }}`。下行帧：

```
{{ "type": "ready",     "clientId": "<id>", "host": {{ "home": "<宿主 home>" }} }}
{{ "type": "emit",      "event": "<事件名>", "args": [ … ] }}
{{ "type": "waterfall", "event": "<事件名>", "eventId": "<id>", "agentId": "<id>", "request": {{ … }} }}
{{ "type": "cancel",    "eventId": "<id>" }}
```

- 首帧必须是 `ready`，客户端据此发布连接代际；此前不发起基线读取。
- `waterfall` 需客户端回写：`POST /api/$events/result`，体为
  `{{ clientId, eventId, outcome: {{ kind:'next' }} | {{ kind:'result', value }} | {{ kind:'rejected', error }} }}`。
- 未就绪、首帧非 ready、事件畸形或流结束都会作废当前代际并重连（500ms → 1s → 2s → 4s → 8s → 10s，
  带 50%~100% 抖动）。

### 2.4 转发事件白名单（`$events` 允许的 `event`）

{os.linesep.join(event_lines)}

## 3. HTTP：`/api/<namespace>/<method>`

### 3.1 信封

请求：

```
POST /api/session/prompt
{{ "type": "client-request", "rpcId": "<id>", "method": "session/prompt", "payload": {{ "args": {{ … }} }} }}
```

响应：

```
{{ "type": "server-response", "rpcId": "<id>", "result": {{ "ok": true,  "value": <JSON> }} }}
{{ "type": "server-response", "rpcId": "<id>", "result": {{ "ok": false, "error": {{ "code": …, "message": …, "details": {{}} }} }} }}
```

约定：`method` 同时出现在路径与信封；`payload` 恒为 `{{ args }}`；`args` 是调用方位置参数的对象投影
（参数名 → 值），末尾 `AbortSignal` 不上线；带 `agent` / `session` 查找参数的方法在线上传身份字段
（如 `sessionId`），宿主解析回对象。未注册端点 404，其余失败统一走 `ok:false` 信封
（`gateway/bad-request`、`gateway/internal`、`gateway/lookup-unavailable`、`session/not-found`、
`session/writer-held`、`settings-conflict` 等）。

### 3.2 一元端点总表（★ = 最小对话闭环涉及的命名空间）

{os.linesep.join(endpoint_lines)}

### 3.3 流端点

{os.linesep.join(stream_lines)}

### 3.4 最小对话闭环的请求/响应形状

```
SessionAddress = {{ kind: 'session', sessionId }} | {{ kind: 'subagent', parentSessionId, childSessionId, mode }}

session/list        ← {{ cursor?: string }}                                  → {{ items: SessionSummary[] }}
SessionSummary      = {{ sessionId, updatedAt, running, blank, parentSessionId?, origin?, cwd?, projections? }}
session/create      ← {{ workspaceId?, cwd?, sessionId?, agentPreset? }}     → {{ sessionId, agentPreset? }}
session/prompt      ← {{ requestId, sessionId, mode:'queue'|'steer', content: PromptContentPart[], clientTimeZone? }}
                                                                          → {{ accepted: true }}
session/cancel      ← {{ sessionId }}                                       → {{ accepted: true }}
session/page        ← {{ address, throughSeq, beforeSeq?, maxMessages? }}   → {{ records, hasMore }}
session/follow(流)   ← {{ address, maxMessages?, assistantStream?: true }}
   帧1 {{ type:'snapshot', header, cursor, records, hasMore, projections, assistantStream? }}
   帧n {{ type:'event', event:{{ type, seq, time, data, surfaceOp?, sourceEventSeqs?, ignorable? }} }}
      {{ type:'assistant-stream', frame:{{ type:'start'|'chunk'|'end', attemptId, revision, … }} }}
session/control(流)  ← {{}}
   帧1 {{ type:'baseline', value:{{ jobs, projections }} }}
   帧n {{ type:'jobs', sessionId, jobs }} | {{ type:'projection', sessionId, key, value, seq }}
workspace/follow(流) ← {{}}
   帧1 {{ type:'baseline', value: … }}，随后 upsert / remove / order / archived 增量
settings/describe   ← refs: string[]                                       → {{ writable, hasDocument, namespaces: […] }}
settings/mutate     ← (namespace, operations, revision)                    → 新命名空间视图
permissionPresets/catalog → ()                                             → 预设目录
```

### 3.5 会话事件词表（`follow` / `page` 内事件的 `type`）

v1 必需子集（载荷即事件的 `data`）：

| 事件 | 载荷 | 由 javis 事件映射自 |
|---|---|---|
| `turn/start` | `{{ turn }}` | turn 开始 |
| `user/message` | `UserMessage`（role + content blocks） | 用户 prompt |
| `step/start` | `{{ turn, step }}` | 一次模型调用开始 |
| `assistant/message` | `{{ turn, step, message, stream, usage?, interrupted? }}` | 助手结算（`stream` 为紧凑流记录） |
| `assistant/attempt` | `{{ turn, step, stream }}` | 未提交消息的尝试 |
| `tool/call` | `{{ turn, step, callId, name, arguments }}` | `AgentToolCallStart` |
| `tool/result` | `{{ turn, step, message, error?, meta? }}` | `AgentToolCallResult` |
| `step/end` | `{{ turn, step }}` | 一步结束 |
| `turn/end` | `{{ turn, reason }}` | 正常 / 取消 / 失败 |

完整词表见 dsh 的 `docs/persistence-catalog.md`（`agent/*`、`approval/*`、`assistant/*`、
`compaction/*`、`deliverables/*`、`goal/*`、`llm/*`、`plan/*`、`request/*`、`session/*`、
`step/*`、`subagent/*`、`tool/*`、`turn/*`、`user/*`、`workspace/*` 等，且可 declaration merging 扩展）。

## 4. 精确 Fetch 路由（非 RPC）

{os.linesep.join(route_lines)}

## 5. 版本与再生成

- 本清单绑定 dsh 版本；升级后重新运行本脚本并核对：Remote 端点集合、流端点、`$events` 白名单、
  注入行贡献者、精确路由路径。
- 命令：`python scripts/inventory_dsh_web.py --dsh-root /path/to/deepseek-harness`（写入），
  `python scripts/inventory_dsh_web.py --check`（CI 漂移闸门）。
"""


def _default_root() -> Path:
    env = os.environ.get("DSH_ROOT")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "deepseek-harness"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh-root", default=str(_default_root()), help="DeepSeek Harness checkout")
    parser.add_argument("--check", action="store_true", help="verify the committed document is fresh")
    parser.add_argument("--out", default=str(DOC_PATH), help="document path")
    args = parser.parse_args(argv)

    root = Path(args.dsh_root).expanduser().resolve()
    if not (root / "packages").is_dir():
        print(f"inventory: {root} is not a dsh checkout (no packages/)", file=sys.stderr)
        return 2

    rendered = render(root)
    out = Path(args.out)
    if args.check:
        if not out.exists():
            print(f"inventory: {out} is missing; run the generator", file=sys.stderr)
            return 1
        current = out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"inventory: {out} is stale against {root}; regenerate with "
                "`python scripts/inventory_dsh_web.py`",
                file=sys.stderr,
            )
            return 1
        print(f"inventory: {out} is fresh")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"inventory: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
