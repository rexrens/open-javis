"""Fixture: one file exporting two plugins."""
from pydantic import BaseModel


class CfgA(BaseModel):
    tag: str = "a"


class CfgB(BaseModel):
    tag: str = "b"


def apply_a(ctx, config):
    ctx.provide("multi-a", config.tag)


def apply_b(ctx, config):
    ctx.provide("multi-b", config.tag)


class PluginA:
    name = "multi-a"
    Config = CfgA
    apply = staticmethod(apply_a)


class PluginB:
    name = "multi-b"
    Config = CfgB
    apply = staticmethod(apply_b)


__plugins__ = [PluginA, PluginB]
