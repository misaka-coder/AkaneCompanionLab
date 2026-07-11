from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from typing import Any, Hashable


@dataclass(frozen=True)
class TTLCacheLookup:
    hit: bool
    value: Any = None
    negative: bool = False
    stored_at: float = 0.0
    expires_at: float = 0.0


@dataclass(frozen=True)
class _TTLCacheEntry:
    value: Any
    negative: bool
    stored_at: float
    expires_at: float


class TTLMarketDataCache:
    """Small thread-safe LRU/TTL cache for immutable normalized market responses."""

    def __init__(self, *, max_entries: int = 256, clock=time.monotonic) -> None:
        if isinstance(max_entries, bool):
            raise ValueError("max_entries must be an integer")
        self.max_entries = max(1, min(4096, int(max_entries)))
        self._clock = clock
        self._entries: OrderedDict[Hashable, _TTLCacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: Hashable) -> TTLCacheLookup:
        _require_hashable(key)
        now = float(self._clock())
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                return TTLCacheLookup(hit=False)
            self._entries.move_to_end(key)
            return TTLCacheLookup(
                hit=True,
                value=entry.value,
                negative=entry.negative,
                stored_at=entry.stored_at,
                expires_at=entry.expires_at,
            )

    def set(self, key: Hashable, value: Any, *, ttl_seconds: float, negative: bool = False) -> None:
        _require_hashable(key)
        ttl = float(ttl_seconds)
        if not 0 < ttl <= 24 * 60 * 60:
            raise ValueError("ttl_seconds must be greater than zero and at most one day")
        now = float(self._clock())
        entry = _TTLCacheEntry(
            value=value,
            negative=bool(negative),
            stored_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._purge_expired_locked(now)
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: Hashable) -> bool:
        _require_hashable(key)
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        now = float(self._clock())
        with self._lock:
            self._purge_expired_locked(now)
            return len(self._entries)

    def _purge_expired_locked(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def _require_hashable(key: Hashable) -> None:
    try:
        hash(key)
    except TypeError as exc:
        raise TypeError("cache key must be hashable") from exc


__all__ = ["TTLCacheLookup", "TTLMarketDataCache"]
