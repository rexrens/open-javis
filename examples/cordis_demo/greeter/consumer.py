
name = "consumer"
inject = ["greeter"]

def apply(ctx):
    print(ctx.get("greeter").greet("world"))
