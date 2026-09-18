import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrontendRequest:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""

async def producer(queue: asyncio.Queue[FrontendRequest]) -> None:
    """模拟前端不断发请求。"""
    for i in range(5):
        req = FrontendRequest(
            kind="chat",
            payload={"text": f"hello {i}"},
            request_id=f"req-{i}",
        )
        await queue.put(req)          # 写：队列满时会挂起等待
        print(f"[producer] 已写入 {req.request_id}")
        await asyncio.sleep(5)      # 模拟间隔

    # 发一个结束信号，方便消费者退出
    await queue.put(FrontendRequest(kind="__stop__"))
    print("[producer] 已写入停止信号")

async def consumer(queue: asyncio.Queue[FrontendRequest]) -> None:
    """模拟后端逐个处理请求。"""
    while True:
        req = await queue.get()       # 读：队列空时会挂起等待
        try:
            if req.kind == "__stop__":
                print("[consumer] 收到停止信号，退出")
                return

            print(f"[consumer] 处理 {req.request_id}: {req.payload}")
            await asyncio.sleep(0.5)  # 模拟处理耗时
        finally:
            queue.task_done()         # 告诉队列这一项处理完了

async def main() -> None:
    # 这就是你问的那行：带类型标注的 asyncio.Queue
    queue: asyncio.Queue[FrontendRequest] = asyncio.Queue()

    # 并发跑生产者和消费者
    await asyncio.gather(
        producer(queue),
        consumer(queue),
    )

    # 如果还有 join() 需求，可以等所有 task_done 被调用
    await queue.join()
    print("[main] 队列已清空，全部处理完毕")

if __name__ == "__main__":
    asyncio.run(main())