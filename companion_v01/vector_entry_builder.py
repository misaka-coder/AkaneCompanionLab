from __future__ import annotations

from typing import Any

from .text_utils import join_tags


def build_raw_vector_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": record["source_id"],
        "text": record.get("content", "") or "",
        "metadata": {
            "profile_user_id": record["profile_user_id"],
            "session_id": record["session_id"],
            "character_pack_id": str(record.get("character_pack_id") or ""),
            "seq_no": int(record["seq_no"]),
            "timestamp": int(record["timestamp"]),
            "date_label": record["date_label"],
            "time_of_day": record["time_of_day"],
            "speaker": record["role"],
            "entry_type": "raw",
            "semantic_tags_text": join_tags(record.get("semantic_tags") or []),
        },
    }


def build_summary_vector_entry(record: dict[str, Any]) -> dict[str, Any]:
    search_text = " ".join(
        [record.get("diary_summary", "")]
        + list(record.get("key_events") or [])
        + list(record.get("core_facts") or [])
    )
    return {
        "source_id": record["summary_id"],
        "text": search_text,
        "metadata": {
            "profile_user_id": record["profile_user_id"],
            "session_id": record["session_id"],
            "character_pack_id": str(record.get("character_pack_id") or ""),
            "seq_no": int(record["source_end_seq"]),
            "timestamp": int(record["timestamp"]),
            "date_label": record["date_label"],
            "time_of_day": record["time_of_day"],
            "speaker": "summary",
            "entry_type": "summary",
            "semantic_tags_text": join_tags(record.get("semantic_tags") or []),
        },
    }


def build_semantic_summary_vector_entry(record: dict[str, Any]) -> dict[str, Any]:
    search_text = " ".join(
        [record.get("semantic_summary", "")]
        + list(record.get("stable_facts") or [])
        + list(record.get("recurring_topics") or [])
        + list(record.get("important_people") or [])
        + list(record.get("open_loops") or [])
    )
    return {
        "source_id": record["semantic_id"],
        "text": search_text,
        "metadata": {
            "profile_user_id": record["profile_user_id"],
            "session_id": record["session_id"],
            "character_pack_id": str(record.get("character_pack_id") or ""),
            "seq_no": 0,
            "timestamp": int(record["timestamp"]),
            "date_label": record["date_label"],
            "time_of_day": record["time_of_day"],
            "speaker": "semantic_summary",
            "entry_type": "semantic_summary",
            "semantic_tags_text": join_tags(record.get("semantic_tags") or []),
        },
    }
