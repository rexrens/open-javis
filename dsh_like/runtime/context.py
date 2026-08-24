from typing import Callable, Dict, List, Any, Awaitable, Union
from collections import defaultdict
import asyncio


class Context:
    def __init__(self):
        # 服务容器：服务名/抽象类 -> 实例
        self.services: Dict[Union[str, type], Any] = {}
        # 清理回调栈
        self._disposers: List[Callable[[], None]] = []
        # 普通广播事件处理器
        self._emit_handlers: Dict[str, List[Callable[..., Union[Any, Awaitable[Any]]]]] = defaultdict(list)
        # waterfall 瀑布链式处理器
        self._waterfall_handlers: Dict[str, List[Callable[..., Union[Any, Awaitable[Any]]]]] = defaultdict(list)

    # ---------- 副作用管理 ----------
    def register_dispose(self, fn: Callable[[], None]):
        self._disposers.append(fn)

    # ---------- 服务注册 ----------
    def provide(self, key: Union[str, type], obj: Any):
        """注册服务，支持字符串名或抽象类作为key"""
        self.services[key] = obj

    # ---------- 普通广播事件 ----------
    def on(self, event_name: str, handler: Callable):
        """注册普通事件监听器"""
        self._emit_handlers[event_name].append(handler)

        def off():
            try:
                self._emit_handlers[event_name].remove(handler)
            except ValueError:
                pass
        self.register_dispose(off)

    async def async_emit(self, event_name: str, *args, **kwargs):
        """异步广播事件，等待所有 handler 执行完成"""
        handlers = self._emit_handlers.get(event_name, [])
        if not handlers:
            return
        tasks = []
        for h in handlers:
            res = h(*args, **kwargs)
            if asyncio.iscoroutine(res):
                tasks.append(res)
            else:
                async def _wrap(fn=h):
                    return fn(*args, **kwargs)
                tasks.append(_wrap())
        await asyncio.gather(*tasks)

    # ---------- waterfall 瀑布事件 ----------
    def waterfall_on(self, event_name: str, handler: Callable):
        """注册瀑布链式处理器"""
        self._waterfall_handlers[event_name].append(handler)

        def off():
            try:
                self._waterfall_handlers[event_name].remove(handler)
            except ValueError:
                pass
        self.register_dispose(off)

    async def waterfall(self, event_name: str, initial_data: Any) -> Union[Any, bool]:
        """
        执行瀑布链：依次调用handler，返回值向下传递
        返回 False 表示被中断，否则返回最终处理后的数据
        """
        handlers = self._waterfall_handlers.get(event_name, [])
        value = initial_data
        for handler in handlers:
            ret = handler(value)
            if asyncio.iscoroutine(ret):
                ret = await ret
            if ret is False:
                return False
            if ret is not None:
                value = ret
        return value

    # ---------- 上下文销毁 ----------
    def dispose(self):
        while self._disposers:
            fn = self._disposers.pop()
            try:
                fn()
            except Exception:
                pass
