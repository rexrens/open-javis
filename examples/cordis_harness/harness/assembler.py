"""Incremental chunk-to-message assembly.

The single canonical assembly algorithm used by the agent loop: feed stream
chunks in order, then read :meth:`blocks`, :meth:`usage` and :meth:`finish`.
Tolerant of delta-only protocols (no ``block-start``/``block-end``), like the
dsh ``BlockAssembler``.
"""

from __future__ import annotations

from typing import Any

from .types import ContentBlock, LlmFailure, FinishReason, content_block_from_partial


class BlockAssembler:
    """Assemble raw stream chunks into complete content blocks."""

    def __init__(self) -> None:
        self._order: list[int] = []
        self._partials: dict[int, dict[str, Any]] = {}
        self.usage: dict[str, int] | None = None
        self.finish: FinishReason | None = None

    # -- input --------------------------------------------------------------

    def push(self, chunk: dict[str, Any]) -> None:
        kind = chunk.get("type")
        if kind == "block-start":
            self._open(chunk["index"], chunk.get("blockType", "text"))
        elif kind == "text-delta":
            self._partial(chunk["index"], "text")["text"] += chunk.get("text", "")
        elif kind == "reasoning-delta":
            self._partial(chunk["index"], "reasoning")["text"] += chunk.get("text", "")
        elif kind == "tool-call-delta":
            partial = self._partial(chunk["index"], "tool-call")
            if chunk.get("id"):
                partial["toolCallId"] = chunk["id"]
            if chunk.get("name"):
                partial["toolCallName"] = chunk["name"]
            partial["toolCallArguments"] += chunk.get("argumentsDelta", "")
        elif kind == "block-end":
            partial = self._partial(chunk["index"], chunk.get("block", {}).get("type", "text"))
            partial["block"] = chunk.get("block")
        elif kind == "usage":
            self.usage = chunk.get("usage")
        elif kind == "finish":
            reason = chunk.get("reason") or {}
            failure = reason.get("failure")
            self.finish = FinishReason(
                kind=reason.get("kind", "stop"),
                failure=LlmFailure(**failure) if failure else None,
            )

    def _open(self, index: int, block_type: str) -> dict[str, Any]:
        if index not in self._partials:
            self._order.append(index)
            self._partials[index] = {
                "blockType": block_type,
                "text": "",
                "toolCallId": None,
                "toolCallName": None,
                "toolCallArguments": "",
                "block": None,
            }
        return self._partials[index]

    def _partial(self, index: int, block_type: str) -> dict[str, Any]:
        return self._open(index, block_type)

    # -- output -------------------------------------------------------------

    def blocks(self) -> list[ContentBlock]:
        """Every assembled block, in first-seen stream order."""
        blocks: list[ContentBlock] = []
        for index in self._order:
            partial = self._partials[index]
            if partial["block"] is not None:
                blocks.append(partial["block"])
                continue
            block = content_block_from_partial(partial)
            if block is not None:
                blocks.append(block)
        return blocks

    def has_content(self) -> bool:
        return bool(self.blocks())

    def finish_kind(self) -> str:
        return self.finish.kind if self.finish is not None else "stop"
