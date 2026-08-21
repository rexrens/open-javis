"""Fixture: config validation failure surfaces as PluginConfigError."""
from pydantic import BaseModel


class Config(BaseModel):
    count: int  # required


def apply(ctx, config):
    ctx.provide("needs-config", config.count)
