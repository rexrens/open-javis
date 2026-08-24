from enum import Enum
from typing import Callable, Optional
from runtime.context import Context


class FiberState(Enum):
    PENDING = "pending"
    LOADING = "loading"
    ACTIVE = "active"
    ERRORED = "errored"
    DISPOSED = "disposed"


class Fiber:
    """每个插件对应一个 Fiber 实例，承载完整生命周期状态"""
    def __init__(self, plugin: Callable, ctx: Context):
        self.plugin = plugin
        self.ctx = ctx
        self.state: FiberState = FiberState.PENDING
        self.dispose_fn: Optional[Callable[[], None]] = None
        self.error: Optional[Exception] = None

    def __repr__(self):
        return f"<Fiber plugin={self.plugin.__name__} state={self.state.value}>"
