from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    acquired: bool
    reason: str
    message: str = ""


class PublicThinkGuard:
    def __init__(
        self,
        *,
        enabled: bool,
        max_concurrent_thinks: int,
        daily_think_limit: int,
        busy_message: str,
        daily_limit_message: str,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.enabled = bool(enabled)
        self.max_concurrent_thinks = max(0, int(max_concurrent_thinks))
        self.daily_think_limit = max(0, int(daily_think_limit))
        self.busy_message = str(busy_message or "当前体验人数较多，请稍后再试。").strip() or "当前体验人数较多，请稍后再试。"
        self.daily_limit_message = (
            str(daily_limit_message or "今日体验名额已满，明天再来看看吧。").strip()
            or "今日体验名额已满，明天再来看看吧。"
        )
        self._timezone = ZoneInfo(timezone_name)
        self._lock = RLock()
        self._active_thinks = 0
        self._used_today = 0
        self._day_key = self._current_day_key()

    def _current_day_key(self) -> str:
        return datetime.now(self._timezone).strftime("%Y-%m-%d")

    def _rollover_day_if_needed(self) -> None:
        current_day_key = self._current_day_key()
        if current_day_key != self._day_key:
            self._day_key = current_day_key
            self._used_today = 0

    def try_acquire(self) -> GuardDecision:
        if not self.enabled:
            return GuardDecision(allowed=True, acquired=False, reason="disabled")

        with self._lock:
            self._rollover_day_if_needed()

            if self.daily_think_limit > 0 and self._used_today >= self.daily_think_limit:
                return GuardDecision(
                    allowed=False,
                    acquired=False,
                    reason="daily_limit",
                    message=self.daily_limit_message,
                )

            if self.max_concurrent_thinks > 0 and self._active_thinks >= self.max_concurrent_thinks:
                return GuardDecision(
                    allowed=False,
                    acquired=False,
                    reason="busy",
                    message=self.busy_message,
                )

            self._active_thinks += 1
            self._used_today += 1
            return GuardDecision(allowed=True, acquired=True, reason="ok")

    def release(self) -> None:
        if not self.enabled:
            return

        with self._lock:
            self._active_thinks = max(0, self._active_thinks - 1)

    def snapshot(self) -> dict[str, int | str | bool]:
        with self._lock:
            self._rollover_day_if_needed()
            return {
                "enabled": self.enabled,
                "max_concurrent_thinks": self.max_concurrent_thinks,
                "daily_think_limit": self.daily_think_limit,
                "active_thinks": self._active_thinks,
                "used_today": self._used_today,
                "day_key": self._day_key,
            }
