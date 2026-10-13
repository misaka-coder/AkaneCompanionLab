"""Small, temporary workspace text snapshots; bodies never enter repr output."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
import time
from typing import Any


@dataclass(frozen=True)
class WorkspaceReadSnapshot:
    text: str = field(repr=False)
    fingerprint: str
    source_stamp: str = field(repr=False)
    source_kind: str
    extraction_capped: bool
    created_at: float = field(default_factory=lambda: time.monotonic())

    def read_result(self) -> dict[str, Any]:
        return {
            'status': 'ok', 'content': self.text, 'source_kind': self.source_kind,
            'extraction_capped': self.extraction_capped, '_fingerprint': self.fingerprint,
        }


class WorkspaceReadCache:
    def __init__(self, *, max_chars: int = 8_000_000, max_entries: int = 8, ttl_seconds: float = 300.0):
        self.max_chars = max(0, int(max_chars))
        self.max_entries = max(0, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._entries: OrderedDict[tuple[str, ...], WorkspaceReadSnapshot] = OrderedDict()
        self._chars = 0
        self._lock = RLock()

    def _discard(self, key: tuple[str, ...]) -> None:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._chars -= len(previous.text)

    def _expire(self) -> None:
        now = time.monotonic()
        for key, value in list(self._entries.items()):
            if now - value.created_at >= self.ttl_seconds:
                self._discard(key)

    def get(self, key: tuple[str, ...]) -> WorkspaceReadSnapshot | None:
        with self._lock:
            self._expire()
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[str, ...], value: WorkspaceReadSnapshot) -> None:
        with self._lock:
            self._expire()
            self._discard(key)
            if not self.max_entries or len(value.text) > self.max_chars:
                return
            self._entries[key] = value
            self._chars += len(value.text)
            while len(self._entries) > self.max_entries or self._chars > self.max_chars:
                self._discard(next(iter(self._entries)))

    def discard(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._discard(key)
