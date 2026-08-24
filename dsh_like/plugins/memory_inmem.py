from typing import List
from runtime.context import Context
from abstractions import BaseMemory, Message


class InMemoryMemory(BaseMemory):
    def __init__(self):
        self._store: dict[str, List[Message]] = {}

    async def get_messages(self, session_id: str) -> List[Message]:
        return self._store.get(session_id, []).copy()

    async def append_message(self, session_id: str, msg: Message) -> None:
        self._store.setdefault(session_id, []).append(msg)

    async def clear(self, session_id: str) -> None:
        self._store[session_id] = []

    async def delete_session(self, session_id: str) -> None:
        self._store.pop(session_id, None)


# 插件元数据
inject = []
provides = [BaseMemory]


def apply(ctx: Context):
    memory = InMemoryMemory()
    ctx.provide(BaseMemory, memory)
    print("✅ InMemoryMemory 服务已注册")

    def dispose():
        print("❌ InMemoryMemory 服务卸载")
    return dispose
