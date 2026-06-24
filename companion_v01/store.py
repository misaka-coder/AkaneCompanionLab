from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .text_utils import timestamp_to_date_label, infer_time_of_day

CHARACTER_PACK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
logger = logging.getLogger("akane.store")


def normalize_character_pack_id(value: Any) -> str:
    pack_id = str(value or "").strip()
    if not pack_id or not CHARACTER_PACK_ID_PATTERN.fullmatch(pack_id):
        return ""
    return pack_id


def _loads_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        payload = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "akane_memory_v01.db"
        self._attachment_inbox_write_lock = threading.Lock()
        self._workspace_file_write_lock = threading.Lock()
        self._message_write_callback: Callable[[dict[str, Any]], None] | None = None
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    source_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    seq_no INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    date_label TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    semantic_tags_json TEXT NOT NULL,
                    memory_metadata_json TEXT NOT NULL DEFAULT '{}',
                    index_in_vector INTEGER NOT NULL DEFAULT 1,
                    is_summarized INTEGER NOT NULL DEFAULT 0,
                    summary_id TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_chat_session_seq
                ON chat_messages(session_id, seq_no);

                CREATE INDEX IF NOT EXISTS idx_chat_profile_time
                ON chat_messages(profile_user_id, timestamp);

                CREATE TABLE IF NOT EXISTS memory_summaries (
                    summary_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    timestamp INTEGER NOT NULL,
                    date_label TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    period_label TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    importance REAL NOT NULL,
                    diary_summary TEXT NOT NULL,
                    key_events_json TEXT NOT NULL,
                    core_facts_json TEXT NOT NULL,
                    semantic_tags_json TEXT NOT NULL,
                    memory_metadata_json TEXT NOT NULL DEFAULT '{}',
                    is_semanticized INTEGER NOT NULL DEFAULT 0,
                    semantic_id TEXT NOT NULL DEFAULT '',
                    source_start_seq INTEGER NOT NULL,
                    source_end_seq INTEGER NOT NULL,
                    source_ids_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_summary_profile_time
                ON memory_summaries(profile_user_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS memory_semantic_summaries (
                    semantic_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    period_start_ts INTEGER NOT NULL,
                    period_end_ts INTEGER NOT NULL,
                    date_label TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    importance REAL NOT NULL,
                    semantic_summary TEXT NOT NULL,
                    stable_facts_json TEXT NOT NULL,
                    recurring_topics_json TEXT NOT NULL,
                    important_people_json TEXT NOT NULL,
                    open_loops_json TEXT NOT NULL,
                    semantic_tags_json TEXT NOT NULL,
                    memory_metadata_json TEXT NOT NULL DEFAULT '{}',
                    source_summary_ids_json TEXT NOT NULL,
                    reinforcement_count INTEGER NOT NULL DEFAULT 1,
                    last_reinforced_ts INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_semantic_profile_time
                ON memory_semantic_summaries(profile_user_id, last_reinforced_ts DESC, importance DESC, timestamp DESC);

                CREATE TABLE IF NOT EXISTS eval_turns (
                    trace_id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    profile_user_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    user_message TEXT NOT NULL,
                    router_json TEXT NOT NULL,
                    verifier_json TEXT NOT NULL,
                    final_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    character_pack_id TEXT NOT NULL DEFAULT '',
                    display_title TEXT NOT NULL DEFAULT '',
                    current_gift_focus_asset_id TEXT NOT NULL DEFAULT '',
                    current_gift_focus_updated_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_profile_updated
                ON chat_sessions(profile_user_id, updated_at DESC, created_at DESC);

                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    due_ts INTEGER NOT NULL,
                    date_label TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    raw_time_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    fired_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders(profile_user_id, session_id, status, due_ts);

                CREATE TABLE IF NOT EXISTS user_media_assets (
                    asset_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    origin_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    file_ext TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    storage_relpath TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'offered',
                    container_type TEXT NOT NULL DEFAULT '',
                    container_key TEXT NOT NULL DEFAULT '',
                    container_name TEXT NOT NULL DEFAULT '',
                    artifact_flags_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_user_media_assets_profile_status
                ON user_media_assets(profile_user_id, media_kind, status, updated_at DESC, created_at DESC);

                CREATE TABLE IF NOT EXISTS vision_observations (
                    observation_id TEXT PRIMARY KEY,
                    observation_type TEXT NOT NULL,
                    resource_fingerprint TEXT NOT NULL,
                    target_id TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    public_path TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT 'v1',
                    provider TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ready',
                    summary TEXT NOT NULL DEFAULT '',
                    observation_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_observations_fingerprint
                ON vision_observations(observation_type, resource_fingerprint, prompt_version);

                CREATE TABLE IF NOT EXISTS persona_cards (
                    card_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'inactive',
                    summary TEXT NOT NULL DEFAULT '',
                    speech_style TEXT NOT NULL DEFAULT '',
                    interaction_bias TEXT NOT NULL DEFAULT '',
                    resource_preference TEXT NOT NULL DEFAULT '',
                    switch_hint TEXT NOT NULL DEFAULT '',
                    unsuitable_contexts TEXT NOT NULL DEFAULT '',
                    created_reason TEXT NOT NULL DEFAULT '',
                    updated_reason TEXT NOT NULL DEFAULT '',
                    source_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    archived_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_persona_cards_profile_session_status
                ON persona_cards(profile_user_id, session_id, status, updated_at DESC, created_at DESC);

                CREATE TABLE IF NOT EXISTS persona_events (
                    event_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    card_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_persona_events_profile_session_time
                ON persona_events(profile_user_id, session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS persona_session_states (
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    active_card_id TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(profile_user_id, session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_persona_session_states_profile
                ON persona_session_states(profile_user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS attachment_inbox_items (
                    attachment_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    attachment_handle TEXT NOT NULL DEFAULT '',
                    sequence_no INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending_observation',
                    origin_name TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    file_ext TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    storage_relpath TEXT NOT NULL DEFAULT '',
                    source_event_id TEXT NOT NULL DEFAULT '',
                    source_message_id TEXT NOT NULL DEFAULT '',
                    summary_title TEXT NOT NULL DEFAULT '',
                    short_hint TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL DEFAULT 0,
                    focus_rank INTEGER NOT NULL DEFAULT 0,
                    expires_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_status
                ON attachment_inbox_items(profile_user_id, session_id, status, updated_at DESC, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_kind_status
                ON attachment_inbox_items(profile_user_id, session_id, kind, status, updated_at DESC, created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_handle
                ON attachment_inbox_items(profile_user_id, session_id, attachment_handle)
                WHERE attachment_handle != '';

                CREATE TABLE IF NOT EXISTS generated_files (
                    generated_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    generated_handle TEXT NOT NULL DEFAULT '',
                    sequence_no INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ready',
                    output_title TEXT NOT NULL DEFAULT '',
                    output_format TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    file_ext TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    storage_relpath TEXT NOT NULL DEFAULT '',
                    source_ids_json TEXT NOT NULL DEFAULT '[]',
                    content_card_json TEXT NOT NULL DEFAULT '{}',
                    summary TEXT NOT NULL DEFAULT '',
                    created_by_tool TEXT NOT NULL DEFAULT '',
                    version_of_generated_id TEXT NOT NULL DEFAULT '',
                    version_no INTEGER NOT NULL DEFAULT 1,
                    delivery_status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_generated_files_profile_session_status
                ON generated_files(profile_user_id, session_id, status, updated_at DESC, created_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_files_profile_session_handle
                ON generated_files(profile_user_id, session_id, generated_handle)
                WHERE generated_handle != '';

                CREATE TABLE IF NOT EXISTS workspace_file_states (
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workspace_uri TEXT NOT NULL,
                    focus_rank INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(profile_user_id, session_id, workspace_uri)
                );

                CREATE INDEX IF NOT EXISTS idx_workspace_file_states_focus
                ON workspace_file_states(
                    profile_user_id,
                    session_id,
                    focus_rank,
                    updated_at DESC
                );

                CREATE TABLE IF NOT EXISTS desktop_music_timelines (
                    timeline_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    source_handle TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    segments_json TEXT NOT NULL DEFAULT '[]',
                    rolling_summary TEXT NOT NULL DEFAULT '',
                    ready_until_seconds REAL NOT NULL DEFAULT 0,
                    transcript_generated_id TEXT NOT NULL DEFAULT '',
                    transcript_generated_handle TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    source_size INTEGER NOT NULL DEFAULT 0,
                    source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_desktop_music_timeline_source
                ON desktop_music_timelines(profile_user_id, session_id, source_id)
                WHERE source_id != '';

                CREATE INDEX IF NOT EXISTS idx_desktop_music_timeline_session_status
                ON desktop_music_timelines(profile_user_id, session_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS task_workspaces (
                    task_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT 'Akane',
                    status TEXT NOT NULL DEFAULT 'queued',
                    raw_request_json TEXT NOT NULL DEFAULT '{}',
                    normalized_goal TEXT NOT NULL DEFAULT '',
                    success_criteria_json TEXT NOT NULL DEFAULT '[]',
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    pending_question_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER NOT NULL DEFAULT 0,
                    cleaned_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_task_workspaces_profile_session_status
                ON task_workspaces(profile_user_id, session_id, status, updated_at DESC, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_task_workspaces_profile_status
                ON task_workspaces(profile_user_id, status, updated_at DESC, created_at DESC);

                CREATE TABLE IF NOT EXISTS task_workspace_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_actor TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    requires_user INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    handled_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_task_workspace_events_task_time
                ON task_workspace_events(task_id, created_at ASC);

                CREATE INDEX IF NOT EXISTS idx_task_workspace_events_profile_session_status
                ON task_workspace_events(profile_user_id, session_id, status, created_at ASC);
                """
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_sessions",
                column_name="character_pack_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_sessions",
                column_name="current_gift_focus_asset_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_sessions",
                column_name="current_gift_focus_updated_at",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_messages",
                column_name="character_pack_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_messages",
                column_name="index_in_vector",
                column_definition="INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                conn=conn,
                table_name="chat_messages",
                column_name="memory_metadata_json",
                column_definition="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_summaries",
                column_name="character_pack_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_summaries",
                column_name="is_semanticized",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_summaries",
                column_name="semantic_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_summaries",
                column_name="memory_metadata_json",
                column_definition="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_semantic_summaries",
                column_name="character_pack_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="memory_semantic_summaries",
                column_name="memory_metadata_json",
                column_definition="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn=conn,
                table_name="eval_turns",
                column_name="character_pack_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="asset_type",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="origin_event_type",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="origin_source_id",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="source_ids_json",
                column_definition="TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="payload_json",
                column_definition="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="last_decision_at",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="last_touched_at",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="container_type",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="container_key",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="container_name",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="user_media_assets",
                column_name="artifact_flags_json",
                column_definition="TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn=conn,
                table_name="persona_cards",
                column_name="unsuitable_contexts",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="attachment_inbox_items",
                column_name="attachment_handle",
                column_definition="TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn=conn,
                table_name="attachment_inbox_items",
                column_name="sequence_no",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn=conn,
                table_name="attachment_inbox_items",
                column_name="focus_rank",
                column_definition="INTEGER NOT NULL DEFAULT 0",
            )
            generated_columns = [
                ("generated_handle", "TEXT NOT NULL DEFAULT ''"),
                ("sequence_no", "INTEGER NOT NULL DEFAULT 0"),
                ("status", "TEXT NOT NULL DEFAULT 'ready'"),
                ("output_title", "TEXT NOT NULL DEFAULT ''"),
                ("output_format", "TEXT NOT NULL DEFAULT ''"),
                ("mime_type", "TEXT NOT NULL DEFAULT ''"),
                ("file_ext", "TEXT NOT NULL DEFAULT ''"),
                ("file_size", "INTEGER NOT NULL DEFAULT 0"),
                ("storage_relpath", "TEXT NOT NULL DEFAULT ''"),
                ("source_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("content_card_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("summary", "TEXT NOT NULL DEFAULT ''"),
                ("created_by_tool", "TEXT NOT NULL DEFAULT ''"),
                ("version_of_generated_id", "TEXT NOT NULL DEFAULT ''"),
                ("version_no", "INTEGER NOT NULL DEFAULT 1"),
                ("delivery_status", "TEXT NOT NULL DEFAULT 'pending'"),
                ("last_used_at", "INTEGER NOT NULL DEFAULT 0"),
            ]
            for column_name, column_definition in generated_columns:
                self._ensure_column(
                    conn=conn,
                    table_name="generated_files",
                    column_name=column_name,
                    column_definition=column_definition,
                )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_profile_character_time
                ON chat_messages(profile_user_id, character_pack_id, timestamp);

                CREATE INDEX IF NOT EXISTS idx_chat_profile_character_date_time
                ON chat_messages(profile_user_id, character_pack_id, date_label, timestamp);

                CREATE INDEX IF NOT EXISTS idx_summary_profile_character_time
                ON memory_summaries(profile_user_id, character_pack_id, timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_summary_semanticized
                ON memory_summaries(profile_user_id, session_id, is_semanticized, timestamp DESC);

                CREATE INDEX IF NOT EXISTS idx_semantic_profile_character_time
                ON memory_semantic_summaries(
                    profile_user_id,
                    character_pack_id,
                    last_reinforced_ts DESC,
                    importance DESC,
                    timestamp DESC
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_profile_character_updated
                ON chat_sessions(profile_user_id, character_pack_id, updated_at DESC, created_at DESC);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_persona_cards_profile_status
                ON persona_cards(profile_user_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_media_assets_profile_session_status
                ON user_media_assets(profile_user_id, session_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_media_assets_profile_type_status
                ON user_media_assets(profile_user_id, asset_type, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_media_assets_profile_container_status
                ON user_media_assets(profile_user_id, container_type, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_status
                ON attachment_inbox_items(profile_user_id, session_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_kind_status
                ON attachment_inbox_items(profile_user_id, session_id, kind, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_inbox_profile_session_handle
                ON attachment_inbox_items(profile_user_id, session_id, attachment_handle)
                WHERE attachment_handle != ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_generated_files_profile_session_status
                ON generated_files(profile_user_id, session_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_files_profile_session_handle
                ON generated_files(profile_user_id, session_id, generated_handle)
                WHERE generated_handle != ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workspace_file_states_focus
                ON workspace_file_states(
                    profile_user_id,
                    session_id,
                    focus_rank,
                    updated_at DESC
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_desktop_music_timeline_source
                ON desktop_music_timelines(profile_user_id, session_id, source_id)
                WHERE source_id != ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_desktop_music_timeline_session_status
                ON desktop_music_timelines(profile_user_id, session_id, status, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_workspaces_profile_session_status
                ON task_workspaces(profile_user_id, session_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_workspaces_profile_status
                ON task_workspaces(profile_user_id, status, updated_at DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_workspace_events_task_time
                ON task_workspace_events(task_id, created_at ASC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_workspace_events_profile_session_status
                ON task_workspace_events(profile_user_id, session_id, status, created_at ASC)
                """
            )
            self._normalize_legacy_gift_rows(conn=conn)

    def _ensure_column(
        self,
        *,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name in existing_columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def _normalize_legacy_gift_rows(self, *, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE user_media_assets SET status = 'pending' WHERE status = 'offered'")
        conn.execute("UPDATE user_media_assets SET status = 'kept' WHERE status = 'saved'")
        conn.execute(
            """
            UPDATE user_media_assets
            SET asset_type = CASE
                WHEN media_kind IN ('bgm', 'audio', 'music') THEN 'audio'
                WHEN media_kind = 'image' THEN 'image'
                WHEN media_kind = 'virtual' THEN 'virtual'
                ELSE asset_type
            END
            WHERE TRIM(COALESCE(asset_type, '')) = ''
            """
        )
        conn.execute(
            """
            UPDATE user_media_assets
            SET origin_event_type = 'upload'
            WHERE TRIM(COALESCE(origin_event_type, '')) = ''
              AND TRIM(COALESCE(storage_relpath, '')) != ''
            """
        )
        conn.execute(
            """
            UPDATE user_media_assets
            SET source_ids_json = '[]'
            WHERE TRIM(COALESCE(source_ids_json, '')) = ''
            """
        )
        conn.execute(
            """
            UPDATE user_media_assets
            SET payload_json = '{}'
            WHERE TRIM(COALESCE(payload_json, '')) = ''
            """
        )
        conn.execute(
            """
            UPDATE user_media_assets
            SET last_touched_at = updated_at
            WHERE COALESCE(last_touched_at, 0) = 0
            """
        )
        rows = conn.execute(
            """
            SELECT asset_id, asset_type, status, payload_json, container_type, container_key, container_name, artifact_flags_json
            FROM user_media_assets
            """
        ).fetchall()
        for row in rows:
            payload = self._safe_json_loads(row["payload_json"], fallback={})
            if not isinstance(payload, dict):
                payload = {}
            container = self._derive_container_fields(
                asset_type=row["asset_type"],
                status=row["status"],
                payload=payload,
                container_type=row["container_type"],
                container_key=row["container_key"],
                container_name=row["container_name"],
                artifact_flags=self._safe_json_loads(row["artifact_flags_json"], fallback={}),
            )
            conn.execute(
                """
                UPDATE user_media_assets
                SET container_type = ?, container_key = ?, container_name = ?, artifact_flags_json = ?
                WHERE asset_id = ?
                """,
                (
                    container["container_type"],
                    container["container_key"],
                    container["container_name"],
                    json.dumps(container["artifact_flags"], ensure_ascii=False),
                    str(row["asset_id"]),
                ),
            )

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_messages")
            conn.execute("DELETE FROM memory_summaries")
            conn.execute("DELETE FROM memory_semantic_summaries")
            conn.execute("DELETE FROM eval_turns")
            conn.execute("DELETE FROM chat_sessions")
            conn.execute("DELETE FROM reminders")
            conn.execute("DELETE FROM user_media_assets")
            conn.execute("DELETE FROM vision_observations")
            conn.execute("DELETE FROM persona_cards")
            conn.execute("DELETE FROM persona_events")
            conn.execute("DELETE FROM persona_session_states")
            conn.execute("DELETE FROM attachment_inbox_items")
            conn.execute("DELETE FROM workspace_file_states")
            conn.execute("DELETE FROM desktop_music_timelines")

    def _build_default_session_title(
        self,
        conn: sqlite3.Connection,
        profile_user_id: str,
        character_pack_id: str = "",
    ) -> str:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM chat_sessions
            WHERE profile_user_id = ? AND character_pack_id = ?
            """,
            (str(profile_user_id), normalized_character_pack_id),
        ).fetchone()
        count = int(row["cnt"] or 0) if row else 0
        if count <= 0:
            return "新的对话"
        return f"新的对话 {count + 1}"

    def ensure_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        display_title: str | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_profile_user_id = str(profile_user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        if not normalized_profile_user_id or not normalized_session_id:
            raise ValueError("profile_user_id and session_id are required")

        effective_ts = int(timestamp or time.time())
        requested_title = str(display_title or "").strip()

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE session_id = ? AND profile_user_id = ?
                LIMIT 1
                """,
                (normalized_session_id, normalized_profile_user_id),
            ).fetchone()
            if existing is not None:
                if requested_title:
                    conn.execute(
                        """
                        UPDATE chat_sessions
                        SET display_title = ?, character_pack_id = ?, updated_at = ?
                        WHERE session_id = ? AND profile_user_id = ?
                        """,
                        (
                            requested_title,
                            normalized_character_pack_id,
                            effective_ts,
                            normalized_session_id,
                            normalized_profile_user_id,
                        ),
                    )
                    return self._row_to_session(
                        {
                            **dict(existing),
                            "display_title": requested_title,
                            "character_pack_id": normalized_character_pack_id,
                            "updated_at": effective_ts,
                        }
                    )

                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET character_pack_id = ?, updated_at = ?
                    WHERE session_id = ? AND profile_user_id = ?
                    """,
                    (
                        normalized_character_pack_id,
                        effective_ts,
                        normalized_session_id,
                        normalized_profile_user_id,
                    ),
                )
                return self._row_to_session({
                    **dict(existing),
                    "character_pack_id": normalized_character_pack_id,
                    "updated_at": effective_ts,
                })

            resolved_title = requested_title or self._build_default_session_title(
                conn,
                normalized_profile_user_id,
                normalized_character_pack_id,
            )
            payload = {
                "session_id": normalized_session_id,
                "profile_user_id": normalized_profile_user_id,
                "character_pack_id": normalized_character_pack_id,
                "display_title": resolved_title,
                "created_at": effective_ts,
                "updated_at": effective_ts,
            }
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, profile_user_id, character_pack_id, display_title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["session_id"],
                    payload["profile_user_id"],
                    payload["character_pack_id"],
                    payload["display_title"],
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
        return self._row_to_session(payload)

    def rename_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        display_title: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_profile_user_id = str(profile_user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_title = str(display_title or "").strip()
        if not normalized_profile_user_id or not normalized_session_id or not normalized_title:
            return None

        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE session_id = ? AND profile_user_id = ?
                LIMIT 1
                """,
                (normalized_session_id, normalized_profile_user_id),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE chat_sessions
                SET display_title = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    normalized_title,
                    effective_ts,
                    normalized_session_id,
                    normalized_profile_user_id,
                ),
            )
        return self._row_to_session({**dict(row), "display_title": normalized_title, "updated_at": effective_ts})

    def get_session(self, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ? AND session_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id)),
            ).fetchone()
        return self._row_to_session(dict(row)) if row else None

    def get_character_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_character_pack_id),
            ).fetchone()
        return self._row_to_session(dict(row)) if row else None

    def set_session_gift_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        asset_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        session = self.ensure_session(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=timestamp,
        )
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET current_gift_focus_asset_id = ?, current_gift_focus_updated_at = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    str(asset_id or "").strip(),
                    effective_ts,
                    effective_ts,
                    str(session_id),
                    str(profile_user_id),
                ),
            )
        return {
            **session,
            "current_gift_focus_asset_id": str(asset_id or "").strip(),
            "current_gift_focus_updated_at": effective_ts,
            "updated_at": effective_ts,
        }

    def clear_session_gift_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        session = self.get_session(profile_user_id, session_id)
        if session is None:
            return None
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET current_gift_focus_asset_id = '', current_gift_focus_updated_at = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    effective_ts,
                    effective_ts,
                    str(session_id),
                    str(profile_user_id),
                ),
            )
        return {
            **session,
            "current_gift_focus_asset_id": "",
            "current_gift_focus_updated_at": effective_ts,
            "updated_at": effective_ts,
        }

    def _backfill_profile_sessions(
        self,
        *,
        conn: sqlite3.Connection,
        profile_user_id: str,
        character_pack_id: str | None = None,
    ) -> None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        existing_ids = {
            str(row["session_id"])
            for row in conn.execute(
                """
                SELECT session_id FROM chat_sessions
                WHERE profile_user_id = ?
                """,
                (str(profile_user_id),),
            ).fetchall()
        }
        legacy_rows = conn.execute(
            """
            SELECT session_id, character_pack_id, MIN(timestamp) AS created_at, MAX(timestamp) AS updated_at
            FROM chat_messages
            WHERE profile_user_id = ?
              AND (? IS NULL OR character_pack_id = ?)
            GROUP BY session_id, character_pack_id
            ORDER BY MIN(timestamp) ASC, session_id ASC
            """,
            (str(profile_user_id), normalized_character_pack_id, normalized_character_pack_id),
        ).fetchall()

        for row in legacy_rows:
            session_id = str(row["session_id"] or "").strip()
            if not session_id or session_id in existing_ids:
                continue
            row_character_pack_id = normalize_character_pack_id(row["character_pack_id"])
            created_at = int(row["created_at"] or time.time())
            updated_at = int(row["updated_at"] or created_at)
            title = self._build_default_session_title(conn, str(profile_user_id), row_character_pack_id)
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, profile_user_id, character_pack_id, display_title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(profile_user_id),
                    row_character_pack_id,
                    title,
                    created_at,
                    updated_at,
                ),
            )
            existing_ids.add(session_id)

    def list_sessions(
        self,
        profile_user_id: str,
        limit: int = 50,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            self._backfill_profile_sessions(
                conn=conn,
                profile_user_id=str(profile_user_id),
                character_pack_id=normalized_character_pack_id,
            )
            rows = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY updated_at DESC, created_at DESC, session_id DESC
                LIMIT ?
                """,
                (
                    str(profile_user_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return [self._row_to_session(dict(row)) for row in rows]

    def get_session_messages(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM chat_messages
                    WHERE profile_user_id = ? AND session_id = ?
                      AND (? IS NULL OR character_pack_id = ?)
                    ORDER BY seq_no DESC
                    LIMIT ?
                )
                ORDER BY seq_no ASC
                """,
                (
                    str(profile_user_id),
                    str(session_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def set_message_write_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._message_write_callback = callback

    def _notify_message_write(self, message: dict[str, Any]) -> None:
        callback = self._message_write_callback
        if callback is None:
            return
        try:
            callback(dict(message))
        except Exception as exc:
            logger.warning("message write callback failed: %s", exc)

    def get_memory_timeline_messages(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE profile_user_id = ?
                  AND character_pack_id = ?
                  AND date_label >= ?
                  AND date_label <= ?
                ORDER BY timestamp ASC, session_id ASC, seq_no ASC, source_id ASC
                """,
                (
                    str(profile_user_id),
                    normalized_character_pack_id,
                    str(date_from),
                    str(date_to),
                ),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def list_memory_timeline_days(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT profile_user_id, character_pack_id, date_label
                FROM chat_messages
                WHERE TRIM(COALESCE(date_label, '')) != ''
                GROUP BY profile_user_id, character_pack_id, date_label
                ORDER BY profile_user_id ASC, character_pack_id ASC, date_label ASC
                """
            ).fetchall()
        return [
            {
                "profile_user_id": str(row["profile_user_id"] or ""),
                "character_pack_id": normalize_character_pack_id(row["character_pack_id"]),
                "date_label": str(row["date_label"] or ""),
            }
            for row in rows
        ]

    def get_memory_timeline_stats(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    MIN(timestamp) AS first_timestamp,
                    MAX(timestamp) AS last_timestamp,
                    COUNT(*) AS message_count,
                    COUNT(DISTINCT date_label) AS active_day_count
                FROM chat_messages
                WHERE profile_user_id = ?
                  AND character_pack_id = ?
                """,
                (str(profile_user_id), normalized_character_pack_id),
            ).fetchone()
        first_timestamp = int(row["first_timestamp"]) if row and row["first_timestamp"] is not None else 0
        last_timestamp = int(row["last_timestamp"]) if row and row["last_timestamp"] is not None else 0
        return {
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "message_count": int(row["message_count"] or 0) if row else 0,
            "active_day_count": int(row["active_day_count"] or 0) if row else 0,
        }

    def next_seq_no(
        self,
        session_id: str,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        clauses = ["session_id = ?"]
        params: list[Any] = [str(session_id)]
        if str(profile_user_id or "").strip():
            clauses.append("profile_user_id = ?")
            params.append(str(profile_user_id))
        if normalized_character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalized_character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COALESCE(MAX(seq_no), 0) AS max_seq FROM chat_messages WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row["max_seq"]) + 1

    def add_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        role: str,
        content: str,
        timestamp: int | None = None,
        date_label: str | None = None,
        time_of_day: str | None = None,
        semantic_tags: list[str] | None = None,
        memory_metadata: dict[str, Any] | None = None,
        index_in_vector: bool = True,
    ) -> dict[str, Any]:
        ts = int(timestamp or time.time())
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        record = {
            "source_id": str(uuid.uuid4()),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "character_pack_id": normalized_character_pack_id,
            "seq_no": self.next_seq_no(
                session_id,
                profile_user_id=profile_user_id,
                character_pack_id=normalized_character_pack_id,
            ),
            "role": str(role),
            "content": str(content),
            "timestamp": ts,
            "date_label": str(date_label or timestamp_to_date_label(ts)),
            "time_of_day": str(time_of_day or infer_time_of_day(ts)),
            "semantic_tags_json": json.dumps(semantic_tags or [], ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "index_in_vector": 1 if bool(index_in_vector) else 0,
            "is_summarized": 0,
            "summary_id": "",
        }
        self.ensure_session(
            profile_user_id=record["profile_user_id"],
            session_id=record["session_id"],
            character_pack_id=record["character_pack_id"],
            timestamp=record["timestamp"],
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    source_id, profile_user_id, session_id, character_pack_id, seq_no, role, content,
                    timestamp, date_label, time_of_day, semantic_tags_json,
                    memory_metadata_json, index_in_vector, is_summarized, summary_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["source_id"],
                    record["profile_user_id"],
                    record["session_id"],
                    record["character_pack_id"],
                    record["seq_no"],
                    record["role"],
                    record["content"],
                    record["timestamp"],
                    record["date_label"],
                    record["time_of_day"],
                    record["semantic_tags_json"],
                    record["memory_metadata_json"],
                    record["index_in_vector"],
                    record["is_summarized"],
                    record["summary_id"],
                ),
            )
        message = self._row_to_message(record)
        self._notify_message_write(message)
        return message

    def update_message_semantic_tags(self, source_id: str, semantic_tags: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET semantic_tags_json = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(semantic_tags or [], ensure_ascii=False),
                    str(source_id),
                ),
            )

    def update_message_memory_metadata(self, source_id: str, memory_metadata: dict[str, Any] | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET memory_metadata_json = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(memory_metadata or {}, ensure_ascii=False),
                    str(source_id),
                ),
            )
        record = self.get_message_by_source_id(source_id)
        if record is not None:
            self._notify_message_write(record)

    def update_message_index_in_vector(self, source_id: str, index_in_vector: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_messages
                SET index_in_vector = ?
                WHERE source_id = ?
                """,
                (
                    1 if bool(index_in_vector) else 0,
                    str(source_id),
                ),
            )

    def get_unsummarized_messages(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY seq_no ASC
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def get_unsummarized_count(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return int(row["cnt"])

    def get_oldest_unsummarized_batch(
        self,
        session_id: str,
        limit: int = 20,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ? AND is_summarized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY seq_no ASC
                LIMIT ?
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def mark_messages_summarized(self, source_ids: list[str], summary_id: str) -> None:
        if not source_ids:
            return
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE chat_messages
                SET is_summarized = 1, summary_id = ?
                WHERE source_id IN ({placeholders})
                """,
                [summary_id, *source_ids],
            )

    def add_summary(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int,
        date_label: str,
        time_of_day: str,
        period_label: str,
        event_type: str,
        importance: float,
        diary_summary: str,
        key_events: list[str],
        core_facts: list[str],
        semantic_tags: list[str],
        source_start_seq: int,
        source_end_seq: int,
        source_ids: list[str],
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        payload = {
            "summary_id": f"summary::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "character_pack_id": normalized_character_pack_id,
            "timestamp": int(timestamp),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "period_label": str(period_label),
            "event_type": str(event_type),
            "importance": float(max(0.0, min(1.0, importance))),
            "diary_summary": str(diary_summary),
            "key_events_json": json.dumps(key_events, ensure_ascii=False),
            "core_facts_json": json.dumps(core_facts, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_start_seq": int(source_start_seq),
            "source_end_seq": int(source_end_seq),
            "source_ids_json": json.dumps(source_ids, ensure_ascii=False),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_summaries (
                    summary_id, profile_user_id, session_id, character_pack_id, timestamp, date_label, time_of_day,
                    period_label, event_type, importance, diary_summary, key_events_json,
                    core_facts_json, semantic_tags_json, memory_metadata_json, is_semanticized, semantic_id,
                    source_start_seq, source_end_seq, source_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["summary_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["character_pack_id"],
                    payload["timestamp"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["period_label"],
                    payload["event_type"],
                    payload["importance"],
                    payload["diary_summary"],
                    payload["key_events_json"],
                    payload["core_facts_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    0,
                    "",
                    payload["source_start_seq"],
                    payload["source_end_seq"],
                    payload["source_ids_json"],
                ),
            )
        return self._row_to_summary(payload)

    def get_recent_summaries(
        self,
        profile_user_id: str,
        limit: int = 5,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]

    def get_visible_episodic_summaries(
        self,
        profile_user_id: str,
        limit: int = 10,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE profile_user_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]

    def get_unsemanticized_summary_count(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> int:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM memory_summaries
                WHERE session_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return int(row["cnt"])

    def get_oldest_unsemanticized_summaries(
        self,
        session_id: str,
        limit: int = 5,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_summaries
                WHERE session_id = ? AND is_semanticized = 0
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_summary(dict(row)) for row in rows]

    def mark_summaries_semanticized(self, summary_ids: list[str], semantic_id: str) -> None:
        if not summary_ids:
            return
        placeholders = ",".join("?" for _ in summary_ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE memory_summaries
                SET is_semanticized = 1, semantic_id = ?
                WHERE summary_id IN ({placeholders})
                """,
                [str(semantic_id), *summary_ids],
            )

    def add_semantic_summary(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        timestamp: int,
        period_start_ts: int,
        period_end_ts: int,
        date_label: str,
        time_of_day: str,
        importance: float,
        semantic_summary: str,
        stable_facts: list[str],
        recurring_topics: list[str],
        important_people: list[str],
        open_loops: list[str],
        semantic_tags: list[str],
        source_summary_ids: list[str],
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        payload = {
            "semantic_id": f"semantic::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "character_pack_id": normalized_character_pack_id,
            "created_at": int(time.time()),
            "timestamp": int(timestamp),
            "period_start_ts": int(period_start_ts),
            "period_end_ts": int(period_end_ts),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "importance": float(max(0.0, min(1.0, importance))),
            "semantic_summary": str(semantic_summary),
            "stable_facts_json": json.dumps(stable_facts, ensure_ascii=False),
            "recurring_topics_json": json.dumps(recurring_topics, ensure_ascii=False),
            "important_people_json": json.dumps(important_people, ensure_ascii=False),
            "open_loops_json": json.dumps(open_loops, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_summary_ids_json": json.dumps(source_summary_ids, ensure_ascii=False),
            "reinforcement_count": 1,
            "last_reinforced_ts": int(timestamp),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_semantic_summaries (
                    semantic_id, profile_user_id, session_id, character_pack_id, created_at, timestamp,
                    period_start_ts, period_end_ts, date_label, time_of_day, importance,
                    semantic_summary, stable_facts_json, recurring_topics_json,
                    important_people_json, open_loops_json, semantic_tags_json, memory_metadata_json,
                    source_summary_ids_json, reinforcement_count, last_reinforced_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["semantic_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["character_pack_id"],
                    payload["created_at"],
                    payload["timestamp"],
                    payload["period_start_ts"],
                    payload["period_end_ts"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["importance"],
                    payload["semantic_summary"],
                    payload["stable_facts_json"],
                    payload["recurring_topics_json"],
                    payload["important_people_json"],
                    payload["open_loops_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    payload["source_summary_ids_json"],
                    payload["reinforcement_count"],
                    payload["last_reinforced_ts"],
                ),
            )
        return self._row_to_semantic_summary(payload)

    def update_semantic_summary(
        self,
        *,
        semantic_id: str,
        timestamp: int,
        period_start_ts: int,
        period_end_ts: int,
        date_label: str,
        time_of_day: str,
        importance: float,
        semantic_summary: str,
        stable_facts: list[str],
        recurring_topics: list[str],
        important_people: list[str],
        open_loops: list[str],
        semantic_tags: list[str],
        source_summary_ids: list[str],
        reinforcement_count: int,
        last_reinforced_ts: int,
        memory_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = {
            "semantic_id": str(semantic_id),
            "timestamp": int(timestamp),
            "period_start_ts": int(period_start_ts),
            "period_end_ts": int(period_end_ts),
            "date_label": str(date_label),
            "time_of_day": str(time_of_day),
            "importance": float(max(0.0, min(1.0, importance))),
            "semantic_summary": str(semantic_summary),
            "stable_facts_json": json.dumps(stable_facts, ensure_ascii=False),
            "recurring_topics_json": json.dumps(recurring_topics, ensure_ascii=False),
            "important_people_json": json.dumps(important_people, ensure_ascii=False),
            "open_loops_json": json.dumps(open_loops, ensure_ascii=False),
            "semantic_tags_json": json.dumps(semantic_tags, ensure_ascii=False),
            "memory_metadata_json": json.dumps(memory_metadata or {}, ensure_ascii=False),
            "source_summary_ids_json": json.dumps(source_summary_ids, ensure_ascii=False),
            "reinforcement_count": max(1, int(reinforcement_count)),
            "last_reinforced_ts": int(last_reinforced_ts),
        }
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_semantic_summaries
                SET timestamp = ?, period_start_ts = ?, period_end_ts = ?, date_label = ?, time_of_day = ?,
                    importance = ?, semantic_summary = ?, stable_facts_json = ?, recurring_topics_json = ?,
                    important_people_json = ?, open_loops_json = ?, semantic_tags_json = ?,
                    memory_metadata_json = ?, source_summary_ids_json = ?, reinforcement_count = ?, last_reinforced_ts = ?
                WHERE semantic_id = ?
                """,
                (
                    payload["timestamp"],
                    payload["period_start_ts"],
                    payload["period_end_ts"],
                    payload["date_label"],
                    payload["time_of_day"],
                    payload["importance"],
                    payload["semantic_summary"],
                    payload["stable_facts_json"],
                    payload["recurring_topics_json"],
                    payload["important_people_json"],
                    payload["open_loops_json"],
                    payload["semantic_tags_json"],
                    payload["memory_metadata_json"],
                    payload["source_summary_ids_json"],
                    payload["reinforcement_count"],
                    payload["last_reinforced_ts"],
                    payload["semantic_id"],
                ),
            )
        return self.get_semantic_summary_by_id(payload["semantic_id"])

    def get_recent_semantic_summaries(
        self,
        profile_user_id: str,
        limit: int = 3,
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_semantic_summaries
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY last_reinforced_ts DESC, importance DESC, timestamp DESC
                LIMIT ?
                """,
                (profile_user_id, normalized_character_pack_id, normalized_character_pack_id, int(limit)),
            ).fetchall()
        return [self._row_to_semantic_summary(dict(row)) for row in rows]

    def count_vectorizable_records(self) -> int:
        with self._connect() as conn:
            chat_count = int(conn.execute("SELECT COUNT(*) FROM chat_messages WHERE index_in_vector = 1").fetchone()[0] or 0)
            summary_count = int(conn.execute("SELECT COUNT(*) FROM memory_summaries").fetchone()[0] or 0)
            semantic_count = int(conn.execute("SELECT COUNT(*) FROM memory_semantic_summaries").fetchone()[0] or 0)
        return chat_count + summary_count + semantic_count

    def iter_messages_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="chat_messages",
            converter=self._row_to_message,
            batch_size=batch_size,
            where_clause="index_in_vector = 1",
        )

    def iter_summaries_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="memory_summaries",
            converter=self._row_to_summary,
            batch_size=batch_size,
        )

    def iter_semantic_summaries_for_vector_reindex(self, batch_size: int = 64):
        yield from self._iter_table_batches(
            table_name="memory_semantic_summaries",
            converter=self._row_to_semantic_summary,
            batch_size=batch_size,
        )

    def get_message_by_source_id(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return self._row_to_message(dict(row)) if row else None

    def get_message_by_seq_no(
        self,
        session_id: str,
        seq_no: int,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["session_id = ?", "seq_no = ?"]
        params: list[Any] = [str(session_id), int(seq_no)]
        normalized_profile_user_id = str(profile_user_id or "").strip()
        if normalized_profile_user_id:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile_user_id)
        if character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalize_character_pack_id(character_pack_id))
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM chat_messages
                WHERE {' AND '.join(clauses)}
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._row_to_message(dict(row)) if row else None

    def get_summary_by_id(self, summary_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_summaries WHERE summary_id = ?",
                (summary_id,),
            ).fetchone()
        return self._row_to_summary(dict(row)) if row else None

    def get_semantic_summary_by_id(self, semantic_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_semantic_summaries WHERE semantic_id = ?",
                (semantic_id,),
            ).fetchone()
        return self._row_to_semantic_summary(dict(row)) if row else None

    def get_record_by_source_id(self, source_id: str) -> dict[str, Any] | None:
        if source_id.startswith("semantic::"):
            return self.get_semantic_summary_by_id(source_id)
        if source_id.startswith("summary::"):
            return self.get_summary_by_id(source_id)
        return self.get_message_by_source_id(source_id)

    def get_context_slice(
        self,
        session_id: str,
        center_seq_no: int,
        window: int = 1,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        start_seq = max(1, int(center_seq_no) - int(window))
        end_seq = int(center_seq_no) + int(window)
        clauses = ["session_id = ?", "seq_no BETWEEN ? AND ?"]
        params: list[Any] = [str(session_id), start_seq, end_seq]
        normalized_profile_user_id = str(profile_user_id or "").strip()
        if normalized_profile_user_id:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile_user_id)
        if character_pack_id is not None:
            clauses.append("character_pack_id = ?")
            params.append(normalize_character_pack_id(character_pack_id))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM chat_messages
                WHERE {' AND '.join(clauses)}
                ORDER BY seq_no ASC
                """,
                params,
            ).fetchall()
        return [self._row_to_message(dict(row)) for row in rows]

    def append_eval_turn(
        self,
        *,
        trace_id: str,
        session_id: str,
        profile_user_id: str,
        character_pack_id: str = "",
        user_message: str,
        router_json: dict[str, Any],
        verifier_json: dict[str, Any],
        final_json: dict[str, Any],
    ) -> None:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_turns (
                    trace_id, created_at, session_id, profile_user_id, character_pack_id, user_message,
                    router_json, verifier_json, final_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    int(time.time()),
                    session_id,
                    profile_user_id,
                    normalized_character_pack_id,
                    user_message,
                    json.dumps(router_json, ensure_ascii=False),
                    json.dumps(verifier_json, ensure_ascii=False),
                    json.dumps(final_json, ensure_ascii=False),
                ),
            )

    def add_reminder(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        content: str,
        due_ts: int,
        raw_time_text: str = "",
    ) -> dict[str, Any]:
        reminder = {
            "reminder_id": f"reminder::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "content": str(content),
            "due_ts": int(due_ts),
            "date_label": timestamp_to_date_label(due_ts),
            "time_of_day": infer_time_of_day(due_ts),
            "raw_time_text": str(raw_time_text or ""),
            "status": "pending",
            "created_at": int(time.time()),
            "fired_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reminders (
                    reminder_id, profile_user_id, session_id, content, due_ts,
                    date_label, time_of_day, raw_time_text, status, created_at, fired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder["reminder_id"],
                    reminder["profile_user_id"],
                    reminder["session_id"],
                    reminder["content"],
                    reminder["due_ts"],
                    reminder["date_label"],
                    reminder["time_of_day"],
                    reminder["raw_time_text"],
                    reminder["status"],
                    reminder["created_at"],
                    reminder["fired_at"],
                ),
            )
        return dict(reminder)

    def claim_due_reminders(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        now_ts: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        effective_now_ts = int(now_ts or time.time())
        query = [
            "SELECT * FROM reminders",
            "WHERE profile_user_id = ?",
            "AND status = 'pending'",
            "AND due_ts <= ?",
        ]
        params: list[Any] = [str(profile_user_id), effective_now_ts]
        if session_id is not None:
            query.append("AND session_id = ?")
            params.append(str(session_id))
        query.append("ORDER BY due_ts ASC, created_at ASC LIMIT ?")
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
            reminder_ids = [str(row["reminder_id"]) for row in rows]
            if reminder_ids:
                placeholders = ",".join("?" for _ in reminder_ids)
                conn.execute(
                    f"""
                    UPDATE reminders
                    SET status = 'fired', fired_at = ?
                    WHERE reminder_id IN ({placeholders})
                    """,
                    [effective_now_ts, *reminder_ids],
                )
        return [self._row_to_reminder(dict(row), status="fired", fired_at=effective_now_ts) for row in rows]

    def list_reminders(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        status: str = "pending",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_status = str(status or "pending").strip().lower() or "pending"
        query = [
            "SELECT * FROM reminders",
            "WHERE profile_user_id = ?",
            "AND status = ?",
        ]
        params: list[Any] = [str(profile_user_id), normalized_status]
        if session_id is not None:
            query.append("AND session_id = ?")
            params.append(str(session_id))
        query.append("ORDER BY due_ts ASC, created_at ASC LIMIT ?")
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_reminder(dict(row)) for row in rows]

    def cancel_reminder(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        reminder_id: str,
        cancelled_at: int | None = None,
    ) -> dict[str, Any] | None:
        effective_cancelled_at = int(cancelled_at or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM reminders
                WHERE reminder_id = ? AND profile_user_id = ? AND session_id = ? AND status = 'pending'
                LIMIT 1
                """,
                (
                    str(reminder_id),
                    str(profile_user_id),
                    str(session_id),
                ),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE reminders
                SET status = 'cancelled', fired_at = ?
                WHERE reminder_id = ?
                """,
                (
                    effective_cancelled_at,
                    str(reminder_id),
                ),
            )
        return self._row_to_reminder(dict(row), status="cancelled", fired_at=effective_cancelled_at)

    def add_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: str = "",
        kind: str = "file",
        status: str = "pending_observation",
        origin_name: str = "",
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        storage_relpath: str = "",
        source_event_id: str = "",
        source_message_id: str = "",
        summary_title: str = "",
        short_hint: str = "",
        detail: dict[str, Any] | None = None,
        error_message: str = "",
        timestamp: int | None = None,
        expires_at: int = 0,
        attachment_id: str = "",
        attachment_handle: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_id = str(attachment_id or "").strip() or f"attachment::{uuid.uuid4()}"
        normalized_kind = self._normalize_attachment_kind(kind)
        payload = {
            "attachment_id": normalized_id,
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "source": str(source or "").strip(),
            "kind": normalized_kind,
            "attachment_handle": str(attachment_handle or "").strip(),
            "sequence_no": 0,
            "status": self._normalize_attachment_status(status),
            "origin_name": str(origin_name or "").strip(),
            "mime_type": str(mime_type or "").strip(),
            "file_ext": str(file_ext or "").strip(),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
            "source_event_id": str(source_event_id or "").strip(),
            "source_message_id": str(source_message_id or "").strip(),
            "summary_title": str(summary_title or "").strip(),
            "short_hint": str(short_hint or "").strip(),
            "detail_json": json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False),
            "error_message": str(error_message or "").strip(),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_used_at": 0,
            "focus_rank": 0,
            "expires_at": max(0, int(expires_at or 0)),
        }
        auto_handle = not payload["attachment_handle"]
        max_attempts = 8 if auto_handle else 1
        next_sequence_no: int | None = None
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                for _ in range(max_attempts):
                    if auto_handle:
                        if next_sequence_no is None:
                            next_sequence_no = self._next_attachment_sequence_no(
                                conn=conn,
                                profile_user_id=payload["profile_user_id"],
                                session_id=payload["session_id"],
                                kind=payload["kind"],
                            )
                        payload["sequence_no"] = next_sequence_no
                        payload["attachment_handle"] = f"{self._attachment_handle_prefix(payload['kind'])}_{next_sequence_no:03d}"
                    else:
                        payload["sequence_no"] = self._sequence_no_from_attachment_handle(payload["attachment_handle"])
                    try:
                        conn.execute(
                            """
                            INSERT INTO attachment_inbox_items (
                                attachment_id, profile_user_id, session_id, source, kind,
                                attachment_handle, sequence_no, status,
                                origin_name, mime_type, file_ext, file_size, storage_relpath,
                                source_event_id, source_message_id, summary_title, short_hint,
                                detail_json, error_message, created_at, updated_at, last_used_at,
                                focus_rank, expires_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                payload["attachment_id"],
                                payload["profile_user_id"],
                                payload["session_id"],
                                payload["source"],
                                payload["kind"],
                                payload["attachment_handle"],
                                payload["sequence_no"],
                                payload["status"],
                                payload["origin_name"],
                                payload["mime_type"],
                                payload["file_ext"],
                                payload["file_size"],
                                payload["storage_relpath"],
                                payload["source_event_id"],
                                payload["source_message_id"],
                                payload["summary_title"],
                                payload["short_hint"],
                                payload["detail_json"],
                                payload["error_message"],
                                payload["created_at"],
                                payload["updated_at"],
                                payload["last_used_at"],
                                payload["focus_rank"],
                                payload["expires_at"],
                            ),
                        )
                        break
                    except sqlite3.IntegrityError as exc:
                        if auto_handle and self._is_attachment_handle_conflict(exc):
                            next_sequence_no = int(payload["sequence_no"] or 0) + 1
                            continue
                        raise
                else:
                    raise RuntimeError("attachment handle allocation failed after retries")
        return self._row_to_attachment_inbox_item(payload)

    def _next_attachment_sequence_no(
        self,
        *,
        conn: sqlite3.Connection,
        profile_user_id: str,
        session_id: str,
        kind: str,
    ) -> int:
        prefix = self._attachment_handle_prefix(kind)
        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) AS max_seq
            FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ? AND attachment_handle LIKE ?
            """,
            (str(profile_user_id), str(session_id), f"{prefix}_%"),
        ).fetchone()
        return int(row["max_seq"] or 0) + 1 if row else 1

    def _is_attachment_handle_conflict(self, exc: sqlite3.IntegrityError) -> bool:
        message = str(exc or "").lower()
        return "attachment_inbox_items.profile_user_id" in message and "attachment_handle" in message

    def update_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        status: str | None = None,
        summary_title: str | None = None,
        short_hint: str | None = None,
        detail: dict[str, Any] | None = None,
        error_message: str | None = None,
        mime_type: str | None = None,
        file_ext: str | None = None,
        file_size: int | None = None,
        storage_relpath: str | None = None,
        last_used_at: int | None = None,
        focus_rank: int | None = None,
        expires_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None

        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_attachment_status(status))
        if summary_title is not None:
            fields.append("summary_title = ?")
            params.append(str(summary_title or "").strip())
        if short_hint is not None:
            fields.append("short_hint = ?")
            params.append(str(short_hint or "").strip())
        if detail is not None:
            fields.append("detail_json = ?")
            params.append(json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False))
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(str(error_message or "").strip())
        if mime_type is not None:
            fields.append("mime_type = ?")
            params.append(str(mime_type or "").strip())
        if file_ext is not None:
            fields.append("file_ext = ?")
            params.append(str(file_ext or "").strip())
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(max(0, int(file_size or 0)))
        if storage_relpath is not None:
            fields.append("storage_relpath = ?")
            params.append(str(storage_relpath or "").strip())
        if last_used_at is not None:
            fields.append("last_used_at = ?")
            params.append(max(0, int(last_used_at or 0)))
        if focus_rank is not None:
            fields.append("focus_rank = ?")
            params.append(max(0, int(focus_rank or 0)))
        if expires_at is not None:
            fields.append("expires_at = ?")
            params.append(max(0, int(expires_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE attachment_inbox_items
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_attachment_inbox_item(dict(updated)) if updated else None

    def mark_attachment_inbox_item_ready(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
        summary_title: str,
        short_hint: str,
        detail: dict[str, Any],
        focus_batch_seconds: int,
        focus_max_items: int,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None
        effective_ts = int(timestamp or time.time())
        profile_id = str(profile_user_id)
        session = str(session_id)
        threshold = effective_ts - max(0, int(focus_batch_seconds or 0))
        with self._attachment_inbox_write_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET status = 'ready', summary_title = ?, short_hint = ?,
                        detail_json = ?, error_message = '', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                    """,
                    (
                        str(summary_title or "").strip(),
                        str(short_hint or "").strip(),
                        json.dumps(detail if isinstance(detail, dict) else {}, ensure_ascii=False),
                        effective_ts,
                        profile_id,
                        session,
                        normalized_id,
                    ),
                )
                candidates = conn.execute(
                    """
                    SELECT attachment_id
                    FROM (
                        SELECT attachment_id, created_at, sequence_no, updated_at
                        FROM attachment_inbox_items
                        WHERE profile_user_id = ? AND session_id = ? AND status = 'ready'
                          AND COALESCE(NULLIF(updated_at, 0), created_at) >= ?
                        ORDER BY created_at DESC, sequence_no DESC, updated_at DESC
                        LIMIT ?
                    )
                    ORDER BY created_at ASC, sequence_no ASC, updated_at ASC
                    """,
                    (
                        profile_id,
                        session,
                        threshold,
                        max(1, int(focus_max_items or 1)),
                    ),
                ).fetchall()
                attachment_ids = [
                    str(row["attachment_id"] or "").strip()
                    for row in candidates
                    if str(row["attachment_id"] or "").strip()
                ]
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET focus_rank = 0, updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ?
                      AND status IN ('ready', 'pending_observation', 'failed')
                    """,
                    (effective_ts, profile_id, session),
                )
                for rank, focused_id in enumerate(attachment_ids, start=1):
                    conn.execute(
                        """
                        UPDATE attachment_inbox_items
                        SET focus_rank = ?, last_used_at = ?, updated_at = ?
                        WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                          AND status = 'ready'
                        """,
                        (
                            rank,
                            effective_ts,
                            effective_ts,
                            profile_id,
                            session,
                            focused_id,
                        ),
                    )
                updated = conn.execute(
                    """
                    SELECT * FROM attachment_inbox_items
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                    """,
                    (profile_id, session, normalized_id),
                ).fetchone()
        return self._row_to_attachment_inbox_item(dict(updated)) if updated else None

    def get_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_id: str,
    ) -> dict[str, Any] | None:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_attachment_inbox_item(dict(row)) if row else None

    def get_attachment_inbox_item_by_storage_relpath(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        storage_relpath: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        normalized_relpath = str(storage_relpath or "").strip()
        if not normalized_relpath:
            return None
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        query = [
            """
            SELECT * FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ? AND storage_relpath = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id), normalized_relpath]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append("ORDER BY updated_at DESC, created_at DESC LIMIT 1")
        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return self._row_to_attachment_inbox_item(dict(row)) if row else None

    def list_attachment_inbox_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        query = [
            """
            SELECT * FROM attachment_inbox_items
            WHERE profile_user_id = ? AND session_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        raw_kind = str(kind or "").strip()
        normalized_kind = self._normalize_attachment_kind(raw_kind) if raw_kind else "any"
        if normalized_kind and normalized_kind != "any":
            query.append("AND kind = ?")
            params.append(normalized_kind)
        query.append(
            """
            ORDER BY
                CASE WHEN last_used_at > 0 THEN last_used_at ELSE updated_at END DESC,
                updated_at DESC,
                created_at DESC
            LIMIT ?
            """
        )
        params.append(max(1, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_attachment_inbox_item(dict(row)) for row in rows]

    def find_attachment_inbox_item(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        kind: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        matches = self.find_attachment_inbox_item_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=query,
            kind=kind,
            statuses=statuses,
            limit=12,
        )
        if not matches:
            return None
        best_rank = int(matches[0].get("_match_rank") or 99)
        top_matches = [
            item
            for item in matches
            if int(item.get("_match_rank") or 99) == best_rank
        ]
        if len(top_matches) > 1 and best_rank >= 2:
            return None
        return {key: value for key, value in matches[0].items() if key != "_match_rank"}

    def find_attachment_inbox_item_matches(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        kind: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_statuses = self._normalize_attachment_status_list(statuses)
        clauses = [
            "profile_user_id = ?",
            "session_id = ?",
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        raw_kind = str(kind or "").strip()
        normalized_kind = self._normalize_attachment_kind(raw_kind) if raw_kind else "any"
        if normalized_kind and normalized_kind != "any":
            clauses.append("kind = ?")
            params.append(normalized_kind)

        lowered_query = normalized_query.lower()
        like_query = f"%{lowered_query}%"
        clauses.append(
            """
            (
                attachment_id = ?
                OR LOWER(attachment_id) = ?
                OR attachment_handle = ?
                OR LOWER(attachment_handle) = ?
                OR LOWER(origin_name) = ?
                OR LOWER(summary_title) = ?
                OR LOWER(origin_name) LIKE ?
                OR LOWER(summary_title) LIKE ?
                OR LOWER(short_hint) LIKE ?
                OR CAST(sequence_no AS TEXT) = ?
            )
            """
        )
        params.extend(
            [
                normalized_query,
                lowered_query,
                normalized_query,
                lowered_query,
                lowered_query,
                lowered_query,
                like_query,
                like_query,
                like_query,
                normalized_query,
            ]
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *,
                    CASE
                        WHEN attachment_id = ? OR LOWER(attachment_id) = ? THEN 0
                        WHEN attachment_handle = ? OR LOWER(attachment_handle) = ? THEN 0
                        WHEN CAST(sequence_no AS TEXT) = ? THEN 1
                        WHEN LOWER(summary_title) = ? OR LOWER(origin_name) = ? THEN 2
                        WHEN LOWER(summary_title) LIKE ? OR LOWER(origin_name) LIKE ? THEN 3
                        WHEN LOWER(short_hint) LIKE ? THEN 4
                        ELSE 5
                    END AS match_rank
                FROM attachment_inbox_items
                WHERE {" AND ".join(clauses)}
                ORDER BY
                    match_rank ASC,
                    updated_at DESC,
                    created_at DESC
                LIMIT ?
                """,
                tuple(
                    [
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        lowered_query,
                        like_query,
                        like_query,
                        like_query,
                    ]
                    + params
                    + [max(1, int(limit or 10))]
                ),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            match_rank = int(raw.pop("match_rank", 99) or 99)
            item = self._row_to_attachment_inbox_item(raw)
            item["_match_rank"] = match_rank
            output.append(item)
        return output

    def clear_attachment_inbox_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "current",
        kind: str | None = None,
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_target = str(target or "current").strip()
        effective_ts = int(timestamp or time.time())
        active_statuses = ["ready", "pending_observation", "failed"]
        if normalized_target.lower() in {"all", "全部", "*"}:
            targets = self.list_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=active_statuses,
                kind=kind,
                limit=200,
            )
        elif normalized_target.lower() in {"current", "latest", "最近", "当前"}:
            targets = self.list_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=active_statuses,
                kind=kind,
                limit=1,
            )
        else:
            found = self.find_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                query=normalized_target,
                kind=kind,
                statuses=active_statuses,
            )
            targets = [found] if found else []

        ids = [str(item.get("attachment_id") or "").strip() for item in targets if item]
        ids = [item_id for item_id in ids if item_id]
        if not ids:
            return []

        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE attachment_inbox_items
                SET status = 'cleared', updated_at = ?
                WHERE profile_user_id = ? AND session_id = ? AND attachment_id IN ({placeholders})
                """,
                (effective_ts, str(profile_user_id), str(session_id), *ids),
            )
        return [dict(item, status="cleared", updated_at=effective_ts) for item in targets if item]

    def sync_attachment_workspace_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_ts = int(timestamp or time.time())
        normalized_ids: list[str] = []
        for item_id in attachment_ids or []:
            normalized = str(item_id or "").strip()
            if normalized and normalized not in normalized_ids:
                normalized_ids.append(normalized)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE attachment_inbox_items
                SET focus_rank = 0, updated_at = ?
                WHERE profile_user_id = ? AND session_id = ?
                  AND status IN ('ready', 'pending_observation', 'failed')
                """,
                (effective_ts, str(profile_user_id), str(session_id)),
            )
            for rank, attachment_id in enumerate(normalized_ids, start=1):
                conn.execute(
                    """
                    UPDATE attachment_inbox_items
                    SET focus_rank = ?, last_used_at = ?, updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND attachment_id = ?
                      AND status IN ('ready', 'pending_observation', 'failed')
                    """,
                    (
                        rank,
                        effective_ts,
                        effective_ts,
                        str(profile_user_id),
                        str(session_id),
                        attachment_id,
                    ),
                )
            if not normalized_ids:
                return []
            placeholders = ", ".join("?" for _ in normalized_ids)
            rows = conn.execute(
                f"""
                SELECT * FROM attachment_inbox_items
                WHERE profile_user_id = ? AND session_id = ?
                  AND attachment_id IN ({placeholders})
                  AND status IN ('ready', 'pending_observation', 'failed')
                ORDER BY focus_rank ASC, updated_at DESC
                """,
                (str(profile_user_id), str(session_id), *normalized_ids),
            ).fetchall()
        return [self._row_to_attachment_inbox_item(dict(row)) for row in rows]

    def list_workspace_file_states(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        focused_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT profile_user_id, session_id, workspace_uri, focus_rank,
                   created_at, updated_at, last_used_at
            FROM workspace_file_states
            WHERE profile_user_id = ? AND session_id = ?
        """
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if focused_only:
            query += " AND focus_rank > 0"
        query += " ORDER BY CASE WHEN focus_rank > 0 THEN 0 ELSE 1 END, focus_rank ASC, updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_workspace_file_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        workspace_uris: list[str],
        action: str,
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"add", "set", "remove"}:
            raise ValueError(f"unsupported workspace focus action: {normalized_action}")

        normalized_uris: list[str] = []
        for workspace_uri in workspace_uris or []:
            normalized = str(workspace_uri or "").strip()
            if normalized and normalized not in normalized_uris:
                normalized_uris.append(normalized)

        effective_ts = int(timestamp or time.time())
        profile_id = str(profile_user_id)
        session = str(session_id)
        with self._workspace_file_write_lock:
            with self._connect() as conn:
                if normalized_action == "set":
                    conn.execute(
                        """
                        UPDATE workspace_file_states
                        SET focus_rank = 0, updated_at = ?
                        WHERE profile_user_id = ? AND session_id = ? AND focus_rank > 0
                        """,
                        (effective_ts, profile_id, session),
                    )

                if normalized_action in {"add", "set"}:
                    row = conn.execute(
                        """
                        SELECT COALESCE(MAX(focus_rank), 0) AS max_rank
                        FROM workspace_file_states
                        WHERE profile_user_id = ? AND session_id = ?
                        """,
                        (profile_id, session),
                    ).fetchone()
                    next_rank = int(row["max_rank"] or 0) + 1 if row else 1
                    for workspace_uri in normalized_uris:
                        existing = conn.execute(
                            """
                            SELECT focus_rank
                            FROM workspace_file_states
                            WHERE profile_user_id = ? AND session_id = ? AND workspace_uri = ?
                            """,
                            (profile_id, session, workspace_uri),
                        ).fetchone()
                        if normalized_action == "add" and existing and int(existing["focus_rank"] or 0) > 0:
                            conn.execute(
                                """
                                UPDATE workspace_file_states
                                SET last_used_at = ?, updated_at = ?
                                WHERE profile_user_id = ? AND session_id = ? AND workspace_uri = ?
                                """,
                                (effective_ts, effective_ts, profile_id, session, workspace_uri),
                            )
                            continue
                        conn.execute(
                            """
                            INSERT INTO workspace_file_states (
                                profile_user_id, session_id, workspace_uri, focus_rank,
                                created_at, updated_at, last_used_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(profile_user_id, session_id, workspace_uri)
                            DO UPDATE SET
                                focus_rank = excluded.focus_rank,
                                updated_at = excluded.updated_at,
                                last_used_at = excluded.last_used_at
                            """,
                            (
                                profile_id,
                                session,
                                workspace_uri,
                                next_rank,
                                effective_ts,
                                effective_ts,
                                effective_ts,
                            ),
                        )
                        next_rank += 1
                else:
                    for workspace_uri in normalized_uris:
                        conn.execute(
                            """
                            INSERT INTO workspace_file_states (
                                profile_user_id, session_id, workspace_uri, focus_rank,
                                created_at, updated_at, last_used_at
                            ) VALUES (?, ?, ?, 0, ?, ?, 0)
                            ON CONFLICT(profile_user_id, session_id, workspace_uri)
                            DO UPDATE SET focus_rank = 0, updated_at = excluded.updated_at
                            """,
                            (
                                profile_id,
                                session,
                                workspace_uri,
                                effective_ts,
                                effective_ts,
                            ),
                        )

        return self.list_workspace_file_states(
            profile_user_id=profile_id,
            session_id=session,
        )

    def add_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        output_title: str,
        output_format: str,
        storage_relpath: str,
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        source_ids: list[str] | tuple[str, ...] | set[str] | None = None,
        content_card: dict[str, Any] | None = None,
        summary: str = "",
        created_by_tool: str = "",
        version_of_generated_id: str = "",
        version_no: int = 1,
        status: str = "ready",
        delivery_status: str = "pending",
        timestamp: int | None = None,
        generated_id: str = "",
        generated_handle: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_id = str(generated_id or "").strip() or f"generated::{uuid.uuid4()}"
        normalized_format = self._normalize_generated_format(output_format or file_ext)
        normalized_ext = str(file_ext or "").strip().lower().lstrip(".")
        if not normalized_ext:
            normalized_ext = normalized_format
        payload = {
            "generated_id": normalized_id,
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "generated_handle": str(generated_handle or "").strip(),
            "sequence_no": 0,
            "status": self._normalize_generated_status(status),
            "output_title": str(output_title or "").strip(),
            "output_format": normalized_format,
            "mime_type": str(mime_type or "").strip(),
            "file_ext": normalized_ext,
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
            "source_ids_json": json.dumps(self._normalize_string_list(source_ids), ensure_ascii=False),
            "content_card_json": json.dumps(content_card if isinstance(content_card, dict) else {}, ensure_ascii=False),
            "summary": str(summary or "").strip(),
            "created_by_tool": str(created_by_tool or "").strip(),
            "version_of_generated_id": str(version_of_generated_id or "").strip(),
            "version_no": max(1, int(version_no or 1)),
            "delivery_status": str(delivery_status or "pending").strip().lower()[:32] or "pending",
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_used_at": 0,
        }
        with self._connect() as conn:
            if not payload["generated_handle"]:
                row = conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) AS max_seq
                    FROM generated_files
                    WHERE profile_user_id = ? AND session_id = ?
                    """,
                    (payload["profile_user_id"], payload["session_id"]),
                ).fetchone()
                sequence_no = int(row["max_seq"] or 0) + 1 if row else 1
                payload["sequence_no"] = sequence_no
                payload["generated_handle"] = f"gen_{sequence_no:03d}"
            else:
                payload["sequence_no"] = self._sequence_no_from_generated_handle(payload["generated_handle"])
            conn.execute(
                """
                INSERT INTO generated_files (
                    generated_id, profile_user_id, session_id, generated_handle, sequence_no,
                    status, output_title, output_format, mime_type, file_ext, file_size,
                    storage_relpath, source_ids_json, content_card_json, summary,
                    created_by_tool, version_of_generated_id, version_no, delivery_status,
                    created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["generated_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["generated_handle"],
                    payload["sequence_no"],
                    payload["status"],
                    payload["output_title"],
                    payload["output_format"],
                    payload["mime_type"],
                    payload["file_ext"],
                    payload["file_size"],
                    payload["storage_relpath"],
                    payload["source_ids_json"],
                    payload["content_card_json"],
                    payload["summary"],
                    payload["created_by_tool"],
                    payload["version_of_generated_id"],
                    payload["version_no"],
                    payload["delivery_status"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["last_used_at"],
                ),
            )
        return self._row_to_generated_file(payload)

    def update_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
        status: str | None = None,
        delivery_status: str | None = None,
        summary: str | None = None,
        content_card: dict[str, Any] | None = None,
        storage_relpath: str | None = None,
        file_size: int | None = None,
        last_used_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(generated_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_generated_status(status))
        if delivery_status is not None:
            fields.append("delivery_status = ?")
            params.append(str(delivery_status or "").strip().lower()[:32] or "pending")
        if summary is not None:
            fields.append("summary = ?")
            params.append(str(summary or "").strip())
        if content_card is not None:
            fields.append("content_card_json = ?")
            params.append(json.dumps(content_card if isinstance(content_card, dict) else {}, ensure_ascii=False))
        if storage_relpath is not None:
            fields.append("storage_relpath = ?")
            params.append(str(storage_relpath or "").strip())
        if file_size is not None:
            fields.append("file_size = ?")
            params.append(max(0, int(file_size or 0)))
        if last_used_at is not None:
            fields.append("last_used_at = ?")
            params.append(max(0, int(last_used_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE generated_files
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_generated_file(dict(updated)) if updated else None

    def get_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
    ) -> dict[str, Any] | None:
        normalized_id = str(generated_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM generated_files
                WHERE profile_user_id = ? AND session_id = ? AND generated_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_generated_file(dict(row)) if row else None

    def list_generated_files(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self._normalize_generated_status_list(statuses)
        query = [
            """
            SELECT * FROM generated_files
            WHERE profile_user_id = ? AND session_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append(
            """
            ORDER BY
                CASE WHEN last_used_at > 0 THEN last_used_at ELSE updated_at END DESC,
                updated_at DESC,
                created_at DESC
            LIMIT ?
            """
        )
        params.append(max(1, int(limit or 10)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_generated_file(dict(row)) for row in rows]

    def find_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any] | None:
        matches = self.find_generated_file_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=query,
            statuses=statuses,
            limit=12,
        )
        if not matches:
            return None
        best_rank = int(matches[0].get("_match_rank") or 99)
        if best_rank <= 2:
            return {key: value for key, value in matches[0].items() if key != "_match_rank"}
        top_matches = [
            item
            for item in matches
            if int(item.get("_match_rank") or 99) == best_rank
        ]
        if len(top_matches) > 1:
            return None
        return {key: value for key, value in matches[0].items() if key != "_match_rank"}

    def find_generated_file_matches(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_statuses = self._normalize_generated_status_list(statuses)
        clauses = ["profile_user_id = ?", "session_id = ?"]
        params: list[Any] = [str(profile_user_id), str(session_id)]
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        lowered_query = normalized_query.lower()
        like_query = f"%{lowered_query}%"
        clauses.append(
            """
            (
                generated_id = ?
                OR LOWER(generated_id) = ?
                OR generated_handle = ?
                OR LOWER(generated_handle) = ?
                OR LOWER(output_title) = ?
                OR LOWER(output_title) LIKE ?
                OR LOWER(summary) LIKE ?
                OR CAST(sequence_no AS TEXT) = ?
            )
            """
        )
        params.extend([
            normalized_query,
            lowered_query,
            normalized_query,
            lowered_query,
            lowered_query,
            like_query,
            like_query,
            normalized_query,
        ])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *,
                    CASE
                        WHEN generated_id = ? OR LOWER(generated_id) = ? THEN 0
                        WHEN generated_handle = ? OR LOWER(generated_handle) = ? THEN 0
                        WHEN CAST(sequence_no AS TEXT) = ? THEN 1
                        WHEN LOWER(output_title) = ? THEN 2
                        WHEN LOWER(output_title) LIKE ? THEN 3
                        WHEN LOWER(summary) LIKE ? THEN 4
                        ELSE 5
                    END AS match_rank
                FROM generated_files
                WHERE {" AND ".join(clauses)}
                ORDER BY
                    match_rank ASC,
                    updated_at DESC,
                    created_at DESC
                LIMIT ?
                """,
                tuple(
                    [
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        normalized_query,
                        lowered_query,
                        like_query,
                        like_query,
                    ]
                    + params
                    + [max(1, int(limit or 10))]
                ),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            match_rank = int(raw.pop("match_rank", 99) or 99)
            item = self._row_to_generated_file(raw)
            item["_match_rank"] = match_rank
            output.append(item)
        return output

    def upsert_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_id: str,
        source_kind: str = "",
        source_handle: str = "",
        title: str = "",
        status: str = "pending",
        segments: list[dict[str, Any]] | None = None,
        rolling_summary: str = "",
        ready_until_seconds: float = 0,
        transcript_generated_id: str = "",
        transcript_generated_handle: str = "",
        error_message: str = "",
        source_size: int = 0,
        source_mtime_ns: int = 0,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            raise ValueError("source_id is required for desktop music timeline")
        effective_ts = int(timestamp or time.time())
        existing = self.get_desktop_music_timeline_by_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_id=normalized_source_id,
        )
        if existing is None:
            timeline_id = f"music_timeline::{uuid.uuid4()}"
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO desktop_music_timelines (
                        timeline_id, profile_user_id, session_id, source_id,
                        source_kind, source_handle, title, status, segments_json,
                        rolling_summary, ready_until_seconds, transcript_generated_id,
                        transcript_generated_handle, error_message, source_size,
                        source_mtime_ns, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timeline_id,
                        str(profile_user_id),
                        str(session_id),
                        normalized_source_id,
                        str(source_kind or "").strip(),
                        str(source_handle or "").strip(),
                        str(title or "").strip(),
                        self._normalize_desktop_music_timeline_status(status),
                        json.dumps(segments if isinstance(segments, list) else [], ensure_ascii=False),
                        str(rolling_summary or "").strip(),
                        max(0.0, float(ready_until_seconds or 0)),
                        str(transcript_generated_id or "").strip(),
                        str(transcript_generated_handle or "").strip(),
                        str(error_message or "").strip(),
                        max(0, int(source_size or 0)),
                        max(0, int(source_mtime_ns or 0)),
                        effective_ts,
                        effective_ts,
                    ),
                )
            return self.get_desktop_music_timeline(timeline_id=timeline_id) or {}

        updated = self.update_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=str(existing.get("timeline_id") or ""),
            source_kind=source_kind,
            source_handle=source_handle,
            title=title,
            status=status,
            segments=segments,
            rolling_summary=rolling_summary,
            ready_until_seconds=ready_until_seconds,
            transcript_generated_id=transcript_generated_id,
            transcript_generated_handle=transcript_generated_handle,
            error_message=error_message,
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            updated_at=effective_ts,
        )
        return updated or existing

    def update_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timeline_id: str,
        source_kind: str | None = None,
        source_handle: str | None = None,
        title: str | None = None,
        status: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        rolling_summary: str | None = None,
        ready_until_seconds: float | None = None,
        transcript_generated_id: str | None = None,
        transcript_generated_handle: str | None = None,
        error_message: str | None = None,
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(timeline_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if source_kind is not None:
            fields.append("source_kind = ?")
            params.append(str(source_kind or "").strip())
        if source_handle is not None:
            fields.append("source_handle = ?")
            params.append(str(source_handle or "").strip())
        if title is not None:
            fields.append("title = ?")
            params.append(str(title or "").strip())
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_desktop_music_timeline_status(status))
        if segments is not None:
            fields.append("segments_json = ?")
            params.append(json.dumps(segments if isinstance(segments, list) else [], ensure_ascii=False))
        if rolling_summary is not None:
            fields.append("rolling_summary = ?")
            params.append(str(rolling_summary or "").strip())
        if ready_until_seconds is not None:
            fields.append("ready_until_seconds = ?")
            params.append(max(0.0, float(ready_until_seconds or 0)))
        if transcript_generated_id is not None:
            fields.append("transcript_generated_id = ?")
            params.append(str(transcript_generated_id or "").strip())
        if transcript_generated_handle is not None:
            fields.append("transcript_generated_handle = ?")
            params.append(str(transcript_generated_handle or "").strip())
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(str(error_message or "").strip())
        if source_size is not None:
            fields.append("source_size = ?")
            params.append(max(0, int(source_size or 0)))
        if source_mtime_ns is not None:
            fields.append("source_mtime_ns = ?")
            params.append(max(0, int(source_mtime_ns or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.extend([str(profile_user_id), str(session_id), normalized_id])

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                f"""
                UPDATE desktop_music_timelines
                SET {", ".join(fields)}
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND timeline_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_id),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(updated)) if updated else None

    def get_desktop_music_timeline(self, *, timeline_id: str) -> dict[str, Any] | None:
        normalized_id = str(timeline_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM desktop_music_timelines WHERE timeline_id = ? LIMIT 1",
                (normalized_id,),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(row)) if row else None

    def get_desktop_music_timeline_by_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM desktop_music_timelines
                WHERE profile_user_id = ? AND session_id = ? AND source_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_source_id),
            ).fetchone()
        return self._row_to_desktop_music_timeline(dict(row)) if row else None

    def add_task_workspace(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        owner: str = "Akane",
        status: str = "queued",
        raw_request: dict[str, Any] | None = None,
        normalized_goal: str = "",
        success_criteria: list[Any] | tuple[Any, ...] | None = None,
        constraints: list[Any] | tuple[Any, ...] | None = None,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        pending_question: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: int | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        payload = {
            "task_id": str(task_id or "").strip() or f"task::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "owner": str(owner or "Akane").strip()[:64] or "Akane",
            "status": self._normalize_task_workspace_status(status),
            "raw_request_json": json.dumps(raw_request if isinstance(raw_request, dict) else {}, ensure_ascii=False),
            "normalized_goal": str(normalized_goal or "").strip(),
            "success_criteria_json": json.dumps(list(success_criteria or []), ensure_ascii=False),
            "constraints_json": json.dumps(list(constraints or []), ensure_ascii=False),
            "steps_json": json.dumps(list(steps or []), ensure_ascii=False),
            "artifacts_json": json.dumps(list(artifacts or []), ensure_ascii=False),
            "pending_question_json": json.dumps(
                pending_question if isinstance(pending_question, dict) else {},
                ensure_ascii=False,
            ),
            "metadata_json": json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "completed_at": 0,
            "cleaned_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_workspaces (
                    task_id, profile_user_id, session_id, owner, status,
                    raw_request_json, normalized_goal, success_criteria_json, constraints_json,
                    steps_json, artifacts_json, pending_question_json, metadata_json,
                    created_at, updated_at, completed_at, cleaned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["task_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["owner"],
                    payload["status"],
                    payload["raw_request_json"],
                    payload["normalized_goal"],
                    payload["success_criteria_json"],
                    payload["constraints_json"],
                    payload["steps_json"],
                    payload["artifacts_json"],
                    payload["pending_question_json"],
                    payload["metadata_json"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["completed_at"],
                    payload["cleaned_at"],
                ),
            )
        return self._row_to_task_workspace(payload)

    def get_task_workspace(self, task_id: str) -> dict[str, Any] | None:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace(dict(row)) if row else None

    def list_task_workspaces(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = [
            """
            SELECT * FROM task_workspaces
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_statuses = self._normalize_task_workspace_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        query.append(
            """
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """
        )
        params.append(max(1, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_task_workspace(dict(row)) for row in rows]

    def update_task_workspace(
        self,
        *,
        task_id: str,
        status: str | None = None,
        owner: str | None = None,
        raw_request: dict[str, Any] | None = None,
        normalized_goal: str | None = None,
        success_criteria: list[Any] | tuple[Any, ...] | None = None,
        constraints: list[Any] | tuple[Any, ...] | None = None,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        pending_question: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        completed_at: int | None = None,
        cleaned_at: int | None = None,
        updated_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return None
        fields: list[str] = []
        params: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(self._normalize_task_workspace_status(status))
        if owner is not None:
            fields.append("owner = ?")
            params.append(str(owner or "Akane").strip()[:64] or "Akane")
        if raw_request is not None:
            fields.append("raw_request_json = ?")
            params.append(json.dumps(raw_request if isinstance(raw_request, dict) else {}, ensure_ascii=False))
        if normalized_goal is not None:
            fields.append("normalized_goal = ?")
            params.append(str(normalized_goal or "").strip())
        if success_criteria is not None:
            fields.append("success_criteria_json = ?")
            params.append(json.dumps(list(success_criteria or []), ensure_ascii=False))
        if constraints is not None:
            fields.append("constraints_json = ?")
            params.append(json.dumps(list(constraints or []), ensure_ascii=False))
        if steps is not None:
            fields.append("steps_json = ?")
            params.append(json.dumps(list(steps or []), ensure_ascii=False))
        if artifacts is not None:
            fields.append("artifacts_json = ?")
            params.append(json.dumps(list(artifacts or []), ensure_ascii=False))
        if pending_question is not None:
            fields.append("pending_question_json = ?")
            params.append(json.dumps(pending_question if isinstance(pending_question, dict) else {}, ensure_ascii=False))
        if metadata is not None:
            fields.append("metadata_json = ?")
            params.append(json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False))
        if completed_at is not None:
            fields.append("completed_at = ?")
            params.append(max(0, int(completed_at or 0)))
        if cleaned_at is not None:
            fields.append("cleaned_at = ?")
            params.append(max(0, int(cleaned_at or 0)))
        fields.append("updated_at = ?")
        params.append(int(updated_at or time.time()))
        params.append(normalized_id)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                f"""
                UPDATE task_workspaces
                SET {", ".join(fields)}
                WHERE task_id = ?
                """,
                tuple(params),
            )
            updated = conn.execute(
                """
                SELECT * FROM task_workspaces
                WHERE task_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace(dict(updated)) if updated else None

    def append_task_workspace_event(
        self,
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        event_type: str,
        from_actor: str = "",
        priority: str = "normal",
        requires_user: bool = False,
        message: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "pending",
        timestamp: int | None = None,
        event_id: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        event = {
            "event_id": str(event_id or "").strip() or f"task_event::{uuid.uuid4()}",
            "task_id": str(task_id or "").strip(),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "event_type": str(event_type or "").strip()[:64] or "note",
            "from_actor": str(from_actor or "").strip()[:64],
            "priority": self._normalize_task_event_priority(priority),
            "requires_user": 1 if requires_user else 0,
            "message": str(message or "").strip(),
            "payload_json": json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
            "status": self._normalize_task_event_status(status),
            "created_at": effective_ts,
            "handled_at": 0,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_workspace_events (
                    event_id, task_id, profile_user_id, session_id, event_type,
                    from_actor, priority, requires_user, message, payload_json,
                    status, created_at, handled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["task_id"],
                    event["profile_user_id"],
                    event["session_id"],
                    event["event_type"],
                    event["from_actor"],
                    event["priority"],
                    event["requires_user"],
                    event["message"],
                    event["payload_json"],
                    event["status"],
                    event["created_at"],
                    event["handled_at"],
                ),
            )
        return self._row_to_task_workspace_event(event)

    def list_task_workspace_events(
        self,
        *,
        task_id: str | None = None,
        profile_user_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id:
            clauses.append("task_id = ?")
            params.append(normalized_task_id)
        normalized_profile = str(profile_user_id or "").strip()
        if normalized_profile:
            clauses.append("profile_user_id = ?")
            params.append(normalized_profile)
        normalized_session = str(session_id or "").strip()
        if normalized_session:
            clauses.append("session_id = ?")
            params.append(normalized_session)
        normalized_status = str(status or "").strip()
        if normalized_status:
            clauses.append("status = ?")
            params.append(self._normalize_task_event_status(normalized_status))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM task_workspace_events
                {where_sql}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_task_workspace_event(dict(row)) for row in rows]

    def mark_task_workspace_event_handled(
        self,
        *,
        event_id: str,
        status: str = "handled",
        handled_at: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = str(event_id or "").strip()
        if not normalized_id:
            return None
        effective_ts = int(handled_at or time.time())
        normalized_status = self._normalize_task_event_status(status)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_workspace_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                """
                UPDATE task_workspace_events
                SET status = ?, handled_at = ?
                WHERE event_id = ?
                """,
                (normalized_status, effective_ts, normalized_id),
            )
            updated = conn.execute(
                """
                SELECT * FROM task_workspace_events
                WHERE event_id = ?
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
        return self._row_to_task_workspace_event(dict(updated)) if updated else None

    def add_persona_event(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str = "",
        event_type: str,
        reason: str = "",
        source_id: str = "",
        payload: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        event = {
            "event_id": f"persona_event::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "card_id": str(card_id or "").strip(),
            "event_type": str(event_type or "").strip(),
            "reason": str(reason or "").strip(),
            "source_id": str(source_id or "").strip(),
            "created_at": effective_ts,
            "payload_json": json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO persona_events (
                    event_id, profile_user_id, session_id, card_id, event_type,
                    reason, source_id, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["profile_user_id"],
                    event["session_id"],
                    event["card_id"],
                    event["event_type"],
                    event["reason"],
                    event["source_id"],
                    event["created_at"],
                    event["payload_json"],
                ),
            )
        return self._row_to_persona_event(event)

    def create_persona_card(
        self,
        *,
        card_id: str,
        profile_user_id: str,
        session_id: str,
        name: str,
        status: str = "inactive",
        summary: str = "",
        speech_style: str = "",
        interaction_bias: str = "",
        resource_preference: str = "",
        switch_hint: str = "",
        unsuitable_contexts: str = "",
        created_reason: str = "",
        updated_reason: str = "",
        source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_status = self._normalize_persona_status(status)
        normalized_sources = self._normalize_string_list(source_ids)
        with self._connect() as conn:
            if normalized_status == "active":
                conn.execute(
                    """
                    UPDATE persona_cards
                    SET status = 'inactive', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND status = 'active'
                    """,
                    (effective_ts, str(profile_user_id), str(session_id)),
                )
            conn.execute(
                """
                INSERT INTO persona_cards (
                    card_id, profile_user_id, session_id, name, status,
                    summary, speech_style, interaction_bias, resource_preference,
                    switch_hint, unsuitable_contexts, created_reason, updated_reason,
                    source_ids_json, created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card_id),
                    str(profile_user_id),
                    str(session_id),
                    str(name),
                    normalized_status,
                    str(summary or ""),
                    str(speech_style or ""),
                    str(interaction_bias or ""),
                    str(resource_preference or ""),
                    str(switch_hint or ""),
                    str(unsuitable_contexts or ""),
                    str(created_reason or ""),
                    str(updated_reason or ""),
                    json.dumps(normalized_sources, ensure_ascii=False),
                    effective_ts,
                    effective_ts,
                    0,
                ),
            )
            if normalized_status == "active":
                conn.execute(
                    """
                    INSERT INTO persona_session_states (
                        profile_user_id, session_id, active_card_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_user_id, session_id)
                    DO UPDATE SET active_card_id = excluded.active_card_id,
                                  updated_at = excluded.updated_at
                    """,
                    (str(profile_user_id), str(session_id), str(card_id), effective_ts),
                )
        created = (
            self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
            if normalized_status == "active"
            else self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        )
        if created is not None:
            return created
        return {
            "card_id": str(card_id),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "name": str(name),
            "status": normalized_status,
            "summary": str(summary or ""),
            "speech_style": str(speech_style or ""),
            "interaction_bias": str(interaction_bias or ""),
            "resource_preference": str(resource_preference or ""),
            "switch_hint": str(switch_hint or ""),
            "unsuitable_contexts": str(unsuitable_contexts or ""),
            "created_reason": str(created_reason or ""),
            "updated_reason": str(updated_reason or ""),
            "source_ids": normalized_sources,
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "archived_at": 0,
        }

    def get_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
    ) -> dict[str, Any] | None:
        normalized_card_id = str(card_id or "").strip()
        if not normalized_card_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND card_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), normalized_card_id),
            ).fetchone()
        return self._row_to_persona_card(dict(row)) if row else None

    def find_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
    ) -> dict[str, Any] | None:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ?
                  AND (card_id = ? OR name = ?)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (str(profile_user_id), normalized_query, normalized_query),
            ).fetchone()
        return self._row_to_persona_card(dict(row)) if row else None

    def get_active_persona_card(self, *, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        normalized_profile_user_id = str(profile_user_id)
        normalized_session_id = str(session_id)
        with self._connect() as conn:
            state = conn.execute(
                """
                SELECT active_card_id FROM persona_session_states
                WHERE profile_user_id = ? AND session_id = ?
                LIMIT 1
                """,
                (normalized_profile_user_id, normalized_session_id),
            ).fetchone()
            active_card_id = str(state["active_card_id"] or "").strip() if state else ""
            if active_card_id:
                row = conn.execute(
                    """
                    SELECT * FROM persona_cards
                    WHERE profile_user_id = ? AND card_id = ?
                      AND status NOT IN ('archived', 'deleted')
                    LIMIT 1
                    """,
                    (normalized_profile_user_id, active_card_id),
                ).fetchone()
                if row is not None:
                    payload = self._row_to_persona_card(dict(row))
                    payload["status"] = "active"
                    return payload

                conn.execute(
                    """
                    UPDATE persona_session_states
                    SET active_card_id = '', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ?
                    """,
                    (int(time.time()), normalized_profile_user_id, normalized_session_id),
                )

            # Legacy fallback: older builds stored "active" directly on the
            # card row for a single session. Promote that into session state.
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND session_id = ? AND status = 'active'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (normalized_profile_user_id, normalized_session_id),
            ).fetchone()
            if row is None:
                return None
            card_id = str(row["card_id"] or "")
            conn.execute(
                """
                INSERT INTO persona_session_states (
                    profile_user_id, session_id, active_card_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_user_id, session_id)
                DO UPDATE SET active_card_id = excluded.active_card_id,
                              updated_at = excluded.updated_at
                """,
                (normalized_profile_user_id, normalized_session_id, card_id, int(time.time())),
            )
            payload = self._row_to_persona_card(dict(row))
            payload["status"] = "active"
            return payload

    def list_persona_cards(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_statuses: list[str] = []
        for item in statuses or ["active", "inactive"]:
            normalized = self._normalize_persona_status(item)
            if normalized and normalized not in normalized_statuses:
                normalized_statuses.append(normalized)
        if not normalized_statuses:
            normalized_statuses = ["active", "inactive"]
        placeholders = ",".join("?" for _ in normalized_statuses)
        normalized_limit = max(1, min(100, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND status IN ({placeholders})
                ORDER BY
                    CASE status WHEN 'active' THEN 0 WHEN 'inactive' THEN 1 WHEN 'archived' THEN 2 ELSE 3 END,
                    updated_at DESC,
                    created_at DESC
                LIMIT ?
                """,
                [str(profile_user_id), *normalized_statuses, normalized_limit],
            ).fetchall()
        return [self._row_to_persona_card(dict(row)) for row in rows]

    def update_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        fields: dict[str, Any],
        source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        if current is None:
            return None
        effective_ts = int(timestamp or time.time())
        allowed_fields = {
            "name",
            "status",
            "summary",
            "speech_style",
            "interaction_bias",
            "resource_preference",
            "switch_hint",
            "unsuitable_contexts",
            "created_reason",
            "updated_reason",
            "archived_at",
        }
        next_payload = {key: current.get(key) for key in allowed_fields}
        for key, value in (fields or {}).items():
            if key not in allowed_fields:
                continue
            if key == "status":
                next_payload[key] = self._normalize_persona_status(value)
            elif key == "archived_at":
                next_payload[key] = max(0, int(value or 0))
            else:
                next_payload[key] = str(value or "")

        next_sources = self._normalize_string_list(
            [*list(current.get("source_ids") or []), *self._normalize_string_list(source_ids)]
        )
        with self._connect() as conn:
            if str(next_payload.get("status") or "") == "active":
                conn.execute(
                    """
                    UPDATE persona_cards
                    SET status = 'inactive', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND status = 'active' AND card_id != ?
                    """,
                    (effective_ts, str(profile_user_id), str(session_id), str(card_id)),
                )
            conn.execute(
                """
                UPDATE persona_cards
                SET name = ?, status = ?, summary = ?, speech_style = ?, interaction_bias = ?,
                    resource_preference = ?, switch_hint = ?, unsuitable_contexts = ?,
                    created_reason = ?, updated_reason = ?, source_ids_json = ?,
                    updated_at = ?, archived_at = ?
                WHERE profile_user_id = ? AND card_id = ?
                """,
                (
                    str(next_payload.get("name") or ""),
                    self._normalize_persona_status(next_payload.get("status")),
                    str(next_payload.get("summary") or ""),
                    str(next_payload.get("speech_style") or ""),
                    str(next_payload.get("interaction_bias") or ""),
                    str(next_payload.get("resource_preference") or ""),
                    str(next_payload.get("switch_hint") or ""),
                    str(next_payload.get("unsuitable_contexts") or ""),
                    str(next_payload.get("created_reason") or ""),
                    str(next_payload.get("updated_reason") or ""),
                    json.dumps(next_sources, ensure_ascii=False),
                    effective_ts,
                    int(next_payload.get("archived_at") or 0),
                    str(profile_user_id),
                    str(card_id),
                ),
            )
            if str(next_payload.get("status") or "") == "active":
                conn.execute(
                    """
                    INSERT INTO persona_session_states (
                        profile_user_id, session_id, active_card_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_user_id, session_id)
                    DO UPDATE SET active_card_id = excluded.active_card_id,
                                  updated_at = excluded.updated_at
                    """,
                    (str(profile_user_id), str(session_id), str(card_id), effective_ts),
                )
            if str(next_payload.get("status") or "") in {"archived", "deleted"}:
                conn.execute(
                    """
                    UPDATE persona_session_states
                    SET active_card_id = '', updated_at = ?
                    WHERE profile_user_id = ? AND active_card_id = ?
                    """,
                    (effective_ts, str(profile_user_id), str(card_id)),
                )
        if str(next_payload.get("status") or "") == "active":
            return self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        return self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)

    def activate_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        if current is None or str(current.get("status") or "") in {"archived", "deleted"}:
            return None
        return self.update_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            fields={"status": "active", "archived_at": 0},
            timestamp=timestamp,
        )

    def deactivate_active_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        active = self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        if active is None:
            return None
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO persona_session_states (
                    profile_user_id, session_id, active_card_id, updated_at
                ) VALUES (?, ?, '', ?)
                ON CONFLICT(profile_user_id, session_id)
                DO UPDATE SET active_card_id = '',
                              updated_at = excluded.updated_at
                """,
                (str(profile_user_id), str(session_id), effective_ts),
            )
        return {**active, "status": "inactive"}

    def add_gift_asset(
        self,
        *,
        asset_id: str,
        resource_id: str,
        profile_user_id: str,
        session_id: str,
        display_name: str,
        asset_type: str,
        origin_event_type: str,
        origin_source_id: str = "",
        source_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        media_kind: str = "",
        origin_name: str = "",
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        storage_relpath: str = "",
        status: str = "pending",
        timestamp: int | None = None,
        last_decision_at: int | None = None,
        last_touched_at: int | None = None,
        container_type: str = "",
        container_key: str = "",
        container_name: str = "",
        artifact_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_status = self._normalize_gift_status(status)
        normalized_source_ids = self._normalize_string_list(source_ids)
        normalized_origin_source_id = str(origin_source_id or "").strip()
        if normalized_origin_source_id and normalized_origin_source_id not in normalized_source_ids:
            normalized_source_ids.insert(0, normalized_origin_source_id)
        normalized_payload = payload if isinstance(payload, dict) else {}
        normalized_container = self._derive_container_fields(
            asset_type=asset_type,
            status=normalized_status,
            payload=normalized_payload,
            container_type=container_type,
            container_key=container_key,
            container_name=container_name,
            artifact_flags=artifact_flags,
        )
        payload = {
            "asset_id": str(asset_id),
            "resource_id": str(resource_id),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "display_name": str(display_name),
            "asset_type": str(asset_type or "").strip(),
            "origin_event_type": str(origin_event_type or "").strip(),
            "origin_source_id": normalized_origin_source_id,
            "source_ids_json": json.dumps(normalized_source_ids, ensure_ascii=False),
            "payload_json": json.dumps(normalized_payload, ensure_ascii=False),
            "media_kind": str(media_kind or "").strip(),
            "origin_name": str(origin_name or "").strip(),
            "display_name": str(display_name),
            "mime_type": str(mime_type or ""),
            "file_ext": str(file_ext or ""),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath),
            "status": normalized_status,
            "container_type": normalized_container["container_type"],
            "container_key": normalized_container["container_key"],
            "container_name": normalized_container["container_name"],
            "artifact_flags_json": json.dumps(normalized_container["artifact_flags"], ensure_ascii=False),
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "last_decision_at": int(last_decision_at or 0),
            "last_touched_at": int(last_touched_at or effective_ts),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_media_assets (
                    asset_id, resource_id, profile_user_id, session_id, media_kind,
                    origin_name, display_name, mime_type, file_ext, file_size,
                    storage_relpath, status, container_type, container_key, container_name, artifact_flags_json, created_at, updated_at,
                    asset_type, origin_event_type, origin_source_id, source_ids_json,
                    payload_json, last_decision_at, last_touched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["asset_id"],
                    payload["resource_id"],
                    payload["profile_user_id"],
                    payload["session_id"],
                    payload["media_kind"],
                    payload["origin_name"],
                    payload["display_name"],
                    payload["mime_type"],
                    payload["file_ext"],
                    payload["file_size"],
                    payload["storage_relpath"],
                    payload["status"],
                    payload["container_type"],
                    payload["container_key"],
                    payload["container_name"],
                    payload["artifact_flags_json"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["asset_type"],
                    payload["origin_event_type"],
                    payload["origin_source_id"],
                    payload["source_ids_json"],
                    payload["payload_json"],
                    payload["last_decision_at"],
                    payload["last_touched_at"],
                ),
            )
        return self.get_gift_asset(profile_user_id=payload["profile_user_id"], asset_id=payload["asset_id"]) or payload

    def add_user_media_asset(
        self,
        *,
        asset_id: str,
        resource_id: str,
        profile_user_id: str,
        session_id: str,
        media_kind: str,
        origin_name: str,
        display_name: str,
        mime_type: str,
        file_ext: str,
        file_size: int,
        storage_relpath: str,
        status: str = "pending",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "filename": str(origin_name or "").strip(),
            "mime_type": str(mime_type or "").strip(),
            "file_ext": str(file_ext or "").strip(),
            "file_size": max(0, int(file_size or 0)),
            "storage_relpath": str(storage_relpath or "").strip(),
        }
        return self.add_gift_asset(
            asset_id=asset_id,
            resource_id=resource_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            display_name=display_name,
            asset_type=self._infer_asset_type_from_media_kind(media_kind),
            origin_event_type="upload",
            payload=payload,
            media_kind=media_kind,
            origin_name=origin_name,
            mime_type=mime_type,
            file_ext=file_ext,
            file_size=file_size,
            storage_relpath=storage_relpath,
            status=status,
            timestamp=timestamp,
        )

    def get_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
        return self._row_to_user_media_asset(dict(row)) if row else None

    def get_user_media_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        return self.get_gift_asset(profile_user_id=profile_user_id, asset_id=asset_id)

    def delete_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                DELETE FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                """,
                (str(profile_user_id), str(asset_id)),
            )
        return self._row_to_user_media_asset(dict(row))

    def list_gift_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = [
            """
            SELECT * FROM user_media_assets
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_asset_type = str(asset_type or "").strip()
        if normalized_asset_type:
            query.append("AND asset_type = ?")
            params.append(normalized_asset_type)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        normalized_status = self._normalize_gift_status(status) if status else ""
        if normalized_status and normalized_status not in normalized_statuses:
            normalized_statuses.append(normalized_status)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.append("ORDER BY updated_at DESC, created_at DESC")
        query.append("LIMIT ?")
        params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_user_media_asset(dict(row)) for row in rows]

    def list_user_media_assets(
        self,
        *,
        profile_user_id: str,
        media_kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.list_gift_assets(
            profile_user_id=profile_user_id,
            asset_type=self._infer_asset_type_from_media_kind(media_kind) if media_kind else None,
            status=status,
            limit=limit,
        )

    def count_gift_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        statuses: list[str] | None = None,
    ) -> int:
        query = [
            """
            SELECT COUNT(*) AS cnt FROM user_media_assets
            WHERE profile_user_id = ?
            """
        ]
        params: list[Any] = [str(profile_user_id)]
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            query.append("AND session_id = ?")
            params.append(normalized_session_id)
        normalized_asset_type = str(asset_type or "").strip()
        if normalized_asset_type:
            query.append("AND asset_type = ?")
            params.append(normalized_asset_type)
        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)
        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return int(row["cnt"] or 0) if row else 0

    def list_artifacts_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return []

        query = [
            """
            SELECT * FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_container_key = str(container_key or "").strip()
        if normalized_container_key:
            query.append("AND container_key = ?")
            params.append(normalized_container_key)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.append("ORDER BY updated_at DESC, created_at DESC")
        query.append("LIMIT ?")
        params.append(max(1, int(limit or 50)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [self._row_to_user_media_asset(dict(row)) for row in rows]

    def count_artifacts_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str | None = None,
        statuses: list[str] | None = None,
    ) -> int:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return 0

        query = [
            """
            SELECT COUNT(*) AS cnt FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_container_key = str(container_key or "").strip()
        if normalized_container_key:
            query.append("AND container_key = ?")
            params.append(normalized_container_key)

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        with self._connect() as conn:
            row = conn.execute("\n".join(query), tuple(params)).fetchone()
        return int(row["cnt"] or 0) if row else 0

    def list_artifact_groups_by_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_container_type = self._normalize_container_type(container_type)
        if not normalized_container_type:
            return []

        query = [
            """
            SELECT
                container_key,
                container_name,
                COUNT(*) AS total_count,
                MAX(updated_at) AS latest_updated_at,
                MAX(created_at) AS latest_created_at
            FROM user_media_assets
            WHERE profile_user_id = ? AND container_type = ?
            """
        ]
        params: list[Any] = [str(profile_user_id), normalized_container_type]

        normalized_statuses = self._normalize_gift_status_list(statuses)
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            query.append(f"AND status IN ({placeholders})")
            params.extend(normalized_statuses)

        query.extend(
            [
                "GROUP BY container_key, container_name",
                "ORDER BY latest_updated_at DESC, latest_created_at DESC, container_name ASC",
                "LIMIT ?",
            ]
        )
        params.append(max(1, int(limit or 100)))
        with self._connect() as conn:
            rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [
            {
                "container_key": str(row["container_key"] or "").strip(),
                "container_name": str(row["container_name"] or "").strip(),
                "total_count": int(row["total_count"] or 0),
                "latest_updated_at": int(row["latest_updated_at"] or 0),
                "latest_created_at": int(row["latest_created_at"] or 0),
            }
            for row in rows
        ]

    def update_gift_asset(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
        display_name: str | None = None,
        status: str | None = None,
        timestamp: int | None = None,
        source_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        last_decision_at: int | None = None,
        last_touched_at: int | None = None,
        container_type: str | None = None,
        container_key: str | None = None,
        container_name: str | None = None,
        artifact_flags: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_media_assets
                WHERE profile_user_id = ? AND asset_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(asset_id)),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_user_media_asset(dict(row))
            next_display_name = str(display_name).strip() if display_name is not None else str(current.get("display_name") or "")
            next_status = self._normalize_gift_status(status) if status else current["status"]
            merged_source_ids = list(current["source_ids"])
            for source_id in self._normalize_string_list(source_ids):
                if source_id not in merged_source_ids:
                    merged_source_ids.append(source_id)
            next_source_ids = merged_source_ids
            next_payload = payload if isinstance(payload, dict) else dict(current["payload"])
            next_last_decision_at = (
                int(last_decision_at)
                if last_decision_at is not None
                else int(current.get("last_decision_at") or 0)
            )
            next_last_touched_at = (
                int(last_touched_at)
                if last_touched_at is not None
                else effective_ts
            )
            next_container = self._derive_container_fields(
                asset_type=current["asset_type"],
                status=next_status,
                payload=next_payload,
                container_type=current["container_type"] if container_type is None else container_type,
                container_key="" if container_key is None else container_key,
                container_name="" if container_name is None else container_name,
                artifact_flags=current["artifact_flags"] if artifact_flags is None else artifact_flags,
            )
            conn.execute(
                """
                UPDATE user_media_assets
                SET display_name = ?, status = ?, updated_at = ?, source_ids_json = ?, payload_json = ?,
                    last_decision_at = ?, last_touched_at = ?, container_type = ?, container_key = ?,
                    container_name = ?, artifact_flags_json = ?
                WHERE profile_user_id = ? AND asset_id = ?
                """,
                (
                    next_display_name,
                    next_status,
                    effective_ts,
                    json.dumps(next_source_ids, ensure_ascii=False),
                    json.dumps(next_payload, ensure_ascii=False),
                    next_last_decision_at,
                    next_last_touched_at,
                    next_container["container_type"],
                    next_container["container_key"],
                    next_container["container_name"],
                    json.dumps(next_container["artifact_flags"], ensure_ascii=False),
                    str(profile_user_id),
                    str(asset_id),
                ),
            )
        return self._row_to_user_media_asset(
            {
                **dict(row),
                "display_name": next_display_name,
                "status": next_status,
                "updated_at": effective_ts,
                "source_ids_json": json.dumps(next_source_ids, ensure_ascii=False),
                "payload_json": json.dumps(next_payload, ensure_ascii=False),
                "last_decision_at": next_last_decision_at,
                "last_touched_at": next_last_touched_at,
                "container_type": next_container["container_type"],
                "container_key": next_container["container_key"],
                "container_name": next_container["container_name"],
                "artifact_flags_json": json.dumps(next_container["artifact_flags"], ensure_ascii=False),
            }
        )

    def update_user_media_asset_status(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
        status: str,
        timestamp: int | None = None,
        ) -> dict[str, Any] | None:
        return self.update_gift_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
            status=status,
            timestamp=timestamp,
            last_decision_at=int(timestamp or time.time()),
            last_touched_at=int(timestamp or time.time()),
        )

    def get_vision_observation(
        self,
        *,
        observation_type: str,
        resource_fingerprint: str,
        prompt_version: str = "v1",
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM vision_observations
                WHERE observation_type = ? AND resource_fingerprint = ? AND prompt_version = ?
                LIMIT 1
                """,
                (
                    str(observation_type or "").strip(),
                    str(resource_fingerprint or "").strip(),
                    str(prompt_version or "v1").strip() or "v1",
                ),
            ).fetchone()
        return self._row_to_vision_observation(dict(row)) if row else None

    def upsert_vision_observation(
        self,
        *,
        observation_type: str,
        resource_fingerprint: str,
        target_id: str = "",
        source_path: str = "",
        public_path: str = "",
        prompt_version: str = "v1",
        provider: str = "",
        model_name: str = "",
        status: str = "ready",
        summary: str = "",
        observation: dict[str, Any] | None = None,
        error_message: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_type = str(observation_type or "").strip()
        normalized_fingerprint = str(resource_fingerprint or "").strip()
        normalized_prompt_version = str(prompt_version or "v1").strip() or "v1"
        if not normalized_type or not normalized_fingerprint:
            raise ValueError("observation_type and resource_fingerprint are required")

        normalized_observation = observation if isinstance(observation, dict) else {}
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM vision_observations
                WHERE observation_type = ? AND resource_fingerprint = ? AND prompt_version = ?
                LIMIT 1
                """,
                (
                    normalized_type,
                    normalized_fingerprint,
                    normalized_prompt_version,
                ),
            ).fetchone()

            if existing is None:
                payload = {
                    "observation_id": f"vision::{uuid.uuid4()}",
                    "observation_type": normalized_type,
                    "resource_fingerprint": normalized_fingerprint,
                    "target_id": str(target_id or "").strip(),
                    "source_path": str(source_path or "").strip(),
                    "public_path": str(public_path or "").strip(),
                    "prompt_version": normalized_prompt_version,
                    "provider": str(provider or "").strip(),
                    "model_name": str(model_name or "").strip(),
                    "status": str(status or "ready").strip() or "ready",
                    "summary": str(summary or "").strip(),
                    "observation_json": json.dumps(normalized_observation, ensure_ascii=False),
                    "error_message": str(error_message or "").strip(),
                    "created_at": effective_ts,
                    "updated_at": effective_ts,
                }
                conn.execute(
                    """
                    INSERT INTO vision_observations (
                        observation_id, observation_type, resource_fingerprint, target_id,
                        source_path, public_path, prompt_version, provider, model_name,
                        status, summary, observation_json, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["observation_id"],
                        payload["observation_type"],
                        payload["resource_fingerprint"],
                        payload["target_id"],
                        payload["source_path"],
                        payload["public_path"],
                        payload["prompt_version"],
                        payload["provider"],
                        payload["model_name"],
                        payload["status"],
                        payload["summary"],
                        payload["observation_json"],
                        payload["error_message"],
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                return self._row_to_vision_observation(payload)

            payload = {
                **dict(existing),
                "target_id": str(target_id or existing["target_id"] or "").strip(),
                "source_path": str(source_path or existing["source_path"] or "").strip(),
                "public_path": str(public_path or existing["public_path"] or "").strip(),
                "provider": str(provider or existing["provider"] or "").strip(),
                "model_name": str(model_name or existing["model_name"] or "").strip(),
                "status": str(status or existing["status"] or "ready").strip() or "ready",
                "summary": str(summary or existing["summary"] or "").strip(),
                "observation_json": json.dumps(
                    normalized_observation or self._safe_json_loads(existing["observation_json"], fallback={}),
                    ensure_ascii=False,
                ),
                "error_message": str(error_message or "").strip(),
                "updated_at": effective_ts,
            }
            conn.execute(
                """
                UPDATE vision_observations
                SET target_id = ?, source_path = ?, public_path = ?, provider = ?, model_name = ?,
                    status = ?, summary = ?, observation_json = ?, error_message = ?, updated_at = ?
                WHERE observation_id = ?
                """,
                (
                    payload["target_id"],
                    payload["source_path"],
                    payload["public_path"],
                    payload["provider"],
                    payload["model_name"],
                    payload["status"],
                    payload["summary"],
                    payload["observation_json"],
                    payload["error_message"],
                    payload["updated_at"],
                    payload["observation_id"],
                ),
            )
        return self._row_to_vision_observation(payload)

    def get_latest_eval_turn(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM eval_turns
                WHERE session_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return self._row_to_eval_turn(dict(row)) if row else None

    def get_latest_eval_turn_for_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id)
            if character_pack_id is not None
            else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM eval_turns
                WHERE profile_user_id = ? AND session_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (
                    str(profile_user_id),
                    str(session_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                ),
            ).fetchone()
        return self._row_to_eval_turn(dict(row)) if row else None

    def _row_to_message(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "seq_no": int(row["seq_no"]),
            "role": row["role"],
            "content": row["content"],
            "timestamp": int(row["timestamp"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "index_in_vector": bool(int(row.get("index_in_vector", 1) or 0)),
            "is_summarized": int(row.get("is_summarized", 0)),
            "summary_id": row.get("summary_id", "") or "",
            "entry_type": "raw",
        }

    def _row_to_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary_id": row["summary_id"],
            "source_id": row["summary_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "timestamp": int(row["timestamp"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "period_label": row["period_label"],
            "event_type": row["event_type"],
            "importance": float(row["importance"]),
            "diary_summary": row["diary_summary"],
            "key_events": json.loads(row.get("key_events_json") or "[]"),
            "core_facts": json.loads(row.get("core_facts_json") or "[]"),
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "is_semanticized": int(row.get("is_semanticized", 0)),
            "semantic_id": row.get("semantic_id", "") or "",
            "source_start_seq": int(row["source_start_seq"]),
            "source_end_seq": int(row["source_end_seq"]),
            "source_ids": json.loads(row.get("source_ids_json") or "[]"),
            "entry_type": "summary",
        }

    def _row_to_semantic_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "semantic_id": row["semantic_id"],
            "source_id": row["semantic_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "created_at": int(row["created_at"]),
            "timestamp": int(row["timestamp"]),
            "period_start_ts": int(row["period_start_ts"]),
            "period_end_ts": int(row["period_end_ts"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "importance": float(row["importance"]),
            "semantic_summary": row["semantic_summary"],
            "stable_facts": json.loads(row.get("stable_facts_json") or "[]"),
            "recurring_topics": json.loads(row.get("recurring_topics_json") or "[]"),
            "important_people": json.loads(row.get("important_people_json") or "[]"),
            "open_loops": json.loads(row.get("open_loops_json") or "[]"),
            "semantic_tags": json.loads(row.get("semantic_tags_json") or "[]"),
            "memory_metadata": _loads_json_object(row.get("memory_metadata_json")),
            "source_summary_ids": json.loads(row.get("source_summary_ids_json") or "[]"),
            "reinforcement_count": int(row.get("reinforcement_count", 1) or 1),
            "last_reinforced_ts": int(row.get("last_reinforced_ts", row["timestamp"]) or row["timestamp"]),
            "entry_type": "semantic_summary",
        }

    def _iter_table_batches(
        self,
        *,
        table_name: str,
        converter,
        batch_size: int,
        where_clause: str = "",
    ):
        normalized_batch_size = max(1, int(batch_size))
        last_rowid = 0
        normalized_where = str(where_clause or "").strip()
        if normalized_where:
            normalized_where = f"({normalized_where}) AND rowid > ?"
        else:
            normalized_where = "rowid > ?"
        while True:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT rowid AS _rowid, * FROM {table_name}
                    WHERE {normalized_where}
                    ORDER BY rowid ASC
                    LIMIT ?
                    """,
                    (int(last_rowid), normalized_batch_size),
                ).fetchall()
            if not rows:
                return
            payload = [converter(dict(row)) for row in rows]
            if payload:
                yield payload
            last_rowid = int(rows[-1]["_rowid"])

    def _row_to_eval_turn(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "trace_id": row["trace_id"],
            "created_at": int(row["created_at"]),
            "session_id": row["session_id"],
            "profile_user_id": row["profile_user_id"],
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "user_message": row["user_message"],
            "router_json": json.loads(row.get("router_json") or "{}"),
            "verifier_json": json.loads(row.get("verifier_json") or "{}"),
            "final_json": json.loads(row.get("final_json") or "{}"),
        }

    def _row_to_session(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"]),
            "profile_user_id": str(row["profile_user_id"]),
            "character_pack_id": normalize_character_pack_id(row.get("character_pack_id", "")),
            "display_title": str(row.get("display_title", "") or "").strip() or "新的对话",
            "current_gift_focus_asset_id": str(row.get("current_gift_focus_asset_id", "") or "").strip(),
            "current_gift_focus_updated_at": int(row.get("current_gift_focus_updated_at", 0) or 0),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }

    def _row_to_reminder(
        self,
        row: dict[str, Any],
        *,
        status: str | None = None,
        fired_at: int | None = None,
    ) -> dict[str, Any]:
        return {
            "reminder_id": row["reminder_id"],
            "profile_user_id": row["profile_user_id"],
            "session_id": row["session_id"],
            "content": row["content"],
            "due_ts": int(row["due_ts"]),
            "date_label": row["date_label"],
            "time_of_day": row["time_of_day"],
            "raw_time_text": row.get("raw_time_text", "") or "",
            "status": status or row.get("status", "pending") or "pending",
            "created_at": int(row["created_at"]),
            "fired_at": int(fired_at if fired_at is not None else row.get("fired_at", 0) or 0),
        }

    def _row_to_persona_card(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_id": str(row.get("card_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "name": str(row.get("name") or ""),
            "status": self._normalize_persona_status(row.get("status")),
            "summary": str(row.get("summary") or ""),
            "speech_style": str(row.get("speech_style") or ""),
            "interaction_bias": str(row.get("interaction_bias") or ""),
            "resource_preference": str(row.get("resource_preference") or ""),
            "switch_hint": str(row.get("switch_hint") or ""),
            "unsuitable_contexts": str(row.get("unsuitable_contexts") or ""),
            "created_reason": str(row.get("created_reason") or ""),
            "updated_reason": str(row.get("updated_reason") or ""),
            "source_ids": self._normalize_string_list(
                self._safe_json_loads(row.get("source_ids_json"), fallback=[])
            ),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "archived_at": int(row.get("archived_at", 0) or 0),
        }

    def _row_to_persona_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "event_id": str(row.get("event_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "card_id": str(row.get("card_id") or ""),
            "event_type": str(row.get("event_type") or ""),
            "reason": str(row.get("reason") or ""),
            "source_id": str(row.get("source_id") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "payload": payload,
        }

    def _row_to_user_media_asset(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        legacy_payload = self._build_legacy_gift_payload(row)
        merged_payload = {
            **legacy_payload,
            **payload,
        }
        asset_type = str(row.get("asset_type") or "").strip() or self._infer_asset_type_from_media_kind(
            row.get("media_kind")
        )
        status = self._normalize_gift_status(row.get("status"))
        origin_source_id = str(row.get("origin_source_id") or "").strip()
        source_ids = self._normalize_string_list(
            self._safe_json_loads(row.get("source_ids_json"), fallback=[])
        )
        if origin_source_id and origin_source_id not in source_ids:
            source_ids.insert(0, origin_source_id)
        artifact_flags = self._safe_json_loads(row.get("artifact_flags_json"), fallback={})
        if not isinstance(artifact_flags, dict):
            artifact_flags = {}
        container = self._derive_container_fields(
            asset_type=asset_type,
            status=status,
            payload=merged_payload,
            container_type=row.get("container_type"),
            container_key=row.get("container_key"),
            container_name=row.get("container_name"),
            artifact_flags=artifact_flags,
        )
        return {
            "asset_id": str(row["asset_id"]),
            "resource_id": str(row.get("resource_id") or ""),
            "profile_user_id": str(row["profile_user_id"]),
            "session_id": str(row["session_id"]),
            "media_kind": str(row.get("media_kind") or ""),
            "origin_name": str(row.get("origin_name") or ""),
            "display_name": str(row.get("display_name") or ""),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "status": status,
            "asset_type": asset_type,
            "origin_event_type": str(row.get("origin_event_type") or "").strip(),
            "origin_source_id": origin_source_id,
            "source_ids": source_ids,
            "payload": merged_payload,
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_decision_at": int(row.get("last_decision_at", 0) or 0),
            "last_touched_at": int(
                row.get("last_touched_at", row.get("updated_at", row.get("created_at", 0))) or 0
            ),
            "container_type": container["container_type"],
            "container_key": container["container_key"],
            "container_name": container["container_name"],
            "artifact_flags": container["artifact_flags"],
        }

    def _row_to_attachment_inbox_item(self, row: dict[str, Any]) -> dict[str, Any]:
        detail = self._safe_json_loads(row.get("detail_json"), fallback={})
        if not isinstance(detail, dict):
            detail = {}
        return {
            "attachment_id": str(row.get("attachment_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "source": str(row.get("source") or ""),
            "kind": self._normalize_attachment_kind(row.get("kind")),
            "attachment_handle": str(row.get("attachment_handle") or ""),
            "sequence_no": int(row.get("sequence_no", 0) or 0),
            "status": self._normalize_attachment_status(row.get("status")),
            "origin_name": str(row.get("origin_name") or ""),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "source_event_id": str(row.get("source_event_id") or ""),
            "source_message_id": str(row.get("source_message_id") or ""),
            "summary_title": str(row.get("summary_title") or ""),
            "short_hint": str(row.get("short_hint") or ""),
            "detail": detail,
            "error_message": str(row.get("error_message") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_used_at": int(row.get("last_used_at", 0) or 0),
            "focus_rank": int(row.get("focus_rank", 0) or 0),
            "expires_at": int(row.get("expires_at", 0) or 0),
        }

    def _row_to_generated_file(self, row: dict[str, Any]) -> dict[str, Any]:
        content_card = self._safe_json_loads(row.get("content_card_json"), fallback={})
        if not isinstance(content_card, dict):
            content_card = {}
        source_ids = self._safe_json_loads(row.get("source_ids_json"), fallback=[])
        if not isinstance(source_ids, list):
            source_ids = []
        return {
            "generated_id": str(row.get("generated_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "generated_handle": str(row.get("generated_handle") or ""),
            "sequence_no": int(row.get("sequence_no", 0) or 0),
            "status": self._normalize_generated_status(row.get("status")),
            "output_title": str(row.get("output_title") or ""),
            "output_format": self._normalize_generated_format(row.get("output_format")),
            "mime_type": str(row.get("mime_type") or ""),
            "file_ext": str(row.get("file_ext") or ""),
            "file_size": int(row.get("file_size", 0) or 0),
            "storage_relpath": str(row.get("storage_relpath") or ""),
            "source_ids": [str(item or "").strip() for item in source_ids if str(item or "").strip()],
            "content_card": content_card,
            "summary": str(row.get("summary") or ""),
            "created_by_tool": str(row.get("created_by_tool") or ""),
            "version_of_generated_id": str(row.get("version_of_generated_id") or ""),
            "version_no": int(row.get("version_no", 1) or 1),
            "delivery_status": str(row.get("delivery_status") or "pending"),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "last_used_at": int(row.get("last_used_at", 0) or 0),
        }

    def _row_to_desktop_music_timeline(self, row: dict[str, Any]) -> dict[str, Any]:
        segments = self._safe_json_loads(row.get("segments_json"), fallback=[])
        if not isinstance(segments, list):
            segments = []
        return {
            "timeline_id": str(row.get("timeline_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "source_id": str(row.get("source_id") or ""),
            "source_kind": str(row.get("source_kind") or ""),
            "source_handle": str(row.get("source_handle") or ""),
            "title": str(row.get("title") or ""),
            "status": self._normalize_desktop_music_timeline_status(row.get("status")),
            "segments": [item for item in segments if isinstance(item, dict)],
            "rolling_summary": str(row.get("rolling_summary") or ""),
            "ready_until_seconds": float(row.get("ready_until_seconds", 0) or 0),
            "transcript_generated_id": str(row.get("transcript_generated_id") or ""),
            "transcript_generated_handle": str(row.get("transcript_generated_handle") or ""),
            "error_message": str(row.get("error_message") or ""),
            "source_size": int(row.get("source_size", 0) or 0),
            "source_mtime_ns": int(row.get("source_mtime_ns", 0) or 0),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }

    def _row_to_task_workspace(self, row: dict[str, Any]) -> dict[str, Any]:
        raw_request = self._safe_json_loads(row.get("raw_request_json"), fallback={})
        if not isinstance(raw_request, dict):
            raw_request = {}
        success_criteria = self._safe_json_loads(row.get("success_criteria_json"), fallback=[])
        if not isinstance(success_criteria, list):
            success_criteria = []
        constraints = self._safe_json_loads(row.get("constraints_json"), fallback=[])
        if not isinstance(constraints, list):
            constraints = []
        steps = self._safe_json_loads(row.get("steps_json"), fallback=[])
        if not isinstance(steps, list):
            steps = []
        artifacts = self._safe_json_loads(row.get("artifacts_json"), fallback=[])
        if not isinstance(artifacts, list):
            artifacts = []
        pending_question = self._safe_json_loads(row.get("pending_question_json"), fallback={})
        if not isinstance(pending_question, dict):
            pending_question = {}
        metadata = self._safe_json_loads(row.get("metadata_json"), fallback={})
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "task_id": str(row.get("task_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "owner": str(row.get("owner") or "Akane"),
            "status": self._normalize_task_workspace_status(row.get("status")),
            "raw_request": raw_request,
            "normalized_goal": str(row.get("normalized_goal") or ""),
            "success_criteria": success_criteria,
            "constraints": constraints,
            "steps": steps,
            "artifacts": artifacts,
            "pending_question": pending_question,
            "metadata": metadata,
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
            "completed_at": int(row.get("completed_at", 0) or 0),
            "cleaned_at": int(row.get("cleaned_at", 0) or 0),
        }

    def _row_to_task_workspace_event(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("payload_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "event_id": str(row.get("event_id") or ""),
            "task_id": str(row.get("task_id") or ""),
            "profile_user_id": str(row.get("profile_user_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "event_type": str(row.get("event_type") or ""),
            "from_actor": str(row.get("from_actor") or ""),
            "priority": self._normalize_task_event_priority(row.get("priority")),
            "requires_user": bool(int(row.get("requires_user", 0) or 0)),
            "message": str(row.get("message") or ""),
            "payload": payload,
            "status": self._normalize_task_event_status(row.get("status")),
            "created_at": int(row.get("created_at", 0) or 0),
            "handled_at": int(row.get("handled_at", 0) or 0),
        }

    def _row_to_vision_observation(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._safe_json_loads(row.get("observation_json"), fallback={})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "observation_id": str(row.get("observation_id") or ""),
            "observation_type": str(row.get("observation_type") or ""),
            "resource_fingerprint": str(row.get("resource_fingerprint") or ""),
            "target_id": str(row.get("target_id") or ""),
            "source_path": str(row.get("source_path") or ""),
            "public_path": str(row.get("public_path") or ""),
            "prompt_version": str(row.get("prompt_version") or "v1"),
            "provider": str(row.get("provider") or ""),
            "model_name": str(row.get("model_name") or ""),
            "status": str(row.get("status") or "ready"),
            "summary": str(row.get("summary") or ""),
            "observation": payload,
            "error_message": str(row.get("error_message") or ""),
            "created_at": int(row.get("created_at", 0) or 0),
            "updated_at": int(row.get("updated_at", row.get("created_at", 0)) or 0),
        }

    def _normalize_gift_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "offered"}:
            return "pending"
        if normalized == "saved":
            return "kept"
        if normalized in {"pending", "kept", "internalized", "rejected"}:
            return normalized
        return "pending"

    def _normalize_persona_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"active", "inactive", "archived", "deleted"}:
            return normalized
        return "inactive"

    def _normalize_attachment_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "pending": "pending_observation",
            "processing": "pending_observation",
            "observing": "pending_observation",
            "done": "ready",
            "ok": "ready",
            "error": "failed",
            "deleted": "cleared",
            "removed": "cleared",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending_observation", "ready", "failed", "cleared"}:
            return normalized
        return "pending_observation"

    def _normalize_desktop_music_timeline_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "queued": "pending",
            "building": "processing",
            "done": "ready",
            "ok": "ready",
            "error": "failed",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending", "processing", "ready", "failed", "partial"}:
            return normalized
        return "pending"

    def _normalize_attachment_kind(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "photo": "image",
            "picture": "image",
            "pic": "image",
            "img": "image",
            "file": "file",
            "doc": "document",
            "text": "document",
            "txt": "document",
            "pdf": "document",
            "music": "audio",
            "song": "audio",
            "voice": "audio",
            "any": "any",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"any", "image", "file", "document", "audio"}:
            return normalized
        return "file"

    def _normalize_generated_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "done": "ready",
            "ok": "ready",
            "created": "ready",
            "error": "failed",
            "deleted": "removed",
            "cleared": "removed",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"ready", "failed", "removed"}:
            return normalized
        return "ready"

    def _normalize_generated_format(self, value: Any) -> str:
        normalized = str(value or "").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html", "srt", "vtt", "zip", "mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}:
            return normalized
        return "md"

    def _attachment_handle_prefix(self, kind: Any) -> str:
        normalized = self._normalize_attachment_kind(kind)
        if normalized == "image":
            return "img"
        if normalized == "audio":
            return "audio"
        return "file"

    def _sequence_no_from_generated_handle(self, value: Any) -> int:
        text = str(value or "").strip()
        if "_" not in text:
            return 0
        try:
            return int(text.rsplit("_", 1)[-1])
        except ValueError:
            return 0

    def _sequence_no_from_attachment_handle(self, value: Any) -> int:
        text = str(value or "").strip()
        if "_" not in text:
            return 0
        try:
            return int(text.rsplit("_", 1)[-1])
        except ValueError:
            return 0

    def _normalize_gift_status_list(self, values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_gift_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized

    def _normalize_attachment_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_attachment_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized

    def _normalize_generated_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_generated_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized

    def _normalize_task_workspace_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "todo": "queued",
            "pending": "queued",
            "working": "running",
            "done": "completed",
            "ok": "completed",
            "error": "failed",
            "cancelled": "canceled",
            "deleted": "cleaned",
            "cleared": "cleaned",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"queued", "running", "waiting_user", "completed", "failed", "canceled", "cleaned"}:
            return normalized
        return "queued"

    def _normalize_task_workspace_status_list(
        self,
        values: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            status = self._normalize_task_workspace_status(value)
            if status not in normalized:
                normalized.append(status)
        return normalized

    def _normalize_task_event_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        aliases = {
            "done": "handled",
            "ok": "handled",
            "read": "handled",
            "new": "pending",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"pending", "handled", "dismissed"}:
            return normalized
        return "pending"

    def _normalize_task_event_priority(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"low", "normal", "high", "urgent"}:
            return normalized
        return "normal"

    def _normalize_string_list(self, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, (str, bytes)):
            raw_values = [values]
        elif isinstance(values, (list, tuple, set)):
            raw_values = list(values)
        else:
            return []

        normalized: list[str] = []
        for value in raw_values:
            item = str(value or "").strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    def _infer_asset_type_from_media_kind(self, media_kind: Any) -> str:
        normalized = str(media_kind or "").strip().lower()
        if normalized in {"bgm", "audio", "music", "song"}:
            return "audio"
        if normalized in {"image", "background", "picture"}:
            return "image"
        if normalized in {"virtual", "story_gift", "item"}:
            return "virtual"
        return normalized or "audio"

    def _build_legacy_gift_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        origin_name = str(row.get("origin_name") or "").strip()
        mime_type = str(row.get("mime_type") or "").strip()
        file_ext = str(row.get("file_ext") or "").strip()
        storage_relpath = str(row.get("storage_relpath") or "").strip()

        if origin_name:
            payload["filename"] = origin_name
        if mime_type:
            payload["mime_type"] = mime_type
        if file_ext:
            payload["file_ext"] = file_ext
        if storage_relpath:
            payload["storage_relpath"] = storage_relpath
        file_size = int(row.get("file_size", 0) or 0)
        if file_size > 0:
            payload["file_size"] = file_size
        return payload

    def _normalize_container_type(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"music", "music_box", "bgm_box"}:
            return "music_box"
        if normalized in {"album", "photo_album"}:
            return "album"
        if normalized in {"note_box", "notes", "note"}:
            return "note_box"
        if normalized in {"keepsake_box", "keepsake", "collection_box", "collection"}:
            return "keepsake_box"
        return normalized

    def _derive_container_fields(
        self,
        *,
        asset_type: Any,
        status: Any,
        payload: dict[str, Any] | None,
        container_type: Any,
        container_key: Any,
        container_name: Any,
        artifact_flags: Any,
    ) -> dict[str, Any]:
        normalized_asset_type = str(asset_type or "").strip().lower() or "audio"
        normalized_status = self._normalize_gift_status(status)
        normalized_payload = payload if isinstance(payload, dict) else {}
        normalized_flags = artifact_flags if isinstance(artifact_flags, dict) else {}

        resolved_type = str(container_type or "").strip().lower()
        resolved_key = str(container_key or "").strip()
        resolved_name = str(container_name or "").strip()

        if not resolved_type:
            if normalized_asset_type == "audio":
                resolved_type = "music_box"
            elif normalized_asset_type == "image":
                resolved_type = "album"
            elif normalized_asset_type in {"text", "note"}:
                resolved_type = "note_box"
            else:
                resolved_type = "keepsake_box"

        if not resolved_key:
            if resolved_type == "music_box":
                resolved_key = "main"
            elif resolved_type == "album":
                resolved_key = str(normalized_payload.get("collection_key") or "").strip() or "memories"
            elif resolved_type == "note_box":
                resolved_key = "notes"
            else:
                resolved_key = "keepsakes"

        if not resolved_name:
            if resolved_type == "music_box":
                resolved_name = "曲库"
            elif resolved_type == "album":
                resolved_name = str(normalized_payload.get("collection_name") or "").strip() or "回忆"
            elif resolved_type == "note_box":
                resolved_name = "便签盒"
            else:
                resolved_name = "收藏盒"

        next_flags = dict(normalized_flags)
        next_flags.setdefault("archived", normalized_status in {"kept", "internalized"})
        next_flags.setdefault("container_version", 1)
        return {
            "container_type": resolved_type,
            "container_key": resolved_key[:64],
            "container_name": resolved_name[:32],
            "artifact_flags": next_flags,
        }

    def _safe_json_loads(self, raw: Any, *, fallback: Any) -> Any:
        if raw in (None, ""):
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
