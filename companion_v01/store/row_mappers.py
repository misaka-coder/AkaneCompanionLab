"""Row mappers extracted from store/core.py.

Each function converts a sqlite3.Row (or dict) into the canonical dict shape
for that table. None use `self` — they are pure functions that take the raw
row dict and the `safe_json_loads` strategy.
"""

from __future__ import annotations

import json
from typing import Any

from .json_utils import safe_json_loads


def row_to_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(row.get("source_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "seq_no": int(row.get("seq_no") or 0),
        "role": str(row.get("role") or ""),
        "content": str(row.get("content") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "date_label": str(row.get("date_label") or ""),
        "time_of_day": str(row.get("time_of_day") or ""),
        "semantic_tags": json.loads(str(row.get("semantic_tags_json") or "[]"))
        if isinstance(row.get("semantic_tags_json"), str)
        else (list(row["semantic_tags_json"]) if isinstance(row.get("semantic_tags_json"), (list, tuple)) else []),
        "memory_metadata": safe_json_loads(row.get("memory_metadata_json"), fallback={}) or {},
        "index_in_vector": bool(row.get("index_in_vector")),
        "is_summarized": bool(row.get("is_summarized")),
        "summary_id": str(row.get("summary_id") or ""),
    }


def row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_id": str(row.get("summary_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "date_label": str(row.get("date_label") or ""),
        "time_of_day": str(row.get("time_of_day") or ""),
        "period_label": str(row.get("period_label") or ""),
        "event_type": str(row.get("event_type") or ""),
        "importance": float(row.get("importance") or 0.0),
        "diary_summary": str(row.get("diary_summary") or ""),
        "key_events": json.loads(str(row.get("key_events_json") or "[]"))
        if isinstance(row.get("key_events_json"), str)
        else (list(row["key_events_json"]) if isinstance(row.get("key_events_json"), (list, tuple)) else []),
        "core_facts": json.loads(str(row.get("core_facts_json") or "[]"))
        if isinstance(row.get("core_facts_json"), str)
        else (list(row["core_facts_json"]) if isinstance(row.get("core_facts_json"), (list, tuple)) else []),
        "semantic_tags": json.loads(str(row.get("semantic_tags_json") or "[]"))
        if isinstance(row.get("semantic_tags_json"), str)
        else (list(row["semantic_tags_json"]) if isinstance(row.get("semantic_tags_json"), (list, tuple)) else []),
        "memory_metadata": safe_json_loads(row.get("memory_metadata_json"), fallback={}) or {},
        "source_ids": json.loads(str(row.get("source_ids_json") or "[]"))
        if isinstance(row.get("source_ids_json"), str)
        else (list(row["source_ids_json"]) if isinstance(row.get("source_ids_json"), (list, tuple)) else []),
        "source_id": str(row.get("source_id") or ""),
    }


def row_to_semantic_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "semantic_id": str(row.get("semantic_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "date_label": str(row.get("date_label") or ""),
        "time_of_day": str(row.get("time_of_day") or ""),
        "importance": float(row.get("importance") or 0.0),
        "semantic_summary": str(row.get("semantic_summary") or ""),
        "stable_facts": json.loads(str(row.get("stable_facts_json") or "[]"))
        if isinstance(row.get("stable_facts_json"), str)
        else (list(row["stable_facts_json"]) if isinstance(row.get("stable_facts_json"), (list, tuple)) else []),
        "recurring_topics": json.loads(str(row.get("recurring_topics_json") or "[]"))
        if isinstance(row.get("recurring_topics_json"), str)
        else (
            list(row["recurring_topics_json"]) if isinstance(row.get("recurring_topics_json"), (list, tuple)) else []
        ),
        "important_people": json.loads(str(row.get("important_people_json") or "[]"))
        if isinstance(row.get("important_people_json"), str)
        else (
            list(row["important_people_json"]) if isinstance(row.get("important_people_json"), (list, tuple)) else []
        ),
        "open_loops": json.loads(str(row.get("open_loops_json") or "[]"))
        if isinstance(row.get("open_loops_json"), str)
        else (list(row["open_loops_json"]) if isinstance(row.get("open_loops_json"), (list, tuple)) else []),
        "memory_metadata": safe_json_loads(row.get("memory_metadata_json"), fallback={}) or {},
        "reinforcement_count": int(row.get("reinforcement_count") or 1),
        "last_reinforced_ts": int(row.get("last_reinforced_ts") or 0),
        "summary_ids": json.loads(str(row.get("summary_ids_json") or "[]"))
        if isinstance(row.get("summary_ids_json"), str)
        else (list(row["summary_ids_json"]) if isinstance(row.get("summary_ids_json"), (list, tuple)) else []),
    }


def row_to_eval_turn(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_id": str(row.get("eval_id") or ""),
        "source_id": str(row.get("source_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "user_message": str(row.get("user_message") or ""),
        "assistant_reply": str(row.get("assistant_reply") or ""),
        "retrieval_context": str(row.get("retrieval_context") or ""),
        "retrieval_used": bool(row.get("retrieval_used")),
        "tool_calls": safe_json_loads(row.get("tool_calls_json"), fallback=[]) or [],
        "eval_result": str(row.get("eval_result") or ""),
        "eval_notes": str(row.get("eval_notes") or ""),
    }


def row_to_session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "display_title": str(row.get("display_title") or ""),
        "gift_focus_asset_id": str(row.get("gift_focus_asset_id") or ""),
        "gift_focus_updated_at": int(row.get("gift_focus_updated_at") or 0),
        "latest_final_json": safe_json_loads(row.get("latest_final_json_json"), fallback={}) or {},
        "latest_message_ts": int(row.get("latest_message_ts") or 0),
        "message_count": int(row.get("message_count") or 0),
    }


def row_to_reminder(row: dict[str, Any], *, safe_json_loads_fn) -> dict[str, Any]:
    return {
        "reminder_id": str(row.get("reminder_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "character_pack_id": str(row.get("character_pack_id") or ""),
        "due_ts": int(row.get("due_ts") or 0),
        "created_ts": int(row.get("created_ts") or 0),
        "content": str(row.get("content") or ""),
        "status": str(row.get("status") or ""),
        "source_turn_source_id": str(row.get("source_turn_source_id") or ""),
        "memory_metadata": safe_json_loads_fn(row.get("memory_metadata_json"), fallback={}) or {},
        "notification_payload": safe_json_loads_fn(row.get("notification_payload_json"), fallback={}) or {},
    }


def row_to_persona_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": str(row.get("card_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "name": str(row.get("name") or ""),
        "card_type": str(row.get("card_type") or ""),
        "status": str(row.get("status") or ""),
        "content_json": safe_json_loads(row.get("content_json"), fallback={}) or {},
        "summary_json": safe_json_loads(row.get("summary_json"), fallback={}) or {},
        "created_ts": int(row.get("created_ts") or 0),
        "updated_ts": int(row.get("updated_ts") or 0),
        "source": str(row.get("source") or ""),
    }


def row_to_persona_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(row.get("event_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "card_id": str(row.get("card_id") or ""),
        "event_type": str(row.get("event_type") or ""),
        "content_json": safe_json_loads(row.get("content_json"), fallback={}) or {},
        "timestamp": int(row.get("timestamp") or 0),
        "source": str(row.get("source") or ""),
    }


def row_to_user_media_asset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "asset_type": str(row.get("asset_type") or ""),
        "media_kind": str(row.get("media_kind") or ""),
        "storage_relpath": str(row.get("storage_relpath") or ""),
        "status": str(row.get("status") or ""),
        "display_name": str(row.get("display_name") or ""),
        "source_relpath": str(row.get("source_relpath") or ""),
        "tags": str(row.get("tags") or ""),
        "file_size": int(row.get("file_size") or 0),
        "file_hash": str(row.get("file_hash") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "gift_metadata": safe_json_loads(row.get("gift_metadata_json"), fallback={}) or {},
        "container_id": str(row.get("container_id") or ""),
        "artifact_metadata": safe_json_loads(row.get("artifact_metadata_json"), fallback={}) or {},
        "sequence_no": int(row.get("sequence_no") or 0),
    }


def row_to_attachment_inbox_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": str(row.get("item_id") or ""),
        "handle": str(row.get("handle") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "source_id": str(row.get("source_id") or ""),
        "kind": str(row.get("kind") or ""),
        "status": str(row.get("status") or ""),
        "storage_relpath": str(row.get("storage_relpath") or ""),
        "origin_name": str(row.get("origin_name") or ""),
        "mime_type": str(row.get("mime_type") or ""),
        "file_size": int(row.get("file_size") or 0),
        "file_hash": str(row.get("file_hash") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "source_uri": str(row.get("source_uri") or ""),
        "observation_result": safe_json_loads(row.get("observation_result_json"), fallback={}) or {},
        "ack_required": bool(row.get("ack_required")),
    }


def row_to_generated_file(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_id": str(row.get("generated_id") or ""),
        "handle": str(row.get("handle") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "source_id": str(row.get("source_id") or ""),
        "status": str(row.get("status") or ""),
        "format": str(row.get("format") or ""),
        "storage_relpath": str(row.get("storage_relpath") or ""),
        "origin_name": str(row.get("origin_name") or ""),
        "file_size": int(row.get("file_size") or 0),
        "timestamp": int(row.get("timestamp") or 0),
        "extra_metadata": safe_json_loads(row.get("extra_metadata_json"), fallback={}) or {},
        "source_generated_id": str(row.get("source_generated_id") or ""),
    }


def row_to_desktop_music_timeline(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timeline_id": str(row.get("timeline_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "source": str(row.get("source") or ""),
        "source_track_id": str(row.get("source_track_id") or ""),
        "source_extra": str(row.get("source_extra") or ""),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or ""),
        "album": str(row.get("album") or ""),
        "duration_seconds": int(row.get("duration_seconds") or 0),
        "listen_start_ts": int(row.get("listen_start_ts") or 0),
        "listen_end_ts": int(row.get("listen_end_ts") or 0),
        "status": str(row.get("status") or ""),
        "generated_track_id": str(row.get("generated_track_id") or ""),
    }


def row_to_task_workspace(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(row.get("task_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "source": str(row.get("source") or ""),
        "status": str(row.get("status") or ""),
        "instruction": str(row.get("instruction") or ""),
        "artifacts": safe_json_loads(row.get("artifacts_json"), fallback=[]) or [],
        "created_ts": int(row.get("created_ts") or 0),
        "updated_ts": int(row.get("updated_ts") or 0),
        "event_count": int(row.get("event_count") or 0),
        "focus_source_ids": safe_json_loads(row.get("focus_source_ids_json"), fallback=[]) or [],
    }


def row_to_task_workspace_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(row.get("event_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "event_type": str(row.get("event_type") or ""),
        "status": str(row.get("status") or ""),
        "priority": str(row.get("priority") or ""),
        "summary": str(row.get("summary") or ""),
        "content": safe_json_loads(row.get("content_json"), fallback={}) or {},
    }


def row_to_vision_observation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": str(row.get("observation_id") or ""),
        "profile_user_id": str(row.get("profile_user_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "timestamp": int(row.get("timestamp") or 0),
        "observer_id": str(row.get("observer_id") or ""),
        "content": str(row.get("content") or ""),
        "observation_type": str(row.get("observation_type") or ""),
        "scene_context": safe_json_loads(row.get("scene_context_json"), fallback={}) or {},
        "gift_context": safe_json_loads(row.get("gift_context_json"), fallback={}) or {},
    }
