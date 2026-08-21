"""Fixture: module-level plugin dict."""
from pydantic import BaseModel


class Config(BaseModel):
    n: int = 1


def _apply(ctx, config):
    ctx.provide("obj-svc", config.n)


plugin = {"name": "obj-plugin", "Config": Config, "apply": _apply}
