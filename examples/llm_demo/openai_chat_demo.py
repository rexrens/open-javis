#!/usr/bin/env python3
"""Minimal OpenAIChat demo using DeepSeek's OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

from javis.agent.model.openai.openai_chat import OpenAIChat

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_PROMPT = (
    '请用 JSON 返回一个三步学习计划。格式必须是 {"topic": "...", "steps": ["...", "...", "..."]}。'
)


@dataclass
class AssistantMessage:
    """Optional output sink accepted by OpenAIChat's signature."""

    role: str | None = None
    content: Any = None
    parsed: Any = None
    reasoning_content: str | None = None
    provider_data: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    time_to_first_token: float | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument("--stream", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Missing API key. Set DEEPSEEK_API_KEY before running this demo.")

    model = OpenAIChat(
        id=args.model,
        api_key=api_key,
        base_url=args.base_url,
        temperature=0.2,
        max_tokens=512,
        role_map={"system": "system"},
    )
    messages = [
        {"role": "system", "content": "你是一个简洁、可靠的助手。"},
        {"role": "user", "content": args.prompt},
    ]
    output = AssistantMessage()

    if args.stream:
        print("stream: ", end="", flush=True)
        for chunk in model.invoke_stream(messages, output):
            if chunk.block_type == "content_delta" and chunk.content:
                print(chunk.content, end="", flush=True)
        print()
    else:
        result = model.invoke(
            messages,
            output,
            response_format={"type": "json_object"},
        )
        print("response.parsed:")
        print(json.dumps(result.parsed, ensure_ascii=False, indent=2))

    print("\nassistant_message:")
    print(
        json.dumps(
            {
                "content": output.content,
                "input_tokens": output.input_tokens,
                "output_tokens": output.output_tokens,
                "total_tokens": output.total_tokens,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
