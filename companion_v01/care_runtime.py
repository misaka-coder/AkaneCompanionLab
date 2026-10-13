"""Stable care entry point composed from static, shared-authority modules."""

from __future__ import annotations

import logging
import random as random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .care_catalog import (
    _LUNAR_DATES as _LUNAR_DATES,
)
from .care_catalog import (
    _LUNAR_DATES_MAX_YEAR as _LUNAR_DATES_MAX_YEAR,
)
from .care_catalog import (
    _SEASONAL_EVENTS as _SEASONAL_EVENTS,
)
from .care_catalog import (
    DEFAULT_CARE_SHOP_ITEMS as DEFAULT_CARE_SHOP_ITEMS,
)
from .care_catalog import (
    DEFAULT_CHECKIN_COINS as DEFAULT_CHECKIN_COINS,
)
from .care_catalog import (
    DEFAULT_ENERGY_RECOVERY_PER_HOUR as DEFAULT_ENERGY_RECOVERY_PER_HOUR,
)
from .care_catalog import (
    DEFAULT_THRESHOLDS as DEFAULT_THRESHOLDS,
)
from .care_catalog import (
    DESKTOP_AUTHORITY_VERSION as DESKTOP_AUTHORITY_VERSION,
)
from .care_catalog import (
    MAX_CARE_EVENT_LEDGER as MAX_CARE_EVENT_LEDGER,
)
from .care_catalog import (
    MAX_DESKTOP_INVENTORY_ITEMS as MAX_DESKTOP_INVENTORY_ITEMS,
)
from .care_catalog import (
    _in_lunar_window as _in_lunar_window,
)
from .care_catalog import (
    get_seasonal_shop_items as get_seasonal_shop_items,
)
from .care_config import (
    _bounded_int as _bounded_int,
)
from .care_config import (
    _coerce_positive_int as _coerce_positive_int,
)
from .care_config import (
    _initial_desktop_state as _initial_desktop_state,
)
from .care_config import (
    _is_qq_mode as _is_qq_mode,
)
from .care_config import (
    _normalize_work_task as _normalize_work_task,
)
from .care_config import (
    _safe_key as _safe_key,
)
from .care_config import (
    normalize_desktop_care_config as normalize_desktop_care_config,
)
from .care_config import (
    normalize_desktop_legacy_state as normalize_desktop_legacy_state,
)
from .care_desktop import CareDesktopMixin
from .care_economy import CareEconomyMixin
from .care_inventory import CareInventoryMixin
from .care_persistence import CarePersistenceMixin
from .care_poke import CarePokeMixin
from .care_projection import CareProjectionMixin
from .care_projection import format_care_feed_prompt as format_care_feed_prompt
from .care_rewards import CareRewardsMixin
from .care_state import (
    CareStateMixin,
)
from .care_state import (
    _affection_tier as _affection_tier,
)
from .care_state import (
    _detect_and_store_tier_event as _detect_and_store_tier_event,
)
from .care_turns import CareTurnMixin

logger = logging.getLogger(__name__)


class CareRuntimeStore(
    CareStateMixin,
    CareProjectionMixin,
    CareTurnMixin,
    CareDesktopMixin,
    CareInventoryMixin,
    CareEconomyMixin,
    CareRewardsMixin,
    CarePokeMixin,
    CarePersistenceMixin,
):
    """Conversation bodies/relationships with role-scoped personal accounts."""

    def __init__(
        self,
        path: Path,
        *,
        reset_baseline_on_first_load: bool = False,
        known_conversations: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
    ):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._state: dict[str, Any] | None = None
        self._state_json: str | None = None
        self._known_conversations = known_conversations
        self._reset_baseline_on_first_load = bool(reset_baseline_on_first_load)


@dataclass(frozen=True, slots=True)
class CareModulePort:
    """Host-owned activation boundary around the existing Care authority.

    A disabled port never constructs or reads a ``CareRuntimeStore``.  Callers
    must branch on ``enabled`` and return the structured disabled result rather
    than inventing default vitals or silently accepting a mutation.
    """

    enabled: bool
    _runtime: CareRuntimeStore | None = None
    reason: str = ""
    reset_baseline_on_start: bool = False

    @classmethod
    def from_feature(
        cls,
        *,
        enabled: bool,
        storage_path: Path,
        reset_baseline_on_start: bool = False,
        known_conversations: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
    ) -> "CareModulePort":
        if not enabled:
            return cls(enabled=False, reason="feature_disabled")
        return cls(
            enabled=True,
            _runtime=CareRuntimeStore(
                storage_path,
                known_conversations=known_conversations,
                reset_baseline_on_first_load=bool(reset_baseline_on_start),
            ),
            reset_baseline_on_start=bool(reset_baseline_on_start),
        )

    @classmethod
    def disabled(cls, reason: str = "feature_disabled") -> "CareModulePort":
        return cls(enabled=False, reason=str(reason or "feature_disabled"))

    @property
    def runtime(self) -> CareRuntimeStore | None:
        return self._runtime if self.enabled else None

    def status_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": "enabled" if self.enabled else "disabled",
            "reason": "" if self.enabled else (self.reason or "feature_disabled"),
            "reset_baseline_on_start": bool(self.enabled and self.reset_baseline_on_start),
        }

    def disabled_result(self, *, reply: str = "养成模块未启用。") -> dict[str, Any]:
        return {
            "ok": False,
            "status": "disabled",
            "reason": self.reason or "feature_disabled",
            "reply": str(reply),
        }
