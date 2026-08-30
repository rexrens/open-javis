"""Tutorial chapter 4: an event-emitting service and its reporter."""

name = "stats"


class StatsService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.counts = {}
        ctx.provide("stats", self)

    def bump(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1
        self.ctx.emit("stats/report", name, self.counts[name])


def apply(ctx):
    ctx.plugin(StatsService)
