"""Fixture: plain apply function."""


def apply(ctx, config):
    ctx.provide("simple-svc", config or "no-config")
