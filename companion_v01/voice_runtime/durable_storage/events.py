"""Atomic event/projection publication and existing journal replay."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from voicecore import VoiceEvent, VoiceRuntimeSnapshot, replay_events, voice_event_from_dict
from ..host import VoiceHostPortResult, VoiceProjectionOutboxLoadResult
from .contracts import (
    _JournalStorageError,
    VoiceJournalLoadResult,
    VoiceJournalReplayResult,
    _PROJECTION_RECORD_FIELDS,
    _PROJECTION_TARGETS,
)
from .storage import _canonical_json, _record_set_digest, _storage_path_is_owned


class JournalEvents:
    __slots__ = ()

    def append_transition(
        self,
        event_record: Mapping[str, Any],
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        normalized = self._normalize_event_record(event_record)
        if isinstance(normalized, str):
            return VoiceHostPortResult.failed(normalized)
        event, canonical, digest = normalized
        normalized_projections = self._normalize_projection_records(
            event_id=event.event_id,
            projection_records=projection_records,
        )
        if isinstance(normalized_projections, str):
            return VoiceHostPortResult.failed(normalized_projections)
        projection_rows, projection_set_digest = normalized_projections
        with self._guard:
            try:
                connection = self._open_database(create=True)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    record_count = self._validated_head(connection)
                    self._validated_projection_outbox(connection)
                    self._validated_command_receipts(connection)
                    existing = connection.execute(
                        """
                        SELECT event_sha256, projection_count, projection_set_sha256
                        FROM voice_events
                        WHERE event_id = ?
                        """,
                        (event.event_id,),
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != digest:
                            connection.rollback()
                            return VoiceHostPortResult.failed("voice_journal_event_conflict")
                        if int(existing[1]) != len(projection_rows) or str(existing[2]) != projection_set_digest:
                            connection.rollback()
                            return VoiceHostPortResult.failed("voice_journal_transition_conflict")
                        connection.commit()
                        return VoiceHostPortResult(status="duplicate")
                    ordinal = record_count + 1
                    connection.execute(
                        """
                        INSERT INTO voice_events (
                            ordinal,
                            event_id,
                            event_sha256,
                            event_json,
                            projection_count,
                            projection_set_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ordinal,
                            event.event_id,
                            digest,
                            canonical,
                            len(projection_rows),
                            projection_set_digest,
                        ),
                    )
                    for projection_index, projection_id, projection_digest, projection_json in projection_rows:
                        connection.execute(
                            """
                            INSERT INTO voice_projection_outbox (
                                projection_id,
                                event_ordinal,
                                projection_index,
                                projection_sha256,
                                projection_json,
                                delivery_status
                            ) VALUES (?, ?, ?, ?, ?, 'pending')
                            """,
                            (
                                projection_id,
                                ordinal,
                                projection_index,
                                projection_digest,
                                projection_json,
                            ),
                        )
                    connection.execute(
                        "UPDATE voice_journal_head SET record_count = ? WHERE singleton_id = 1",
                        (ordinal,),
                    )
                    connection.commit()
                    return VoiceHostPortResult.succeeded()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
            except _JournalStorageError as exc:
                return VoiceHostPortResult.failed(exc.reason)
            except (OSError, sqlite3.Error):
                return VoiceHostPortResult.failed("voice_journal_write_failed", retryable=True)

    def append(self, event_record: Mapping[str, Any]) -> VoiceHostPortResult:
        """Append a journal-only event for diagnostics and replay fixtures."""

        return self.append_transition(event_record, ())

    def load_pending_projections(self) -> VoiceProjectionOutboxLoadResult:
        with self._guard:
            if not _storage_path_is_owned(self._database_path):
                return VoiceProjectionOutboxLoadResult.failed("voice_journal_unreadable")
            if not self._database_path.exists():
                return VoiceProjectionOutboxLoadResult.succeeded()
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN")
                    self._validated_head(connection)
                    self._validated_projection_outbox(connection)
                    rows = connection.execute(
                        """
                        SELECT projection_json
                        FROM voice_projection_outbox
                        WHERE delivery_status = 'pending'
                        ORDER BY event_ordinal ASC, projection_index ASC
                        """
                    ).fetchall()
                finally:
                    connection.close()
                records: list[Mapping[str, Any]] = []
                for row in rows:
                    if len(row) != 1 or not isinstance(row[0], str):
                        raise ValueError("invalid projection outbox row")
                    record = json.loads(row[0])
                    if not isinstance(record, dict):
                        raise TypeError("projection outbox record must be an object")
                    records.append(record)
                return VoiceProjectionOutboxLoadResult.succeeded(tuple(records))
            except _JournalStorageError as exc:
                return VoiceProjectionOutboxLoadResult.failed(exc.reason)
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error):
                return VoiceProjectionOutboxLoadResult.failed("voice_projection_outbox_unreadable")

    def mark_projection_delivered(self, projection_id: str) -> VoiceHostPortResult:
        if not isinstance(projection_id, str) or not projection_id:
            return VoiceHostPortResult.failed("voice_projection_id_invalid")
        with self._guard:
            if not _storage_path_is_owned(self._database_path):
                return VoiceHostPortResult.failed("voice_journal_unreadable")
            if not self._database_path.exists():
                return VoiceHostPortResult.failed("voice_projection_outbox_missing")
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._validated_head(connection)
                    self._validated_projection_outbox(connection)
                    row = connection.execute(
                        """
                        SELECT delivery_status
                        FROM voice_projection_outbox
                        WHERE projection_id = ?
                        """,
                        (projection_id,),
                    ).fetchone()
                    if row is None:
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_projection_outbox_missing")
                    if str(row[0]) == "delivered":
                        connection.commit()
                        return VoiceHostPortResult(status="duplicate")
                    if str(row[0]) != "pending":
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_projection_outbox_unreadable")
                    connection.execute(
                        """
                        UPDATE voice_projection_outbox
                        SET delivery_status = 'delivered'
                        WHERE projection_id = ? AND delivery_status = 'pending'
                        """,
                        (projection_id,),
                    )
                    connection.commit()
                    return VoiceHostPortResult.succeeded()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
            except _JournalStorageError as exc:
                return VoiceHostPortResult.failed(exc.reason)
            except (OSError, sqlite3.Error):
                return VoiceHostPortResult.failed(
                    "voice_projection_acknowledgement_failed",
                    retryable=True,
                )

    def load_events(self) -> VoiceJournalLoadResult:
        with self._guard:
            if not _storage_path_is_owned(self._database_path):
                return VoiceJournalLoadResult(status="failed", reason="voice_journal_unreadable")
            if not self._database_path.exists():
                return VoiceJournalLoadResult(status="succeeded")
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN")
                    record_count = self._validated_head(connection)
                    self._validated_projection_outbox(connection)
                    self._validated_command_receipts(connection)
                    rows = connection.execute(
                        """
                        SELECT ordinal, event_id, event_sha256, event_json
                        FROM voice_events
                        ORDER BY ordinal ASC
                        """
                    ).fetchall()
                finally:
                    connection.close()
                if len(rows) != record_count:
                    return VoiceJournalLoadResult(status="failed", reason="voice_journal_record_missing")
                events = tuple(self._event_from_row(row, expected_ordinal=index) for index, row in enumerate(rows, 1))
                return VoiceJournalLoadResult(status="succeeded", events=events)
            except _JournalStorageError as exc:
                return VoiceJournalLoadResult(status="failed", reason=exc.reason)
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error):
                return VoiceJournalLoadResult(status="failed", reason="voice_journal_unreadable")

    def replay(self, initial_snapshot: VoiceRuntimeSnapshot) -> VoiceJournalReplayResult:
        if (
            initial_snapshot.conversation_id != self.conversation_id
            or initial_snapshot.conversation_generation != self.conversation_generation
        ):
            return VoiceJournalReplayResult(status="failed", reason="voice_journal_snapshot_identity_mismatch")
        loaded = self.load_events()
        if not loaded.ok:
            return VoiceJournalReplayResult(status="failed", reason=loaded.reason)
        replay = replay_events(initial_snapshot, loaded.events)
        if not replay.fully_accepted:
            return VoiceJournalReplayResult(status="failed", reason="voice_journal_replay_rejected", replay=replay)
        return VoiceJournalReplayResult(status="succeeded", replay=replay)

    def _event_from_row(self, row: tuple[Any, ...], *, expected_ordinal: int) -> VoiceEvent:
        if len(row) != 4:
            raise ValueError("invalid journal row")
        ordinal, event_id, expected_digest, canonical = row
        if ordinal != expected_ordinal or not isinstance(canonical, str):
            raise ValueError("invalid journal ordinal")
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        if digest != expected_digest:
            raise ValueError("journal event digest mismatch")
        event_record = json.loads(canonical)
        if not isinstance(event_record, dict):
            raise TypeError("journal event must be an object")
        event = voice_event_from_dict(event_record)
        if (
            event.event_id != event_id
            or event.conversation_id != self.conversation_id
            or event.conversation_generation != self.conversation_generation
        ):
            raise ValueError("journal event identity mismatch")
        return event

    @staticmethod
    def _normalize_projection_records(
        *,
        event_id: str,
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> tuple[tuple[tuple[int, str, str, str], ...], str] | str:
        if not isinstance(projection_records, tuple):
            return "voice_projection_records_invalid"
        rows: list[tuple[int, str, str, str]] = []
        projection_ids: set[str] = set()
        canonical_records: list[str] = []
        for index, raw_record in enumerate(projection_records):
            if not isinstance(raw_record, Mapping) or set(raw_record) != _PROJECTION_RECORD_FIELDS:
                return "voice_projection_record_invalid"
            projection_id = raw_record.get("projection_id")
            target = raw_record.get("target")
            kind = raw_record.get("kind")
            source_event_id = raw_record.get("source_event_id")
            payload = raw_record.get("payload")
            if (
                not isinstance(projection_id, str)
                or not projection_id
                or projection_id in projection_ids
                or not isinstance(target, str)
                or target not in _PROJECTION_TARGETS
                or not isinstance(kind, str)
                or not kind
                or source_event_id != event_id
                or not isinstance(payload, Mapping)
            ):
                return "voice_projection_record_invalid"
            record = {
                "projection_id": projection_id,
                "target": target,
                "kind": kind,
                "source_event_id": source_event_id,
                "payload": dict(payload),
            }
            try:
                canonical = _canonical_json(record)
            except (TypeError, ValueError):
                return "voice_projection_record_invalid"
            projection_ids.add(projection_id)
            canonical_records.append(canonical)
            rows.append(
                (
                    index,
                    projection_id,
                    sha256(canonical.encode("utf-8")).hexdigest(),
                    canonical,
                )
            )
        return tuple(rows), _record_set_digest(canonical_records)

    def _normalize_event_record(
        self,
        event_record: Mapping[str, Any],
    ) -> tuple[VoiceEvent, str, str] | str:
        if not isinstance(event_record, Mapping):
            return "voice_journal_event_invalid"
        try:
            normalized = dict(event_record)
            canonical = _canonical_json(normalized)
            event = voice_event_from_dict(normalized)
        except (TypeError, ValueError):
            return "voice_journal_event_invalid"
        if (
            event.conversation_id != self.conversation_id
            or event.conversation_generation != self.conversation_generation
        ):
            return "voice_journal_event_identity_mismatch"
        return event, canonical, sha256(canonical.encode("utf-8")).hexdigest()
