"""Tutorial chapter 3: providing and consuming a service."""

name = "greeter"


class GreeterService:
    def __init__(self, ctx):
        self.ctx = ctx
        ctx.provide("greeter", self)

    def greet(self, who):
        return f"Hello, {who}!"


def apply(ctx):
    ctx.plugin(GreeterService)
