from typing import Callable, Dict, List, Set, Any
from collections import deque
from runtime.context import Context
from runtime.fiber import Fiber, FiberState


class PluginManager:
    def __init__(self, root_ctx: Context):
        self.root_ctx = root_ctx
        self.fibers: Dict[Callable, Fiber] = {}
        # 服务名 -> 提供该服务的插件
        self.service_provider: Dict[Union[str, type], Callable] = {}
        # 插件 -> 依赖的服务集合
        self.plugin_deps: Dict[Callable, Set[Union[str, type]]] = {}
        # 服务 -> 依赖它的插件集合（反向索引，用于级联卸载）
        self.rev_deps: Dict[Union[str, type], Set[Callable]] = defaultdict(set)

    def load(self, plugin: Callable[[Context], Callable[[], None]]) -> Fiber:
        """加载单个插件，异常隔离在 Fiber 中，不向外抛出"""
        if plugin in self.fibers:
            f = self.fibers[plugin]
            if f.state in (FiberState.ACTIVE, FiberState.LOADING):
                return f

        fiber = Fiber(plugin, self.root_ctx)
        self.fibers[plugin] = fiber
        fiber.state = FiberState.LOADING
        required_services: List[Union[str, type]] = getattr(plugin, "inject", [])

        try:
            # 依赖校验
            for svc in required_services:
                if svc not in self.root_ctx.services:
                    raise RuntimeError(f"插件 {plugin.__name__} 缺失依赖服务：{svc}")

            # 执行插件入口
            dispose_fn = plugin(self.root_ctx)
            fiber.dispose_fn = dispose_fn
            fiber.state = FiberState.ACTIVE

            # 记录依赖关系
            self.plugin_deps[plugin] = set(required_services)
            for svc in required_services:
                self.rev_deps[svc].add(plugin)

            # 扫描新增服务，记录提供者
            for svc_key in self.root_ctx.services:
                if svc_key not in self.service_provider:
                    self.service_provider[svc_key] = plugin

        except Exception as e:
            fiber.error = e
            fiber.state = FiberState.ERRORED
        return fiber

    def unload(self, plugin: Callable[[Context], Callable[[], None]]):
        """卸载插件 + 级联卸载所有依赖它的插件"""
        if plugin not in self.fibers:
            return
        if self.fibers[plugin].state == FiberState.DISPOSED:
            return

        # DFS 收集所有需要级联卸载的插件
        cascade: Set[Callable] = set()

        def _dfs(p: Callable):
            if p in cascade:
                return
            cascade.add(p)
            provided = [k for k, v in self.service_provider.items() if v == p]
            for svc in provided:
                for dep_plugin in self.rev_deps.get(svc, set()):
                    _dfs(dep_plugin)

        _dfs(plugin)

        # 逆序卸载：先卸依赖者，后卸提供者
        for p in reversed(list(cascade)):
            if p not in self.fibers:
                continue
            f = self.fibers[p]
            if f.state == FiberState.DISPOSED:
                continue

            if f.dispose_fn is not None:
                try:
                    f.dispose_fn()
                except Exception:
                    pass
            f.state = FiberState.DISPOSED

            # 清理服务与索引
            provided = [k for k, v in self.service_provider.items() if v == p]
            for svc in provided:
                if svc in self.root_ctx.services and self.service_provider.get(svc) == p:
                    del self.root_ctx.services[svc]
                if svc in self.rev_deps:
                    del self.rev_deps[svc]
                del self.service_provider[svc]
            if p in self.plugin_deps:
                del self.plugin_deps[p]

    # ---------- 拓扑排序 ----------
    def _build_dag(self, plugin_list: List[Callable]) -> tuple[Dict[Callable, Set[Callable]], Dict[Callable, int]]:
        """构建插件间依赖DAG：服务提供者 -> 服务消费者"""
        temp_provider: Dict[Union[str, type], Callable] = {}
        for p in plugin_list:
            exported = getattr(p, "provides", [])
            for svc in exported:
                if svc not in temp_provider:
                    temp_provider[svc] = p

        adj: Dict[Callable, Set[Callable]] = {p: set() for p in plugin_list}
        in_degree: Dict[Callable, int] = {p: 0 for p in plugin_list}

        for consumer in plugin_list:
            req_svcs = getattr(consumer, "inject", [])
            for svc in req_svcs:
                if svc not in temp_provider:
                    continue  # 外部预置服务，不参与排序
                provider = temp_provider[svc]
                if provider == consumer:
                    continue
                if consumer not in adj[provider]:
                    adj[provider].add(consumer)
                    in_degree[consumer] += 1
        return adj, in_degree

    def sort_plugins_topology(self, plugin_list: List[Callable]) -> List[Callable]:
        adj, in_degree = self._build_dag(plugin_list)
        q = deque([p for p in plugin_list if in_degree[p] == 0])
        result: List[Callable] = []

        while q:
            cur = q.popleft()
            result.append(cur)
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    q.append(nxt)

        if len(result) != len(plugin_list):
            unprocessed = set(plugin_list) - set(result)
            names = [p.__name__ for p in unprocessed]
            raise RuntimeError(f"检测到循环依赖！无法解析的插件：{names}")
        return result

    def load_all(self, plugin_list: List[Callable]) -> List[Fiber]:
        """自动拓扑排序后批量加载插件"""
        order = self.sort_plugins_topology(plugin_list)
        fibers = []
        for p in order:
            fib = self.load(p)
            fibers.append(fib)
            if fib.state == FiberState.ERRORED and fib.error is not None:
                print(f"⚠️ 插件 {p.__name__} 加载失败: {fib.error}")
        return fibers
