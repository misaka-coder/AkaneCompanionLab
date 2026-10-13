"""Bounded, instance-owned section snapshots; never persist source paths or text."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock


@dataclass(frozen=True)
class SectionSnapshot:
    text: str = field(repr=False)
    fingerprint: str
    source_stamp: str = field(repr=False)
    truncated: bool
    created_at: float


class AttachmentSectionCache:
    def __init__(self, *, max_chars: int = 8_000_000, max_entries: int = 8, ttl_seconds: float = 300.0) -> None:
        self.max_chars = max(0, int(max_chars))
        self.max_entries = max(0, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._entries: OrderedDict[tuple[str, ...], SectionSnapshot] = OrderedDict()
        self._chars = 0
        self._lock = RLock()

    def _discard(self, key: tuple[str, ...]) -> None:
        value = self._entries.pop(key, None)
        if value is not None:
            self._chars -= len(value.text)

    def _expire(self) -> None:
        now = time.monotonic()
        for key, value in list(self._entries.items()):
            if now - value.created_at >= self.ttl_seconds:
                self._discard(key)

    def get(self, key: tuple[str, ...]) -> SectionSnapshot | None:
        with self._lock:
            self._expire()
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[str, ...], value: SectionSnapshot) -> None:
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

    @private_asset_scope
    def discard_attachment(self, profile_user_id: str, session_id: str, attachment_id: str) -> None:
        prefix = (profile_user_id, session_id, attachment_id)
        with self._lock:
            for key in list(self._entries):
                if key[:3] == prefix:
                    self._discard(key)
