from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from voicecore import (
    ReplayResult,
    VoiceEvent,
    VoiceRuntimeSnapshot,
    replay_events,
    voice_event_from_dict,
)

from .host import VoiceHostPortResult, VoiceProjectionOutboxLoadResult
from .stream_bridge import VoiceTextArtifactResult


_STORAGE_SCHEMA_VERSION = 2
_JOURNAL_DATABASE_FILENAME = "journal.sqlite3"
_ARTIFACT_REF_PATTERN = re.compile(r"^voice-text:(?P<digest>[0-9a-f]{64})$")
_PROJECTION_TARGETS = frozenset({"runtime", "host", "memcore", "model"})
_PROJECTION_RECORD_FIELDS = frozenset({"projection_id", "target", "kind", "source_event_id", "payload"})


class _JournalStorageError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class VoiceJournalLoadResult:
    status: str
    reason: str = ""
    events: tuple[VoiceEvent, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class VoiceJournalReplayResult:
    status: str
    reason: str = ""
    replay: ReplayResult | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and self.replay is not None


@dataclass(frozen=True)
class VoiceTextArtifactReadResult:
    status: str
    reason: str = ""
    text: str = ""
    media_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class SqliteVoiceRuntimeJournal:
    """Transactional VoiceEvent journal for one conversation generation."""

    def __init__(
        self,
        *,
        state_dir: Path,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        self.conversation_id = _required_identity(conversation_id, "conversation_id")
        self.conversation_generation = _required_generation(conversation_generation)
        self._conversation_root = _conversation_storage_root(
            state_dir=state_dir,
            conversation_id=self.conversation_id,
            conversation_generation=self.conversation_generation,
        )
        self._database_path = self._conversation_root / _JOURNAL_DATABASE_FILENAME
        self._guard = threading.RLock()

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
                        self._validated_projection_outbox(connection)
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
            if not self._database_path.exists():
                return VoiceProjectionOutboxLoadResult.succeeded()
            try:
                connection = self._open_database(create=False)
                try:
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
            if not self._database_path.exists():
                return VoiceJournalLoadResult(status="succeeded")
            try:
                connection = self._open_database(create=False)
                try:
                    record_count = self._validated_head(connection)
                    self._validated_projection_outbox(connection)
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

    def _open_database(self, *, create: bool) -> sqlite3.Connection:
        if self._database_path.is_symlink():
            raise _JournalStorageError("voice_journal_unreadable")
        if not create and not self._database_path.is_file():
            raise _JournalStorageError("voice_journal_unreadable")
        if create:
            try:
                self._conversation_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise _JournalStorageError("voice_journal_write_failed") from exc
        connection = sqlite3.connect(self._database_path, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            self._ensure_schema(connection)
            if os.name == "posix":
                os.chmod(self._database_path, 0o600)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
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
                    or sha256(canonical.encode("utf-8")).hexdigest() != expected_digest
                ):
                    raise _JournalStorageError("voice_projection_outbox_unreadable")
                try:
                    record = json.loads(canonical)
                except json.JSONDecodeError:
                    raise _JournalStorageError("voice_projection_outbox_unreadable") from None
                if (
                    not isinstance(record, dict)
                    or record.get("projection_id") != projection_id
                    or record.get("source_event_id") != event_id
                ):
                    raise _JournalStorageError("voice_projection_outbox_unreadable")
                canonical_records.append(canonical)
            if _record_set_digest(canonical_records) != expected_set_digest:
                raise _JournalStorageError("voice_projection_outbox_unreadable")
        if set(rows_by_event) - known_ordinals:
            raise _JournalStorageError("voice_projection_outbox_unreadable")

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


class FileVoiceTextArtifactPort:
    """Immutable, path-free text artifacts for VoiceCore speech units."""

    def __init__(
        self,
        *,
        state_dir: Path,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        conversation_root = _conversation_storage_root(
            state_dir=state_dir,
            conversation_id=_required_identity(conversation_id, "conversation_id"),
            conversation_generation=_required_generation(conversation_generation),
        )
        self._artifact_dir = conversation_root / "text_artifacts"
        self._guard = threading.RLock()

    def put_text(
        self,
        *,
        artifact_key: str,
        text: str,
        media_type: str,
    ) -> VoiceTextArtifactResult:
        if not isinstance(artifact_key, str) or not artifact_key:
            return VoiceTextArtifactResult.failed("voice_text_artifact_key_invalid")
        if not isinstance(text, str):
            return VoiceTextArtifactResult.failed("voice_text_artifact_text_invalid")
        if not isinstance(media_type, str) or not media_type:
            return VoiceTextArtifactResult.failed("voice_text_artifact_media_type_invalid")
        key_digest = sha256(artifact_key.encode("utf-8")).hexdigest()
        artifact_ref = f"voice-text:{key_digest}"
        content_digest = sha256(text.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": _STORAGE_SCHEMA_VERSION,
            "artifact_ref": artifact_ref,
            "artifact_key_sha256": key_digest,
            "content_sha256": content_digest,
            "media_type": media_type,
            "text": text,
        }
        path = self._artifact_dir / f"{key_digest}.json"
        with self._guard:
            if path.exists():
                if path.is_symlink():
                    return VoiceTextArtifactResult.failed("voice_text_artifact_unreadable")
                existing = self._read_record(path=path, expected_ref=artifact_ref)
                if not existing.ok:
                    return VoiceTextArtifactResult.failed(existing.reason)
                if existing.text == text and existing.media_type == media_type:
                    return VoiceTextArtifactResult.duplicate(artifact_ref)
                return VoiceTextArtifactResult.failed("voice_text_artifact_conflict")
            try:
                self._artifact_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(path, payload)
            except OSError:
                return VoiceTextArtifactResult.failed("voice_text_artifact_write_failed", retryable=True)
        return VoiceTextArtifactResult.succeeded(artifact_ref)

    def read_text(self, artifact_ref: str) -> VoiceTextArtifactReadResult:
        match = _ARTIFACT_REF_PATTERN.fullmatch(str(artifact_ref or ""))
        if match is None:
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_ref_invalid")
        path = self._artifact_dir / f"{match.group('digest')}.json"
        with self._guard:
            if not path.is_file():
                return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_missing")
            if path.is_symlink():
                return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
            return self._read_record(path=path, expected_ref=str(artifact_ref))

    @staticmethod
    def _read_record(*, path: Path, expected_ref: str) -> VoiceTextArtifactReadResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        required = {
            "schema_version",
            "artifact_ref",
            "artifact_key_sha256",
            "content_sha256",
            "media_type",
            "text",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        text = payload.get("text")
        media_type = payload.get("media_type")
        if (
            payload.get("schema_version") != _STORAGE_SCHEMA_VERSION
            or payload.get("artifact_ref") != expected_ref
            or not isinstance(text, str)
            or not isinstance(media_type, str)
            or sha256(text.encode("utf-8")).hexdigest() != payload.get("content_sha256")
        ):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        match = _ARTIFACT_REF_PATTERN.fullmatch(expected_ref)
        if match is None or payload.get("artifact_key_sha256") != match.group("digest"):
            return VoiceTextArtifactReadResult(status="failed", reason="voice_text_artifact_unreadable")
        return VoiceTextArtifactReadResult(
            status="succeeded",
            text=text,
            media_type=media_type,
        )


def _conversation_storage_root(
    *,
    state_dir: Path,
    conversation_id: str,
    conversation_generation: int,
) -> Path:
    if not isinstance(state_dir, Path):
        raise TypeError("voice_runtime_state_dir_must_be_path")
    try:
        resolved_state = state_dir.expanduser().resolve()
        runtime_root = (resolved_state / "voice_runtime").resolve()
        runtime_root.relative_to(resolved_state)
        namespace = sha256(f"{conversation_id}\0{conversation_generation}".encode("utf-8")).hexdigest()
        conversation_root = (runtime_root / "conversations" / namespace).resolve()
        conversation_root.relative_to(runtime_root)
    except (OSError, ValueError):
        raise ValueError("voice_runtime_storage_path_invalid") from None
    return conversation_root


def _required_identity(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"voice_runtime_{field_name}_invalid")
    return value


def _required_generation(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("voice_runtime_conversation_generation_invalid")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _record_set_digest(canonical_records: list[str]) -> str:
    return sha256("\n".join(canonical_records).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (_canonical_json(payload) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    file_descriptor = -1
    try:
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
