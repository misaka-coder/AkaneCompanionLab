"""Stable QQ source identities for idempotent passive-history replay."""
from __future__ import annotations

from typing import Any


def _passive_replay_identity(payload: dict[str, Any]) -> str:
    """Stable admitted message or host record identity, including media."""
    return str(payload.get("passive_memory_source_id") or payload.get("source_message_id") or "").strip()


def _passive_record_source_id(
    payload: dict[str, Any],
    *,
    profile_user_id: str,
    character_pack_id: str,
) -> str:
    """Deterministic record id for a replay-stable passive envelope.

    A queued passive batch may be replayed after a memory failure. Keeping one
    stable id for the message in both the local store and MemCore makes that
    replay idempotent instead of writing the same history twice.
    """

    frozen_source = str(payload.get("passive_memory_source_id") or "").strip()
    if frozen_source:
        return frozen_source
    identity = _passive_replay_identity(payload)
    if not identity:
        return ""
    return f"qq-passive:{profile_user_id}:{character_pack_id}:{identity}"
