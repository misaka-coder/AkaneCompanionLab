"""Immutable job ownership, durable snapshots and JSON contracts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


JOB_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
JOB_CONTROL_STATES = frozenset({"running", "paused", "stopping", "stopped"})
_COMPLETION_MODES = frozenset({"agent", "silent"})
_MEMORY_MODES = frozenset({"current_turn", "timeline"})


@dataclass(frozen=True, slots=True)
class HostJobOwner:
    profile_user_id: str
    session_id: str

    def __post_init__(self) -> None:
        for field_name in ("profile_user_id", "session_id"):
            normalized = str(getattr(self, field_name) or "").strip()
            if not normalized:
                raise ValueError(f"host_job_{field_name}_required")
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class HostJob:
    job_id: str
    sequence: int
    owner: HostJobOwner
    character_pack_id: str
    channel: str
    delivery_target: str
    capability_source: str
    capability_id: str
    turn_id: str
    tool_call_id: str
    idempotency_key: str
    argument_fingerprint: str
    payload: dict[str, Any]
    completion_mode: str
    memory_mode: str
    status: str
    attempts: int
    available_at: float
    created_at: float
    started_at: float
    finished_at: float
    lease_until: float
    claim_token: str
    claimed_by: str
    cancel_requested: bool
    result_summary: str
    artifacts: tuple[dict[str, Any], ...]
    completion_event_id: str
    completion_status: str
    completion_attempts: int
    completion_last_error: str
    last_error: str
    result: dict[str, Any] = field(default_factory=dict)
    delivery_receipt: dict[str, Any] = field(default_factory=dict)
    scope_revoked_reason: str = ""
    control_state: str = "running"
    checkpoint: Any = None
    checkpoint_version: int = 0
    checkpoint_fingerprint: str = ""
    checkpoint_generation_id: str = ""
    checkpoint_updated_at: float = 0.0
    recovery_generation_id: str = ""
    recovery_generation_epoch: int = 0
    recovery_owner_round: int = 0
    recovery_epoch: int = 0
    recovery_state: str = ""
    recovery_reason: str = ""
    # Model receipt is independent of execution, pagination and user delivery.
    model_consumed_by: str = ""


def _json_object(value: dict[str, Any]) -> str | None:
    return _json_value(value)


def _json_value(value: Any) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _json_value_load(value: Any) -> Any:
    try:
        return json.loads(str(value if value is not None else "null"))
    except (TypeError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_artifacts(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, dict) for item in value):
        return None
    return _json_value(list(value))
