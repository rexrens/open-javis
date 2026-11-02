name = "reporter"
inject = ["stats"]


def apply(ctx):
    def report(name, count):
        print(f"[stats] {name} -> {count}")

    ctx.on("stats/report", report)
    ctx.get("stats").bump("tool_call")
    ctx.get("stats").bump("tool_call")
    ctx.get("stats").bump("prompt")
    ctx.get("stats").bump("thinking")
    ctx.get("stats").bump("thinking")
    ctx.get("stats").bump("thinking")
    ctx.get("stats").bump("thinking")
