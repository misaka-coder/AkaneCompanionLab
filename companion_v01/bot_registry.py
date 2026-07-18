"""Host-owned registry for configured Akane Bot runtimes."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any


_SAFE_BOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BotRegistryError(RuntimeError):
    def __init__(self, *, status: str, reason: str, bot_id: str = "") -> None:
        self.status = str(status or "error")
        self.reason = str(reason or "bot_registry_error")
        self.bot_id = str(bot_id or "")
        super().__init__(self.reason)

    def as_dict(self) -> dict[str, str]:
        payload = {"status": self.status, "reason": self.reason}
        if self.bot_id:
            payload["bot_id"] = self.bot_id
        return payload


class BotRegistry:
    """One authority mapping from bot id to runtime.

    Slice 1 registers only the compatibility/default Bot. Later slices can add
    more runtimes without changing callers or creating Bot-specific classes.
    """

    def __init__(self, *, default_bot_id: str = "") -> None:
        self._runtimes: dict[str, Any] = {}
        self._default_bot_id = self._require_safe_bot_id(default_bot_id) if default_bot_id else ""

    @property
    def default_bot_id(self) -> str:
        return self._default_bot_id

    def add(self, runtime: Any, *, default: bool = False) -> None:
        bot_id = self._require_safe_bot_id(getattr(runtime, "bot_id", ""))
        if bot_id in self._runtimes:
            raise BotRegistryError(status="conflict", reason="duplicate_bot_id", bot_id=bot_id)
        self._runtimes[bot_id] = runtime
        if default or not self._default_bot_id:
            self._default_bot_id = bot_id

    def get(self, bot_id: str) -> Any | None:
        normalized = self._require_safe_bot_id(bot_id)
        return self._runtimes.get(normalized)

    def require(self, bot_id: str) -> Any:
        normalized = self._require_safe_bot_id(bot_id)
        runtime = self._runtimes.get(normalized)
        if runtime is None:
            raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)
        return runtime

    def default(self) -> Any:
        if not self._default_bot_id:
            raise BotRegistryError(status="unavailable", reason="default_bot_not_configured")
        return self.require(self._default_bot_id)

    def values(self) -> tuple[Any, ...]:
        return tuple(self._runtimes.values())

    def __len__(self) -> int:
        return len(self._runtimes)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values())

    @staticmethod
    def _require_safe_bot_id(value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized or _SAFE_BOT_ID_PATTERN.fullmatch(normalized) is None:
            raise BotRegistryError(status="invalid_config", reason="invalid_bot_id")
        return normalized


__all__ = ["BotRegistry", "BotRegistryError"]
