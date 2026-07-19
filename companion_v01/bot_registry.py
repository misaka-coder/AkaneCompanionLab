"""Host-owned registry and bounded lifecycle for Akane Bot runtimes."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SAFE_BOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_REASON_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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


@dataclass(slots=True)
class _RegistryEntry:
    runtime: Any | None
    display_name: str
    state: str = "registered"
    reason: str = ""
    last_status: str = "registered"
    root_identity: Path | None = None


class BotRegistry:
    """One authority mapping Bot ids to runtime instances and lifecycle state."""

    def __init__(self, *, default_bot_id: str = "") -> None:
        self._entries: dict[str, _RegistryEntry] = {}
        self._root_identities: set[Path] = set()
        self._default_bot_id = self._require_safe_bot_id(default_bot_id) if default_bot_id else ""
        self._guard = threading.RLock()

    @property
    def default_bot_id(self) -> str:
        with self._guard:
            return self._default_bot_id

    def add(self, runtime: Any, *, default: bool = False) -> None:
        bot_id = self._require_safe_bot_id(getattr(runtime, "bot_id", ""))
        root_identity = self._runtime_root_identity(runtime)
        with self._guard:
            if bot_id in self._entries:
                raise BotRegistryError(status="conflict", reason="duplicate_bot_id", bot_id=bot_id)
            if root_identity is not None and root_identity in self._root_identities:
                raise BotRegistryError(status="conflict", reason="duplicate_data_root", bot_id=bot_id)
            self._entries[bot_id] = _RegistryEntry(
                runtime=runtime,
                display_name=self._safe_display_name(getattr(runtime, "display_name", ""), fallback=bot_id),
                root_identity=root_identity,
            )
            if root_identity is not None:
                self._root_identities.add(root_identity)
            if default or not self._default_bot_id:
                self._default_bot_id = bot_id

    def add_unavailable(
        self,
        bot_config: Any,
        *,
        reason: str,
        data_root: Path | None = None,
        default: bool = False,
    ) -> None:
        """Record a configured Bot whose runtime could not be constructed."""

        bot_id = self._require_safe_bot_id(getattr(bot_config, "bot_id", ""))
        root_identity = Path(data_root).resolve() if data_root is not None else None
        with self._guard:
            if bot_id in self._entries:
                raise BotRegistryError(status="conflict", reason="duplicate_bot_id", bot_id=bot_id)
            if root_identity is not None and root_identity in self._root_identities:
                raise BotRegistryError(status="conflict", reason="duplicate_data_root", bot_id=bot_id)
            self._entries[bot_id] = _RegistryEntry(
                runtime=None,
                display_name=self._safe_display_name(getattr(bot_config, "display_name", ""), fallback=bot_id),
                state="degraded",
                reason=self._safe_reason(reason) or "bot_runtime_unavailable",
                last_status="unavailable",
                root_identity=root_identity,
            )
            if root_identity is not None:
                self._root_identities.add(root_identity)
            if default or not self._default_bot_id:
                self._default_bot_id = bot_id

    def get(self, bot_id: str) -> Any | None:
        normalized = self._require_safe_bot_id(bot_id)
        with self._guard:
            entry = self._entries.get(normalized)
            return entry.runtime if entry is not None and entry.runtime is not None else None

    def require(self, bot_id: str) -> Any:
        normalized = self._require_safe_bot_id(bot_id)
        with self._guard:
            entry = self._entries.get(normalized)
            if entry is None:
                raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)
            if entry.runtime is None:
                raise BotRegistryError(status="unavailable", reason="bot_runtime_unavailable", bot_id=normalized)
            return entry.runtime

    def default(self) -> Any:
        with self._guard:
            default_bot_id = self._default_bot_id
        if not default_bot_id:
            raise BotRegistryError(status="unavailable", reason="default_bot_not_configured")
        return self.require(default_bot_id)

    def values(self) -> tuple[Any, ...]:
        with self._guard:
            return tuple(entry.runtime for entry in self._entries.values() if entry.runtime is not None)

    def public_snapshot(self) -> dict[str, Any]:
        with self._guard:
            bots = [self._entry_snapshot(bot_id, entry) for bot_id, entry in self._entries.items()]
            default_bot_id = self._default_bot_id
        return {
            "status": "available",
            "default_bot_id": default_bot_id,
            "count": len(bots),
            "bots": bots,
        }

    async def start(self, bot_id: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        normalized = self._require_safe_bot_id(bot_id)
        with self._guard:
            entry = self._entries.get(normalized)
            if entry is None:
                raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)
            if entry.runtime is None:
                return self._entry_snapshot(normalized, entry)
            if entry.state in {"starting", "online", "degraded", "stopping", "stopped"}:
                return self._entry_snapshot(normalized, entry)
            entry.state = "starting"
            entry.reason = ""
            entry.last_status = "starting"
            runtime = entry.runtime

        try:
            result = await asyncio.wait_for(runtime.start(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            status, state, reason = "degraded", "degraded", "bot_start_timeout"
        except Exception:
            status, state, reason = "degraded", "degraded", "bot_start_failed"
        else:
            status = str(result.get("status") or "degraded") if isinstance(result, dict) else "degraded"
            state = "online" if status == "active" else "degraded"
            reason = self._safe_reason(result.get("reason") if isinstance(result, dict) else "")
            if state == "degraded" and not reason:
                reason = "bot_start_degraded"

        with self._guard:
            current = self._entries.get(normalized)
            if current is not None:
                current.state = state
                current.reason = reason
                current.last_status = status
                return self._entry_snapshot(normalized, current)
        raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)

    async def stop(self, bot_id: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        normalized = self._require_safe_bot_id(bot_id)
        with self._guard:
            entry = self._entries.get(normalized)
            if entry is None:
                raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)
            if entry.runtime is None:
                entry.state = "stopped"
                entry.reason = "not_started"
                entry.last_status = "stopped"
                return self._entry_snapshot(normalized, entry)
            if entry.state == "stopped":
                return self._entry_snapshot(normalized, entry)
            if entry.state == "stopping":
                return self._entry_snapshot(normalized, entry)
            entry.state = "stopping"
            entry.reason = ""
            entry.last_status = "stopping"
            runtime = entry.runtime

        try:
            result = await asyncio.wait_for(runtime.stop(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            status, state, reason = "degraded", "degraded", "bot_stop_timeout"
        except Exception:
            status, state, reason = "degraded", "degraded", "bot_stop_failed"
        else:
            status = str(result.get("status") or "degraded") if isinstance(result, dict) else "degraded"
            state = "stopped" if status == "stopped" else "degraded"
            reason = self._safe_reason(result.get("reason") if isinstance(result, dict) else "")
            if state == "degraded" and not reason:
                reason = "bot_stop_degraded"

        with self._guard:
            current = self._entries.get(normalized)
            if current is not None:
                current.state = state
                current.reason = reason
                current.last_status = status
                return self._entry_snapshot(normalized, current)
        raise BotRegistryError(status="not_found", reason="bot_not_registered", bot_id=normalized)

    async def start_all(self, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        with self._guard:
            bot_ids = tuple(self._entries)
        results = await asyncio.gather(
            *(self.start(bot_id, timeout_seconds=timeout_seconds) for bot_id in bot_ids),
            return_exceptions=True,
        )
        bots = [
            result
            if isinstance(result, dict)
            else {"bot_id": bot_id, "state": "degraded", "status": "degraded", "reason": "bot_start_failed"}
            for bot_id, result in zip(bot_ids, results)
        ]
        return {
            "status": "active" if all(item.get("state") == "online" for item in bots) else "degraded",
            "count": len(bots),
            "bots": bots,
        }

    async def stop_all(self, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        with self._guard:
            bot_ids = tuple(reversed(self._entries))
        bots: list[dict[str, Any]] = []
        for bot_id in bot_ids:
            try:
                bots.append(await self.stop(bot_id, timeout_seconds=timeout_seconds))
            except Exception:
                bots.append({"bot_id": bot_id, "state": "degraded", "status": "degraded", "reason": "bot_stop_failed"})
        return {
            "status": "stopped" if all(item.get("state") == "stopped" for item in bots) else "degraded",
            "count": len(bots),
            "bots": bots,
        }

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values())

    def _entry_snapshot(self, bot_id: str, entry: _RegistryEntry) -> dict[str, Any]:
        return {
            "bot_id": bot_id,
            "display_name": entry.display_name,
            "default": bot_id == self._default_bot_id,
            "state": entry.state,
            "status": entry.last_status,
            "reason": entry.reason,
        }

    @staticmethod
    def _runtime_root_identity(runtime: Any) -> Path | None:
        layout = getattr(runtime, "runtime_layout", None)
        data_root = getattr(layout, "data_root", None)
        if data_root is None:
            return None
        try:
            return Path(data_root).resolve()
        except (OSError, ValueError):
            raise BotRegistryError(status="invalid_config", reason="invalid_data_root")

    @staticmethod
    def _safe_reason(value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        return normalized if _SAFE_REASON_PATTERN.fullmatch(normalized) is not None else "runtime_failed"

    @staticmethod
    def _safe_display_name(value: Any, *, fallback: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
            return fallback
        return normalized

    @staticmethod
    def _require_safe_bot_id(value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized or _SAFE_BOT_ID_PATTERN.fullmatch(normalized) is None:
            raise BotRegistryError(status="invalid_config", reason="invalid_bot_id")
        return normalized


__all__ = ["BotRegistry", "BotRegistryError"]
