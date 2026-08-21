"""Fixture: module-level Config/inject/name + apply."""
from pydantic import BaseModel


class Config(BaseModel):
    greeting: str = "hi"


inject = ["tools"]
name = "decl-plugin"


def apply(ctx, config):
    ctx.provide("decl-svc", config.greeting)
