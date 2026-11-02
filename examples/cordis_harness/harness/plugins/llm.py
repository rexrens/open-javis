"""Provide ``ctx.llm``: the provider-neutral model-call service."""

from __future__ import annotations

from harness.llm import LlmService

name = "llm"


def apply(ctx):
    ctx.provide("llm", LlmService(ctx))
