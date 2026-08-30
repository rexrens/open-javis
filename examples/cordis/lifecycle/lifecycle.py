"""Tutorial chapter 2: lifecycle and effects."""

import asyncio

name = "lifecycle-demo"


def heartbeat(ctx):
    print("heartbeat plugin loading")

    def execute():
        async def tick():
            while True:
                print("tick")
                await asyncio.sleep(0.2)

        task = asyncio.ensure_future(tick())

        def cleanup():
            task.cancel()
            print("heartbeat cleaned up")

        return cleanup

    ctx.effect(execute)


def apply(ctx):
    fiber = ctx.plugin(heartbeat)

    def execute():
        async def body():
            await asyncio.sleep(0.7)
            await fiber.dispose()
            print("disposed")
            # Graceful exit: plugins may emit `app/exit`; the CLI listens.
            ctx.emit("app/exit", 0)

        task = asyncio.ensure_future(body())

        def cleanup():
            task.cancel()

        return cleanup

    ctx.effect(execute)
