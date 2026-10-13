"""Connect exact provider requests to MemCore's optional prefix selection."""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

from ..owner_private_session import memory_scope
from .contracts import resolve_memcore_provider_profile


def _refresh_terminal_context(snapshot: Any, projections: tuple[Any, ...]) -> Any:
    """Prepare settled provider history without an API call or ledger rewrite."""
    from memcore import ChatContextSnapshot, stable_projection_hash
    from memcore.projection import sanitize_projection_payload
    from ..model_runtime.memory_prefix import CapturedCompletionRequest
    from services import provider_continuation as continuation

    captured = snapshot.transport
    if not isinstance(captured, CapturedCompletionRequest) or not projections:
        return None
    required = {sid for message in projections for sid in message.source_ids}
    indexes = [i for i, ids in enumerate(snapshot.message_source_ids) if required.intersection(ids)]
    if not indexes:
        return None
    first, last = min(indexes), max(indexes)
    # Do not resurrect an older range absent from the captured request, or
    # replace another mapped source that happens to lie inside this interval.
    captured_ids = {sid for ids in snapshot.message_source_ids for sid in ids}
    if not set(projections[0].source_ids) <= captured_ids:
        return None

    def is_background(ids: tuple[str, ...]) -> bool:
        return not ids or all(sid.startswith("prompt-context:") for sid in ids)

    if any(
        not is_background(ids) and not set(ids) <= required for ids in snapshot.message_source_ids[first : last + 1]
    ):
        return None
    original = captured.payload["messages"]
    exact_wire: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
    signed_batches: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    positions: dict[str, int] = {}
    for index in indexes:
        ids = snapshot.message_source_ids[index]
        for sid in ids:
            positions.setdefault(sid, index)
        wire = original[index]
        safe, _ = sanitize_projection_payload(continuation.public_message(wire))
        exact_wire[(ids, stable_projection_hash(safe))] = wire
        if wire.get(continuation.FIELD):
            signed_batches[ids] = continuation.batch(wire)
    effective = list(original[:first])
    groups = list(snapshot.message_source_ids[:first])
    background_indexes = [i for i in range(first, last + 1) if is_background(snapshot.message_source_ids[i])]
    background_cursor = 0
    for message in projections:
        ids = tuple(message.source_ids)
        boundary = min((positions[sid] for sid in ids if sid in positions), default=last + 1)
        # Preserve unchanged request-local evidence at its source boundary.
        # It remains background, never a newly persisted dialogue/source.
        while background_cursor < len(background_indexes) and background_indexes[background_cursor] < boundary:
            index = background_indexes[background_cursor]
            effective.append(original[index])
            groups.append(())
            background_cursor += 1
        wire = exact_wire.get((ids, stable_projection_hash(message.payload)))
        if wire is None:
            wire = dict(message.payload)
            if ids in signed_batches and continuation.batch(wire) != signed_batches[ids]:
                # A signature cannot authenticate rewritten tool arguments.
                # Keep the independent path rather than replay stale native data.
                return None
        effective.append(wire)
        groups.append(ids)
    refreshed = CapturedCompletionRequest.capture(
        captured.bundle,
        {**captured.payload, "messages": effective},
        context_window=captured.context_window,
    )
    return ChatContextSnapshot(
        provider_profile=snapshot.provider_profile,
        messages=tuple(refreshed.public_messages()),
        message_source_ids=tuple(groups),
        transport=refreshed,
        terminal_context_refresh=_refresh_terminal_context,
    )


class MemcoreChatPrefixMixin:
    @memory_scope
    def capture_compaction_request(
        self,
        *,
        request: dict[str, Any],
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        system = self._get_system_or_none(
            operation="capture_compaction_request",
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        captured = request.get("_compaction_request")
        if system is None or captured is None or system.config.compaction_request_mode != "chat_prefix":
            return {"ok": True, "status": "disabled"}
        from memcore import ChatContextSnapshot, stable_projection_hash
        from services import provider_continuation as continuation

        projection = generation_context.get("memcore_projection_read") or {}
        history = list(generation_context.get("history_turns") or [])
        start = int(generation_context.get("memcore_history_start_index") or 0)
        candidates = [
            (dict(payload), tuple(ids))
            for payload, ids in zip(history[start:], projection.get("history_message_source_ids") or [])
        ]
        candidates += [
            (dict(message.get("payload") or {}), tuple(message.get("source_ids") or []))
            for message in request.get("_compaction_source_messages") or projection.get("current_turn_messages") or []
        ]
        messages = captured.public_messages()
        # Bind the actual public wire shape here. SDK eligibility separately
        # verifies its persisted safe projection; avoid sanitizing every long
        # historical body twice on the user's request path.
        message_hashes = [stable_projection_hash(message) for message in messages]
        positions: dict[str, list[int]] = {}
        for index, fingerprint in enumerate(message_hashes):
            positions.setdefault(fingerprint, []).append(index)
        groups: list[tuple[str, ...]] = [() for _ in messages]
        cursor = 0
        for payload, ids in candidates:
            wanted = stable_projection_hash(continuation.public_message(payload))
            matches = positions.get(wanted, [])
            slot = bisect_left(matches, cursor)
            if slot < len(matches):
                index = matches[slot]
                groups[index] = ids
                cursor = index + 1
        profile = resolve_memcore_provider_profile(
            projection.get("provider_profile") or request.get("protocol") or "openai_chat"
        )
        # Explicit hard-budget capture requests open-turn maintenance even if
        # the configured soft threshold has not been reached yet.
        requested_limit = int(request.get("_working_budget_tokens") or 0)
        system.capture_chat_context(
            ChatContextSnapshot(
                profile,
                tuple(messages),
                tuple(groups),
                captured,
                tuple(str(item) for item in request.get("_pending_task_ids") or []),
                requested_limit,
                terminal_context_refresh=_refresh_terminal_context,
            )
        )
        # The same namespace job is coalesced; a slow summary never creates a
        # second competing writer or a user-visible completion notification.
        return self.compact_due_background(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            provider_profile=profile,
        )
