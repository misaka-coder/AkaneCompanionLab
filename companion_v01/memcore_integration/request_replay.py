"""Read-only verification of terminal request replays, not a second ledger."""
from __future__ import annotations

from typing import Any


def verified_terminal_projection_replay(
    *,
    system: Any,
    turn_id: str,
    provider_profile: str,
    turn_messages: list[Any],
    history_messages: list[dict[str, Any]],
    history_message_indexes: list[int] | None,
) -> str:
    """Return the terminal status only for an exact, source-owned replay.

    Completing/aborting a turn does not request-freeze its final or unobserved
    suffix. Replaying that already persisted surface must not promote those
    projections to REQUEST_FROZEN. Any actual payload, mapping, version or
    history mismatch falls through to MemCore's normal strict write checks.
    No audit is claimed for a request skipped at this terminal boundary.
    """
    read_turn = getattr(system, "get_turn", None)
    store = getattr(system, "store", None)
    read_projections = getattr(store, "get_turn_projections", None)
    if not callable(read_turn) or not callable(read_projections) or not turn_messages:
        return ""
    handle = read_turn(turn_id)
    status = str(getattr(handle, "status", ""))
    if status not in {"closed", "aborted"}:
        return ""

    saved = read_projections(namespace=system.namespace, turn_id=turn_id, provider_profile=provider_profile)
    by_index = {item.projection_index: item for item in saved}
    indexes = (list(history_message_indexes) if history_message_indexes is not None
               else list(range(len(history_messages) - len(turn_messages), len(history_messages))))
    if (len(indexes) != len(turn_messages) or len(set(indexes)) != len(indexes)
            or indexes != sorted(indexes) or any(index < 0 or index >= len(history_messages) for index in indexes)):
        return ""

    # Keep imports lazy: legacy installations must not require MemCore.
    from memcore.projection import (
        ProjectionStatus,
        canonical_json_bytes,
        merge_projection_status,
        sanitize_projection_payload,
    )

    seen = set()
    for ordinal, (message, request_index) in enumerate(zip(turn_messages, indexes)):
        index = message.projection_index if message.projection_index >= 0 else ordinal
        existing = by_index.get(index)
        if existing is None or index in seen:
            return ""
        seen.add(index)
        if (tuple(message.source_ids) != tuple(existing.source_ids)
                or message.projection_version != existing.projection_version):
            return ""
        # An observer created before the first freeze may retain the earlier
        # rendering status. Only the existing request-frozen status can absorb
        # that stale metadata; never absorb a changed final/settled status.
        if message.projection_status != existing.projection_status and (
            existing.projection_status != ProjectionStatus.REQUEST_FROZEN
            or merge_projection_status(ProjectionStatus.REQUEST_FROZEN, message.projection_status)
                != existing.projection_status
        ):
            return ""
        actual, _ = sanitize_projection_payload(history_messages[request_index], provider_profile=provider_profile)
        declared = canonical_json_bytes(message.payload)
        if canonical_json_bytes(actual) != declared or canonical_json_bytes(existing.payload) != declared:
            return ""
    return status
