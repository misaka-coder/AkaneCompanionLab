"""Capability catalog shaping and request-only provider evidence overlays."""

from __future__ import annotations

from typing import Any

from services import provider_continuation as continuation

_EPHEMERAL_PROVIDER_BLOCK_TYPES = frozenset(
    {"image_url", "input_image", "image", "input_audio", "audio", "input_file", "file"}
)
_CAPABILITY_CATALOG_SOURCE_PREFIX = "prompt-context:capability-catalog:"
_LEGACY_CAPABILITY_SNAPSHOT_SOURCE_PREFIX = "prompt-context:capabilities:"


def _capability_catalog_source(source_ids: list[str] | tuple[str, ...]) -> tuple[str, int, str] | None:
    normalized = [str(item or "").strip() for item in source_ids if str(item or "").strip()]
    if not normalized or any(not item.startswith(_CAPABILITY_CATALOG_SOURCE_PREFIX) for item in normalized):
        return None
    for source_id in normalized:
        parts = source_id.split(":")
        if len(parts) < 7:
            continue
        try:
            generation = max(0, int(parts[2]))
        except (TypeError, ValueError):
            continue
        role = str(parts[3] or "").strip()
        if role in {"base", "update"}:
            return source_id, generation, role
    return None


def _catalog_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ).strip()
    return ""


def _shape_capability_catalog_projection(
    projection: dict[str, Any],
    *,
    compaction_generation: int,
    client_scope: str = "",
) -> tuple[dict[str, Any], str]:
    """Lift the base once; preserve every same-generation update in place."""

    shaped = dict(projection or {})
    history_turns = [dict(item) for item in list(shaped.get("history_turns") or []) if isinstance(item, dict)]
    history_groups = [list(group or []) for group in list(shaped.get("history_message_source_ids") or [])]
    active_turns = [dict(item) for item in list(shaped.get("active_turn_messages") or []) if isinstance(item, dict)]
    active_groups = [list(group or []) for group in list(shaped.get("active_turn_message_source_ids") or [])]
    current_turn_messages = [
        dict(item) for item in list(shaped.get("current_turn_messages") or []) if isinstance(item, dict)
    ]

    records: list[tuple[str, int, str, dict[str, Any], list[str]]] = []
    for message, source_ids in [*zip(history_turns, history_groups), *zip(active_turns, active_groups)]:
        parsed = _capability_catalog_source(source_ids)
        if parsed is not None:
            records.append((*parsed, message, source_ids))

    generation = max(0, int(compaction_generation or 0))

    def same_client(source_id):
        parts = source_id.split(":")
        return not client_scope or (parts[7] if len(parts) > 7 else "qq_text") == client_scope

    current_records = [record for record in records if record[1] == generation and same_client(record[0])]
    base_records = [record for record in current_records if record[2] == "base"]
    base_context = _catalog_message_text(base_records[-1][3]) if base_records else ""

    def keep_message(source_ids: list[str]) -> bool:
        normalized = [str(item or "").strip() for item in source_ids if str(item or "").strip()]
        if normalized and all(item.startswith(_LEGACY_CAPABILITY_SNAPSHOT_SOURCE_PREFIX) for item in normalized):
            return False
        parsed = _capability_catalog_source(normalized)
        if parsed is None:
            return True
        source_id, source_generation, role = parsed
        return bool(source_generation == generation and role == "update" and same_client(source_id))

    kept_history = [
        (message, source_ids) for message, source_ids in zip(history_turns, history_groups) if keep_message(source_ids)
    ]
    kept_active = [
        (message, source_ids) for message, source_ids in zip(active_turns, active_groups) if keep_message(source_ids)
    ]
    shaped["history_turns"] = [message for message, _source_ids in kept_history]
    shaped["history_message_source_ids"] = [source_ids for _message, source_ids in kept_history]
    shaped["active_turn_messages"] = [message for message, _source_ids in kept_active]
    shaped["active_turn_message_source_ids"] = [source_ids for _message, source_ids in kept_active]
    shaped["current_turn_messages"] = [
        message for message in current_turn_messages if keep_message(list(message.get("source_ids") or []))
    ]
    return shaped, base_context


