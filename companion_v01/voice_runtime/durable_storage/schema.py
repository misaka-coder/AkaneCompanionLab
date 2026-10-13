"""SQLite setup and validation of the one existing journal schema."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any
from voicecore import voice_command_from_dict, voice_event_from_dict, voice_event_to_dict
from .contracts import (
    _JournalStorageError,
    _STORAGE_SCHEMA_VERSION,
    _COMMAND_RECEIPT_PHASES,
    _PROJECTION_RECORD_FIELDS,
    _PROJECTION_TARGETS,
)
from .storage import _canonical_json, _canonical_json_value, _record_set_digest, _storage_path_is_owned, _utf8_digest


class JournalSchema:
    __slots__ = ()

    def _open_database(self, *, create: bool) -> sqlite3.Connection:
        if not _storage_path_is_owned(self._database_path):
            raise _JournalStorageError("voice_journal_unreadable")
        if not create and not self._database_path.is_file():
            raise _JournalStorageError("voice_journal_unreadable")
        if create:
            try:
                self._conversation_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise _JournalStorageError("voice_journal_write_failed") from exc
        new_database = create and not self._database_path.exists()
        database = self._database_path if new_database else self._database_path.as_uri() + "?mode=rw"
        connection = sqlite3.connect(database, timeout=5.0, uri=not new_database)
        try:
            if not new_database:
                try:
                    self._validate_schema(connection)
                except sqlite3.Error as exc:
                    if getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                        raise
                    raise _JournalStorageError("voice_journal_unreadable") from exc
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            if new_database:
                self._ensure_schema(connection)
            if os.name == "posix":
                os.chmod(self._database_path, 0o600)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        """Existing storage is evidence to validate, never a schema to repair."""

        meta_rows = connection.execute("SELECT singleton_id, schema_version FROM voice_journal_meta").fetchall()
        if not meta_rows:
            raise _JournalStorageError("voice_journal_unreadable")
        if meta_rows != [(1, _STORAGE_SCHEMA_VERSION)]:
            raise _JournalStorageError("voice_journal_schema_incompatible")
        for query in (
            "SELECT singleton_id, record_count FROM voice_journal_head LIMIT 0",
            "SELECT ordinal, event_id, event_sha256, event_json, projection_count, projection_set_sha256 "
            "FROM voice_events LIMIT 0",
            "SELECT projection_id, event_ordinal, projection_index, projection_sha256, projection_json, delivery_status "
            "FROM voice_projection_outbox LIMIT 0",
            "SELECT receipt_ordinal, command_id, idempotency_key, causation_event_id, command_sha256, command_json, "
            "observation_count, observation_set_sha256, observations_json, phase FROM voice_command_receipts LIMIT 0",
        ):
            connection.execute(query)
        heads = connection.execute("SELECT singleton_id FROM voice_journal_head").fetchall()
        if heads != [(1,)]:
            raise _JournalStorageError("voice_journal_unreadable")

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS voice_journal_meta (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                schema_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voice_journal_head (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                record_count INTEGER NOT NULL CHECK (record_count >= 0)
            );
            CREATE TABLE IF NOT EXISTS voice_events (
                ordinal INTEGER PRIMARY KEY CHECK (ordinal > 0),
                event_id TEXT NOT NULL UNIQUE,
                event_sha256 TEXT NOT NULL,
                event_json TEXT NOT NULL,
                projection_count INTEGER NOT NULL CHECK (projection_count >= 0),
                projection_set_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voice_projection_outbox (
                projection_id TEXT PRIMARY KEY,
                event_ordinal INTEGER NOT NULL,
                projection_index INTEGER NOT NULL CHECK (projection_index >= 0),
                projection_sha256 TEXT NOT NULL,
                projection_json TEXT NOT NULL,
                delivery_status TEXT NOT NULL
                    CHECK (delivery_status IN ('pending', 'delivered')),
                UNIQUE (event_ordinal, projection_index),
                FOREIGN KEY (event_ordinal) REFERENCES voice_events(ordinal)
            );
            CREATE TABLE IF NOT EXISTS voice_command_receipts (
                receipt_ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                causation_event_id TEXT NOT NULL,
                command_sha256 TEXT NOT NULL,
                command_json TEXT NOT NULL,
                observation_count INTEGER NOT NULL CHECK (observation_count >= 0),
                observation_set_sha256 TEXT NOT NULL,
                observations_json TEXT NOT NULL,
                phase TEXT NOT NULL
                    CHECK (
                        phase IN (
                            'executing',
                            'observations_pending',
                            'completed',
                            'released'
                        )
                    ),
                FOREIGN KEY (causation_event_id) REFERENCES voice_events(event_id)
            );
            """
        )
        meta_rows = connection.execute("SELECT singleton_id, schema_version FROM voice_journal_meta").fetchall()
        if not meta_rows:
            connection.execute(
                "INSERT INTO voice_journal_meta (singleton_id, schema_version) VALUES (1, ?)",
                (_STORAGE_SCHEMA_VERSION,),
            )
        elif meta_rows != [(1, _STORAGE_SCHEMA_VERSION)]:
            raise _JournalStorageError("voice_journal_schema_incompatible")
        head_rows = connection.execute("SELECT singleton_id, record_count FROM voice_journal_head").fetchall()
        if not head_rows:
            connection.execute("INSERT INTO voice_journal_head (singleton_id, record_count) VALUES (1, 0)")
        elif len(head_rows) != 1 or head_rows[0][0] != 1:
            raise _JournalStorageError("voice_journal_unreadable")
        connection.commit()

    @staticmethod
    def _validated_head(connection: sqlite3.Connection) -> int:
        head = connection.execute("SELECT record_count FROM voice_journal_head WHERE singleton_id = 1").fetchone()
        if head is None or isinstance(head[0], bool) or not isinstance(head[0], int) or head[0] < 0:
            raise _JournalStorageError("voice_journal_unreadable")
        record_count = int(head[0])
        actual = connection.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(MIN(ordinal), 0),
                COALESCE(MAX(ordinal), 0)
            FROM voice_events
            """
        ).fetchone()
        if actual is None:
            raise _JournalStorageError("voice_journal_unreadable")
        actual_count, minimum_ordinal, maximum_ordinal = map(int, actual)
        expected_minimum = 1 if record_count else 0
        if actual_count != record_count or minimum_ordinal != expected_minimum or maximum_ordinal != record_count:
            raise _JournalStorageError("voice_journal_record_missing")
        rows = connection.execute("SELECT event_id, event_sha256, event_json FROM voice_events").fetchall()
        identity = None
        for event_id, expected_digest, canonical in rows:
            try:
                if not isinstance(canonical, str) or _utf8_digest(canonical) != expected_digest:
                    raise ValueError
                record = json.loads(canonical)
                if not isinstance(record, dict):
                    raise ValueError
                _canonical_json(record)
                event = voice_event_from_dict(record)
                event_identity = (event.conversation_id, event.conversation_generation)
                if event.event_id != event_id or (identity is not None and event_identity != identity):
                    raise ValueError
                identity = event_identity
            except (TypeError, ValueError):
                raise _JournalStorageError("voice_journal_unreadable") from None
        return record_count

    @staticmethod
    def _validated_projection_outbox(connection: sqlite3.Connection) -> None:
        event_rows = connection.execute(
            """
            SELECT ordinal, event_id, projection_count, projection_set_sha256
            FROM voice_events
            ORDER BY ordinal ASC
            """
        ).fetchall()
        outbox_rows = connection.execute(
            """
            SELECT
                projection_id,
                event_ordinal,
                projection_index,
                projection_sha256,
                projection_json,
                delivery_status
            FROM voice_projection_outbox
            ORDER BY event_ordinal ASC, projection_index ASC
            """
        ).fetchall()
        rows_by_event: dict[int, list[tuple[Any, ...]]] = {}
        for row in outbox_rows:
            if len(row) != 6:
                raise _JournalStorageError("voice_projection_outbox_unreadable")
            try:
                event_ordinal = int(row[1])
            except (TypeError, ValueError):
                raise _JournalStorageError("voice_projection_outbox_unreadable") from None
            rows_by_event.setdefault(event_ordinal, []).append(row)

        known_ordinals: set[int] = set()
        for event_row in event_rows:
            if len(event_row) != 4:
                raise _JournalStorageError("voice_projection_outbox_unreadable")
            ordinal, event_id, projection_count, expected_set_digest = event_row
            if (
                isinstance(ordinal, bool)
                or not isinstance(ordinal, int)
                or isinstance(projection_count, bool)
                or not isinstance(projection_count, int)
                or projection_count < 0
                or not isinstance(event_id, str)
                or not isinstance(expected_set_digest, str)
            ):
                raise _JournalStorageError("voice_projection_outbox_unreadable")
            known_ordinals.add(ordinal)
            rows = rows_by_event.get(ordinal, [])
            if len(rows) != projection_count:
                raise _JournalStorageError("voice_projection_outbox_record_missing")
            canonical_records: list[str] = []
            for expected_index, row in enumerate(rows):
                (
                    projection_id,
                    row_event_ordinal,
                    projection_index,
                    expected_digest,
                    canonical,
                    delivery_status,
                ) = row
                if (
                    row_event_ordinal != ordinal
                    or projection_index != expected_index
                    or not isinstance(projection_id, str)
                    or not projection_id
                    or not isinstance(expected_digest, str)
                    or not isinstance(canonical, str)
                    or delivery_status not in {"pending", "delivered"}
                    or _utf8_digest(canonical) != expected_digest
                ):
                    raise _JournalStorageError("voice_projection_outbox_unreadable")
                try:
                    record = json.loads(canonical)
                    if not isinstance(record, dict):
                        raise ValueError
                    _canonical_json(record)
                except (TypeError, ValueError):
                    raise _JournalStorageError("voice_projection_outbox_unreadable") from None
                if (
                    not isinstance(record, dict)
                    or set(record) != _PROJECTION_RECORD_FIELDS
                    or record.get("projection_id") != projection_id
                    or record.get("source_event_id") != event_id
                    or not isinstance(record.get("target"), str)
                    or record["target"] not in _PROJECTION_TARGETS
                    or not isinstance(record.get("kind"), str)
                    or not record["kind"]
                    or not isinstance(record.get("payload"), dict)
                ):
                    raise _JournalStorageError("voice_projection_outbox_unreadable")
                canonical_records.append(canonical)
            if _record_set_digest(canonical_records) != expected_set_digest:
                raise _JournalStorageError("voice_projection_outbox_unreadable")
        if set(rows_by_event) - known_ordinals:
            raise _JournalStorageError("voice_projection_outbox_unreadable")

    @staticmethod
    def _validated_command_receipts(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT
                command_id,
                idempotency_key,
                causation_event_id,
                command_sha256,
                command_json,
                observation_count,
                observation_set_sha256,
                observations_json,
                phase
            FROM voice_command_receipts
            ORDER BY receipt_ordinal ASC
            """
        ).fetchall()
        event_records = dict(connection.execute("SELECT event_id, event_json FROM voice_events")) if rows else {}
        for row in rows:
            if len(row) != 9:
                raise _JournalStorageError("voice_command_receipt_unreadable")
            (
                command_id,
                idempotency_key,
                causation_event_id,
                expected_command_digest,
                command_json,
                observation_count,
                expected_observation_digest,
                observations_json,
                phase,
            ) = row
            if (
                not isinstance(command_id, str)
                or not command_id
                or not isinstance(idempotency_key, str)
                or not idempotency_key
                or not isinstance(causation_event_id, str)
                or not causation_event_id
                or not isinstance(expected_command_digest, str)
                or not isinstance(command_json, str)
                or isinstance(observation_count, bool)
                or not isinstance(observation_count, int)
                or observation_count < 0
                or not isinstance(expected_observation_digest, str)
                or not isinstance(observations_json, str)
                or phase not in _COMMAND_RECEIPT_PHASES
                or _utf8_digest(command_json) != expected_command_digest
            ):
                raise _JournalStorageError("voice_command_receipt_unreadable")
            try:
                command_record = json.loads(command_json)
                observation_records = json.loads(observations_json)
                if not isinstance(command_record, dict) or not isinstance(observation_records, list):
                    raise TypeError("invalid command receipt record")
                command = voice_command_from_dict(command_record)
                _canonical_json(command_record)
                observations = tuple(
                    voice_event_from_dict(record) for record in observation_records if isinstance(record, dict)
                )
                cause = voice_event_from_dict(json.loads(event_records[causation_event_id]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise _JournalStorageError("voice_command_receipt_unreadable") from None
            if (
                len(observations) != len(observation_records)
                or command.command_id != command_id
                or command.idempotency_key != idempotency_key
                or command.causation_id != causation_event_id
                or len(observation_records) != observation_count
                or any(
                    observation.conversation_id != cause.conversation_id
                    or observation.conversation_generation != cause.conversation_generation
                    for observation in observations
                )
            ):
                raise _JournalStorageError("voice_command_receipt_unreadable")
            try:
                normalized_observations = [voice_event_to_dict(observation) for observation in observations]
                canonical_observations = [_canonical_json(observation) for observation in normalized_observations]
            except (TypeError, ValueError):
                raise _JournalStorageError("voice_command_receipt_unreadable") from None
            if (
                _record_set_digest(canonical_observations) != expected_observation_digest
                or _canonical_json_value(normalized_observations) != observations_json
            ):
                raise _JournalStorageError("voice_command_receipt_unreadable")
            if phase in {"executing", "released"}:
                if observation_count != 0:
                    raise _JournalStorageError("voice_command_receipt_unreadable")
            else:
                completion_indexes = [
                    index
                    for index, observation in enumerate(observations)
                    if observation.payload.get("command_id") == command_id
                ]
                if observation_count == 0 or completion_indexes != [observation_count - 1]:
                    raise _JournalStorageError("voice_command_receipt_unreadable")
