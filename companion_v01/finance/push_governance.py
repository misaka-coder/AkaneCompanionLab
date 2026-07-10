from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

from .importance_policy import ImportanceDecision


PUSH_GOVERNANCE_ACTIONS = ("deliver", "defer", "skip")
PUSH_DELIVERY_MODES = ("immediate", "cluster", "digest")
_LEVEL_RANK = {"archive": 0, "digest": 1, "notify": 2, "alert": 3}


@dataclass(frozen=True)
class FinancePushGovernanceDecision:
    action: str
    delivery_mode: str
    available_at: int
    reason: str
    bypassed: bool = False

    def __post_init__(self) -> None:
        if self.action not in PUSH_GOVERNANCE_ACTIONS:
            raise ValueError(f"unsupported push governance action: {self.action}")
        if self.delivery_mode not in PUSH_DELIVERY_MODES:
            raise ValueError(f"unsupported push delivery mode: {self.delivery_mode}")
        object.__setattr__(self, "available_at", max(0, int(self.available_at)))

    def to_public_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "delivery_mode": self.delivery_mode,
            "available_at": self.available_at,
            "reason": self.reason,
            "bypassed": self.bypassed,
        }


class FinancePushGovernancePolicy:
    """Plans user-visible proactive delivery without slowing event ingestion.

    The policy only controls when a persisted event may be pushed. Alert events
    bypass cluster waiting, quiet hours and normal notification pacing.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        cluster_coalesce_seconds: int = 0,
        cluster_max_wait_seconds: int = 0,
        digest_enabled: bool = False,
        digest_interval_seconds: int = 30 * 60,
        min_interval_seconds: int = 0,
        rate_window_seconds: int = 5 * 60,
        max_notifications_per_window: int = 0,
        quiet_hours_enabled: bool = False,
        quiet_start: str = "23:00",
        quiet_end: str = "07:00",
        timezone: str = "Asia/Shanghai",
    ) -> None:
        self.enabled = bool(enabled)
        self.cluster_coalesce_seconds = max(0, min(30 * 60, int(cluster_coalesce_seconds)))
        configured_max_wait = max(0, min(2 * 60 * 60, int(cluster_max_wait_seconds)))
        self.cluster_max_wait_seconds = max(self.cluster_coalesce_seconds, configured_max_wait)
        self.digest_enabled = bool(digest_enabled)
        self.digest_interval_seconds = max(5 * 60, min(24 * 60 * 60, int(digest_interval_seconds)))
        self.min_interval_seconds = max(0, min(60 * 60, int(min_interval_seconds)))
        self.rate_window_seconds = max(60, min(24 * 60 * 60, int(rate_window_seconds)))
        self.max_notifications_per_window = max(0, min(1000, int(max_notifications_per_window)))
        self.quiet_hours_enabled = bool(quiet_hours_enabled)
        self.quiet_start = _parse_clock_time(quiet_start, fallback="23:00")
        self.quiet_end = _parse_clock_time(quiet_end, fallback="07:00")
        try:
            self.timezone = ZoneInfo(str(timezone or "Asia/Shanghai"))
        except Exception:
            self.timezone = ZoneInfo("Asia/Shanghai")

    def plan_initial(
        self,
        *,
        importance: ImportanceDecision,
        now_ts: int,
    ) -> FinancePushGovernanceDecision:
        now = int(now_ts)
        if not self._eligible(importance):
            return FinancePushGovernanceDecision(
                action="skip",
                delivery_mode="immediate",
                available_at=now,
                reason="below_subscription_threshold",
            )
        if importance.level == "alert":
            return FinancePushGovernanceDecision(
                action="deliver",
                delivery_mode="immediate",
                available_at=now,
                reason="alert_bypass",
                bypassed=True,
            )
        if not self.enabled:
            return FinancePushGovernanceDecision(
                action="deliver" if importance.should_deliver else "skip",
                delivery_mode="immediate",
                available_at=now,
                reason="governance_disabled",
            )

        reasons: list[str] = []
        if importance.level == "digest":
            if not self.digest_enabled:
                return FinancePushGovernanceDecision(
                    action="skip",
                    delivery_mode="digest",
                    available_at=now,
                    reason="digest_disabled",
                )
            delivery_mode = "digest"
            available_at = self._next_digest_at(now)
            reasons.append("digest_window")
        else:
            delivery_mode = "cluster" if self.cluster_coalesce_seconds > 0 else "immediate"
            available_at = now + self.cluster_coalesce_seconds
            if self.cluster_coalesce_seconds > 0:
                reasons.append("cluster_coalesce")

        quiet_until = self._quiet_until(now)
        if quiet_until > available_at:
            available_at = quiet_until
            reasons.append("quiet_hours")
        return FinancePushGovernanceDecision(
            action="defer" if available_at > now else "deliver",
            delivery_mode=delivery_mode,
            available_at=available_at,
            reason="+".join(reasons) or "ready",
        )

    def plan_runtime(
        self,
        *,
        importance: ImportanceDecision,
        delivery_mode: str,
        now_ts: int,
        recent_delivery_times: tuple[int, ...] = (),
    ) -> FinancePushGovernanceDecision:
        now = int(now_ts)
        clean_mode = delivery_mode if delivery_mode in PUSH_DELIVERY_MODES else "immediate"
        if importance.level == "alert":
            return FinancePushGovernanceDecision(
                action="deliver",
                delivery_mode=clean_mode,
                available_at=now,
                reason="alert_bypass",
                bypassed=True,
            )
        if not self.enabled:
            return FinancePushGovernanceDecision(
                action="deliver",
                delivery_mode=clean_mode,
                available_at=now,
                reason="governance_disabled",
            )

        available_at = now
        reasons: list[str] = []
        quiet_until = self._quiet_until(now)
        if quiet_until > available_at:
            available_at = quiet_until
            reasons.append("quiet_hours")

        clean_times = tuple(sorted(max(0, int(item)) for item in recent_delivery_times if int(item) > 0))
        if clean_times and self.min_interval_seconds > 0:
            paced_at = clean_times[-1] + self.min_interval_seconds
            if paced_at > available_at:
                available_at = paced_at
                reasons.append("minimum_interval")
        if self.max_notifications_per_window > 0 and len(clean_times) >= self.max_notifications_per_window:
            window_at = clean_times[-self.max_notifications_per_window] + self.rate_window_seconds
            if window_at > available_at:
                available_at = window_at
                reasons.append("rate_window")

        return FinancePushGovernanceDecision(
            action="defer" if available_at > now else "deliver",
            delivery_mode=clean_mode,
            available_at=available_at,
            reason="+".join(reasons) or "ready",
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "cluster_coalesce_seconds": self.cluster_coalesce_seconds,
            "cluster_max_wait_seconds": self.cluster_max_wait_seconds,
            "digest_enabled": self.digest_enabled,
            "digest_interval_seconds": self.digest_interval_seconds,
            "min_interval_seconds": self.min_interval_seconds,
            "rate_window_seconds": self.rate_window_seconds,
            "max_notifications_per_window": self.max_notifications_per_window,
            "quiet_hours_enabled": self.quiet_hours_enabled,
            "quiet_start": self.quiet_start.strftime("%H:%M"),
            "quiet_end": self.quiet_end.strftime("%H:%M"),
            "timezone": str(self.timezone.key),
        }

    def _eligible(self, importance: ImportanceDecision) -> bool:
        minimum = importance.minimum_level
        if minimum == "archive":
            return False
        level_rank = _LEVEL_RANK.get(importance.level, 0)
        minimum_rank = _LEVEL_RANK.get(minimum, _LEVEL_RANK["notify"])
        if importance.level == "digest" and self.digest_enabled and minimum == "notify":
            return True
        return importance.level != "archive" and level_rank >= minimum_rank

    def _next_digest_at(self, now_ts: int) -> int:
        current = datetime.fromtimestamp(now_ts, tz=self.timezone)
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = max(0, int((current - midnight).total_seconds()))
        next_slot = ((elapsed // self.digest_interval_seconds) + 1) * self.digest_interval_seconds
        return int((midnight + timedelta(seconds=next_slot)).timestamp())

    def _quiet_until(self, now_ts: int) -> int:
        if not self.quiet_hours_enabled or self.quiet_start == self.quiet_end:
            return now_ts
        current = datetime.fromtimestamp(now_ts, tz=self.timezone)
        current_time = current.timetz().replace(tzinfo=None)
        overnight = self.quiet_start > self.quiet_end
        if overnight:
            inside = current_time >= self.quiet_start or current_time < self.quiet_end
        else:
            inside = self.quiet_start <= current_time < self.quiet_end
        if not inside:
            return now_ts
        end_date = current.date()
        if overnight and current_time >= self.quiet_start:
            end_date += timedelta(days=1)
        quiet_end = datetime.combine(end_date, self.quiet_end, tzinfo=self.timezone)
        return int(quiet_end.timestamp())


def _parse_clock_time(value: str, *, fallback: str) -> datetime_time:
    raw = str(value or "").strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except (TypeError, ValueError):
        parsed = datetime.strptime(fallback, "%H:%M")
    return datetime_time(hour=parsed.hour, minute=parsed.minute)