def _split_active_projection_turns(
    projection: dict[str, Any],
    prepared_turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay media on ordinary turns without moving durable catalog updates."""

    active_turns = [
        dict(item) for item in list((projection or {}).get("active_turn_messages") or []) if isinstance(item, dict)
    ]
    active_groups = [
        list(group or []) for group in list((projection or {}).get("active_turn_message_source_ids") or [])
    ]
    ordinary: list[dict[str, Any]] = []
    ordinary_indexes: list[int] = []
    for index, (turn, source_ids) in enumerate(zip(active_turns, active_groups)):
        if _capability_catalog_source(source_ids) is None:
            ordinary.append(turn)
            ordinary_indexes.append(index)
    working = any(str(item.get("turn_id") or "").startswith("working.")
                  for item in projection.get("current_turn_messages") or [])
    overlaid = _overlay_ephemeral_provider_evidence(ordinary, prepared_turns, allow_folded_history=working)
    for index, turn in zip(ordinary_indexes, overlaid):
        active_turns[index] = turn
    return active_turns


def _visible_projection_source_ids(projection: dict[str, Any]) -> list[str]:
    """Return authoritative source ids in the order visible to the model."""

    source_ids = [
        str(source_id or "").strip()
        for source_id in list((projection or {}).get("source_ids") or [])
        if str(source_id or "").strip()
    ]
    for message in list((projection or {}).get("current_turn_messages") or []):
        if not isinstance(message, dict):
            continue
        source_ids.extend(
            str(source_id or "").strip()
            for source_id in list(message.get("source_ids") or [])
            if str(source_id or "").strip()
        )
    return list(dict.fromkeys(source_ids))


def _overlay_ephemeral_provider_evidence(
    authoritative_turns: list[dict[str, Any]],
    prepared_turns: list[dict[str, Any]],
    *, allow_folded_history: bool = False,
) -> list[dict[str, Any]]:
    """Decorate the authoritative open turn with request-only provider state.

    MemCore owns message order, roles and durable text. Raw image/audio/file
    bytes deliberately do not live in its projection, so the tool runtime may
    attach provider-native blocks to the matching open-turn message for this
    request only. Signed continuation also requires an unchanged tool batch.
    A shape mismatch skips media overlay, but fails for signed continuation.
    """

    authoritative = [dict(turn) for turn in authoritative_turns]
    prepared = [dict(turn) for turn in prepared_turns]
    if allow_folded_history:
        # Source-addressed working summaries add one message and can replace
        # old batches. Replay transient state only on surviving identical calls.
        def key(message):
            batch = continuation.batch(message)
            if batch:
                return ("calls", tuple(item["id"] for item in batch))
            if message.get("tool_call_id"):
                return ("result", str(message["tool_call_id"]))
            return None
        by_key = {key(message): message for message in prepared if key(message) is not None}
        prepared = [by_key.get(key(message), message) for message in authoritative]
    if not authoritative or len(authoritative) != len(prepared):
        if any(turn.get(continuation.FIELD) for turn in prepared):
            raise ValueError("provider_continuation_projection_changed")
        return authoritative
    if any(
        str(left.get("role") or "").strip().lower() != str(right.get("role") or "").strip().lower()
        for left, right in zip(authoritative, prepared)
    ):
        if any(turn.get(continuation.FIELD) for turn in prepared):
            raise ValueError("provider_continuation_projection_changed")
        return authoritative
    overlaid: list[dict[str, Any]] = []
    for durable, transient in zip(authoritative, prepared):
        durable = continuation.carry(transient, durable)
        if transient.get("reasoning_content") and continuation.batch(durable) == continuation.batch(transient):
            durable = {**durable, "reasoning_content": transient["reasoning_content"]}
        transient_content = transient.get("content")
        media_blocks = (
            [
                dict(block)
                for block in transient_content
                if isinstance(block, dict)
                and str(block.get("type") or "").strip().lower() in _EPHEMERAL_PROVIDER_BLOCK_TYPES
            ]
            if isinstance(transient_content, list)
            else []
        )
        if not media_blocks:
            overlaid.append(durable)
            continue
        content = durable.get("content")
        if isinstance(content, list):
            blocks = [dict(block) for block in content if isinstance(block, dict)]
        else:
            blocks = [{"type": "text", "text": str(content or "")}]
        overlaid.append({**durable, "content": [*blocks, *media_blocks]})
    return overlaid
