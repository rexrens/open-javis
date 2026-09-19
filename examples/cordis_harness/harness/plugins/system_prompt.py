"""Provide ``ctx.systemPrompt`` with the built-in prompt sections."""

from __future__ import annotations

from harness.system_prompt import SystemPromptService, default_sections

name = "system-prompt"


def apply(ctx):
    service = SystemPromptService(ctx)
    ctx.provide("systemPrompt", service)
    return [service.register_section(section_name, render) for section_name, render in default_sections()]
