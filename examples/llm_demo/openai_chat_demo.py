#!/usr/bin/env python3
"""Interactive multi-turn chat demo for OpenAIChat."""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from javis.agent.model.model import ModelError
from javis.agent.model.openai.openai_chat import OpenAIChat
from javis.agent.model.request import Message, ModelRequest
from javis.harness.types import (
    FinishChunk,
    ReasoningDeltaChunk,
    TextDeltaChunk,
    UsageChunk,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question directly and "
    "use the conversation history as context."
)
EXIT_COMMANDS = {"exit", "quit"}


async def main() -> None:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "Missing API key. Set DEEPSEEK_API_KEY or OPENAI_API_KEY before running this demo."
        )

    model = OpenAIChat(
        model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        timeout=60,
    )
    request = ModelRequest(
        messages=[Message.system(SYSTEM_PROMPT)],
        temperature=0.7,
    )

    while True:
        try:
            user_text = await asyncio.to_thread(input, "\nYou: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        user_text = user_text.strip()
        if not user_text:
            continue
        if user_text.lower() in EXIT_COMMANDS:
            break

        request.messages.append(Message.user(user_text))
        assistant_parts: list[str] = []
        reasoning_started = False
        text_started = False

        try:
            async for chunk in model.ainvoke_stream(request):
                if isinstance(chunk, ReasoningDeltaChunk):
                    if not reasoning_started:
                        print("\nReasoning: ", end="", flush=True)
                        reasoning_started = True
                    print(chunk.text, end="", flush=True)
                elif isinstance(chunk, TextDeltaChunk):
                    if not text_started:
                        print("\nAssistant: ", end="", flush=True)
                        text_started = True
                    print(chunk.text, end="", flush=True)
                    assistant_parts.append(chunk.text)
                elif isinstance(chunk, UsageChunk):
                    usage = chunk.usage
                    print(f"\nUsage: input={usage.input_tokens}, output={usage.output_tokens}")
                elif isinstance(chunk, FinishChunk):
                    print(f"\nFinish: {chunk.reason.kind}")
        except (ModelError, httpx.HTTPError, json.JSONDecodeError) as exc:
            request.messages.pop()
            print(f"\nRequest failed: {exc}")
            continue

        assistant_text = "".join(assistant_parts)
        if assistant_text:
            request.messages.append(Message.assistant(assistant_text))


if __name__ == "__main__":
    asyncio.run(main())
