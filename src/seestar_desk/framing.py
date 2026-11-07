"""Newline-delimited JSON used on the Seestar control port."""

from __future__ import annotations

import json
from typing import Any


def dumps(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\r\n").encode("utf-8")


class LineBuffer:
    """Collect socket bytes and return each complete JSON object."""

    def __init__(self, max_line: int = 1_048_576) -> None:
        self._buf = bytearray()
        self.max_line = max_line

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            newline = self._buf.find(b"\n")
            if newline < 0:
                if len(self._buf) > self.max_line:
                    self._buf.clear()
                break
            raw = bytes(self._buf[:newline]).strip()
            del self._buf[: newline + 1]
            if not raw:
                continue
            messages.append(json.loads(raw))
        return messages

    def pending(self) -> int:
        return len(self._buf)
