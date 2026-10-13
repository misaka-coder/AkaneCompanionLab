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

from ..text_utils import timestamp_to_date_label, infer_time_of_day
from .base import (
    CHARACTER_PACK_ID_PATTERN,
    GENERATED_FILE_FORMAT_PATTERN,
    MessageSourceIdCollisionError,
    normalize_character_pack_id,
    _loads_json_object,
    logger,
    BaseStoreMixin,
)
from .sessions import SessionStoreMixin
from .messages import MessageStoreMixin
from .legacy_summaries import LegacySummaryStoreMixin
from .eval_turns import EvalTurnStoreMixin
from .reminders import ReminderStoreMixin
from .attachments import AttachmentStoreMixin
from .generated_files import GeneratedFileStoreMixin
from .music_timeline import MusicTimelineStoreMixin
from .workspaces import WorkspaceStoreMixin
from .personas import PersonaStoreMixin
from .assets import AssetStoreMixin
from .vision import VisionStoreMixin


class MemoryStore(
    BaseStoreMixin,
    SessionStoreMixin,
    MessageStoreMixin,
    LegacySummaryStoreMixin,
    EvalTurnStoreMixin,
    ReminderStoreMixin,
    AttachmentStoreMixin,
    GeneratedFileStoreMixin,
    MusicTimelineStoreMixin,
    WorkspaceStoreMixin,
    PersonaStoreMixin,
    AssetStoreMixin,
    VisionStoreMixin,
):
    """SQLite-backed memory store for Akane companion application data."""

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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()



    def _init_db(self) -> None:
        with self._connect() as conn:
            journal_mode = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] or "").lower()
            if journal_mode != "wal":
                raise sqlite3.OperationalError("memory_store_wal_unavailable")
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
                    prompt_envelope_text TEXT NOT NULL DEFAULT '',
                    index_in_vector INTEGER NOT NULL DEFAULT 1,
                    is_summarized INTEGER NOT NULL DEFAULT 0,
                    summary_id TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_chat_session_seq
                ON chat_messages(session_id, seq_no);

                CREATE INDEX IF NOT EXISTS idx_chat_profile_time
                ON chat_messages(profile_user_id, timestamp);

                CREATE TABLE IF NOT EXISTS owner_private_voice_activity (
                    character_pack_id TEXT PRIMARY KEY,
                    last_user_at INTEGER NOT NULL DEFAULT 0,
                    last_reply_at INTEGER NOT NULL DEFAULT 0
                );

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

                CREATE TABLE IF NOT EXISTS attachment_inbox_private_sources (
                    attachment_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT '',
                    locator_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at INTEGER NOT NULL DEFAULT 0,
                    last_attempt_route TEXT NOT NULL DEFAULT '',
                    last_attempt_status TEXT NOT NULL DEFAULT '',
                    last_attempt_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_attachment_private_source_scope
                ON attachment_inbox_private_sources(profile_user_id, session_id, updated_at DESC);

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

                CREATE TABLE IF NOT EXISTS project_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    owner_kind TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    actor_scope TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'active',
                    root_relpath TEXT NOT NULL,
                    root_kind TEXT NOT NULL DEFAULT 'managed',
                    host_root_path TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    archived_at INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_project_workspaces_owner_state
                ON project_workspaces(owner_kind, owner_id, actor_scope, state, updated_at DESC);

                CREATE TABLE IF NOT EXISTS project_workspace_selections (
                    selection_key TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._ensure_column(
                conn=conn,
                table_name="project_workspaces",
                column_name="root_kind",
                column_definition="TEXT NOT NULL DEFAULT 'managed'",
            )
            self._ensure_column(
                conn=conn,
                table_name="project_workspaces",
                column_name="host_root_path",
                column_definition="TEXT NOT NULL DEFAULT ''",
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
                table_name="chat_messages",
                column_name="prompt_envelope_text",
                column_definition="TEXT NOT NULL DEFAULT ''",
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
                -- Ascending time also orders implicit rowid ascending. Scanning
                -- backwards satisfies created_at DESC, rowid DESC without sorting.
                CREATE INDEX IF NOT EXISTS idx_eval_session_time
                ON eval_turns(session_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_eval_profile_session_time
                ON eval_turns(profile_user_id, session_id, created_at);

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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_workspaces_owner_state
                ON project_workspaces(owner_kind, owner_id, actor_scope, state, updated_at DESC)
                """
            )
            self._normalize_legacy_gift_rows(conn=conn)
            conn.execute(
                """
                UPDATE attachment_inbox_private_sources
                SET locator_json = '{}'
                WHERE expires_at > 0 AND expires_at <= ? AND locator_json != '{}'
                """,
                (int(time.time()),),
            )



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
            conn.execute("DELETE FROM attachment_inbox_private_sources")
            conn.execute("DELETE FROM attachment_inbox_items")
            conn.execute("DELETE FROM workspace_file_states")
            conn.execute("DELETE FROM desktop_music_timelines")
