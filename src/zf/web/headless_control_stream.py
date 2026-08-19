"""Separate headless control envelopes from visible assistant deltas."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_CONTROL_KEY_PATTERN = re.compile(
    r"[\"']?(plan_request|action_proposal)[\"']?\s*:",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ControlStreamBatch:
    visible_text: tuple[str, ...] = ()
    preparing: tuple[str, ...] = ()


class HeadlessControlStreamFilter:
    """Buffer fenced blocks until they can be classified as UI control."""

    def __init__(self) -> None:
        self._buffer = ""
        self._announced: set[str] = set()

    def feed(self, content: str) -> ControlStreamBatch:
        if not content:
            return ControlStreamBatch()
        self._buffer += content
        visible: list[str] = []
        preparing: list[str] = []

        while self._buffer:
            fence_start = self._buffer.find("```")
            if fence_start < 0:
                safe_length = len(self._buffer) - self._trailing_backticks()
                if safe_length:
                    visible.append(self._buffer[:safe_length])
                    self._buffer = self._buffer[safe_length:]
                break
            if fence_start:
                visible.append(self._buffer[:fence_start])
                self._buffer = self._buffer[fence_start:]

            opener_end = self._buffer.find("\n", 3)
            if opener_end < 0:
                break
            fence_end = self._buffer.find("```", opener_end + 1)
            if fence_end < 0:
                break
            block_end = fence_end + 3
            block = self._buffer[:block_end]
            self._buffer = self._buffer[block_end:]
            control_kind = _control_kind(block, opener_end)
            if not control_kind:
                visible.append(block)
                continue
            if control_kind not in self._announced:
                self._announced.add(control_kind)
                preparing.append(control_kind)

        return ControlStreamBatch(tuple(visible), tuple(preparing))

    def _trailing_backticks(self) -> int:
        count = len(self._buffer) - len(self._buffer.rstrip("`"))
        return min(count, 2)


def _control_kind(block: str, opener_end: int) -> str:
    body = block[opener_end + 1:-3].strip()
    try:
        decoded: Any = json.loads(body)
    except (TypeError, ValueError):
        match = _CONTROL_KEY_PATTERN.search(body)
        return match.group(1).lower() if match else ""
    if not isinstance(decoded, dict):
        return ""
    if "plan_request" in decoded:
        return "plan_request"
    if "action_proposal" in decoded:
        return "action_proposal"
    return ""
