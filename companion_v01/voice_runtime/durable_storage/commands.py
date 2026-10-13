"""Command claims, durable observations and terminal receipt transitions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from voicecore import (
    VoiceCommand,
    VoiceEvent,
    voice_command_from_dict,
    voice_command_to_dict,
    voice_event_from_dict,
    voice_event_to_dict,
)
from ..host import VoiceCommandReceiptLoadResult, VoiceCommandReceiptRecord, VoiceHostPortResult
from .contracts import _JournalStorageError
from .storage import _canonical_json, _canonical_json_value, _record_set_digest, _storage_path_is_owned


class JournalCommands:
    __slots__ = ()

    def begin_command(self, command_record: Mapping[str, Any]) -> VoiceHostPortResult:
        normalized = self._normalize_command_record(command_record)
        if isinstance(normalized, str):
            return VoiceHostPortResult.failed(normalized)
        command, canonical, digest = normalized
        with self._guard:
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._validated_head(connection)
                    self._validated_command_receipts(connection)
                    existing = connection.execute(
                        """
                        SELECT command_sha256, phase
                        FROM voice_command_receipts
                        WHERE command_id = ?
                        """,
                        (command.command_id,),
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != digest:
                            connection.rollback()
                            return VoiceHostPortResult.failed("voice_command_receipt_conflict")
                        phase = str(existing[1])
                        if phase == "executing":
                            connection.commit()
                            return VoiceHostPortResult(status="duplicate")
                        if phase == "released":
                            connection.execute(
                                """
                                UPDATE voice_command_receipts
                                SET phase = 'executing'
                                WHERE command_id = ? AND phase = 'released'
                                """,
                                (command.command_id,),
                            )
                            connection.commit()
                            return VoiceHostPortResult.succeeded()
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_receipt_already_has_result")
                    connection.execute(
                        """
                        INSERT INTO voice_command_receipts (
                            command_id,
                            idempotency_key,
                            causation_event_id,
                            command_sha256,
                            command_json,
                            observation_count,
                            observation_set_sha256,
                            observations_json,
                            phase
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, '[]', 'executing')
                        """,
                        (
                            command.command_id,
                            command.idempotency_key,
                            command.causation_id,
                            digest,
                            canonical,
                            _record_set_digest([]),
                        ),
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
                    "voice_command_receipt_write_failed",
                    retryable=True,
                )

    def store_command_observations(
        self,
        command_id: str,
        observation_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        normalized = self._normalize_command_observations(
            command_id=command_id,
            observation_records=observation_records,
        )
        if isinstance(normalized, str):
            return VoiceHostPortResult.failed(normalized)
        canonical_observations, observation_count, observation_set_digest = normalized
        if any(
            record["conversation_id"] != self.conversation_id
            or record["conversation_generation"] != self.conversation_generation
            for record in json.loads(canonical_observations)
        ):
            return VoiceHostPortResult.failed("voice_command_observation_identity_mismatch")
        with self._guard:
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._validated_head(connection)
                    self._validated_command_receipts(connection)
                    existing = connection.execute(
                        """
                        SELECT
                            phase,
                            observation_count,
                            observation_set_sha256,
                            observations_json
                        FROM voice_command_receipts
                        WHERE command_id = ?
                        """,
                        (command_id,),
                    ).fetchone()
                    if existing is None:
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_receipt_missing")
                    phase = str(existing[0])
                    if phase in {"observations_pending", "completed"}:
                        if (
                            int(existing[1]) == observation_count
                            and str(existing[2]) == observation_set_digest
                            and str(existing[3]) == canonical_observations
                        ):
                            connection.commit()
                            return VoiceHostPortResult(status="duplicate")
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_observation_conflict")
                    if phase != "executing":
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_receipt_phase_conflict")
                    connection.execute(
                        """
                        UPDATE voice_command_receipts
                        SET
                            observation_count = ?,
                            observation_set_sha256 = ?,
                            observations_json = ?,
                            phase = 'observations_pending'
                        WHERE command_id = ? AND phase = 'executing'
                        """,
                        (
                            observation_count,
                            observation_set_digest,
                            canonical_observations,
                            command_id,
                        ),
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
                    "voice_command_observation_store_failed",
                    retryable=True,
                )

    def release_command(self, command_id: str) -> VoiceHostPortResult:
        return self._transition_command_receipt(
            command_id=command_id,
            expected_phase="executing",
            next_phase="released",
            duplicate_phase="released",
            failure_reason="voice_command_receipt_release_failed",
        )

    def load_unfinished_commands(self) -> VoiceCommandReceiptLoadResult:
        with self._guard:
            if not _storage_path_is_owned(self._database_path):
                return VoiceCommandReceiptLoadResult.failed("voice_journal_unreadable")
            if not self._database_path.exists():
                return VoiceCommandReceiptLoadResult.succeeded()
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN")
                    self._validated_head(connection)
                    self._validated_command_receipts(connection)
                    rows = connection.execute(
                        """
                        SELECT phase, command_json, observations_json
                        FROM voice_command_receipts
                        WHERE phase IN ('executing', 'observations_pending')
                        ORDER BY receipt_ordinal ASC
                        """
                    ).fetchall()
                finally:
                    connection.close()
                records: list[VoiceCommandReceiptRecord] = []
                for phase, command_json, observations_json in rows:
                    command_record = json.loads(str(command_json))
                    observation_records = json.loads(str(observations_json))
                    if not isinstance(command_record, dict) or not isinstance(observation_records, list):
                        raise TypeError("invalid command receipt payload")
                    if any(not isinstance(record, dict) for record in observation_records):
                        raise TypeError("invalid command observation payload")
                    records.append(
                        VoiceCommandReceiptRecord(
                            phase=str(phase),
                            command_record=command_record,
                            observation_records=tuple(observation_records),
                        )
                    )
                return VoiceCommandReceiptLoadResult.succeeded(tuple(records))
            except _JournalStorageError as exc:
                return VoiceCommandReceiptLoadResult.failed(exc.reason)
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                sqlite3.Error,
            ):
                return VoiceCommandReceiptLoadResult.failed("voice_command_receipt_unreadable")

    def mark_command_completed(self, command_id: str) -> VoiceHostPortResult:
        return self._transition_command_receipt(
            command_id=command_id,
            expected_phase="observations_pending",
            next_phase="completed",
            duplicate_phase="completed",
            failure_reason="voice_command_receipt_completion_failed",
        )

    def _transition_command_receipt(
        self,
        *,
        command_id: str,
        expected_phase: str,
        next_phase: str,
        duplicate_phase: str,
        failure_reason: str,
    ) -> VoiceHostPortResult:
        if not isinstance(command_id, str) or not command_id:
            return VoiceHostPortResult.failed("voice_command_id_invalid")
        with self._guard:
            if not _storage_path_is_owned(self._database_path):
                return VoiceHostPortResult.failed("voice_journal_unreadable")
            if not self._database_path.exists():
                return VoiceHostPortResult.failed("voice_command_receipt_missing")
            try:
                connection = self._open_database(create=False)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    self._validated_head(connection)
                    self._validated_command_receipts(connection)
                    row = connection.execute(
                        """
                        SELECT phase
                        FROM voice_command_receipts
                        WHERE command_id = ?
                        """,
                        (command_id,),
                    ).fetchone()
                    if row is None:
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_receipt_missing")
                    phase = str(row[0])
                    if phase == duplicate_phase:
                        connection.commit()
                        return VoiceHostPortResult(status="duplicate")
                    if phase != expected_phase:
                        connection.rollback()
                        return VoiceHostPortResult.failed("voice_command_receipt_phase_conflict")
                    connection.execute(
                        """
                        UPDATE voice_command_receipts
                        SET phase = ?
                        WHERE command_id = ? AND phase = ?
                        """,
                        (next_phase, command_id, expected_phase),
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
                return VoiceHostPortResult.failed(failure_reason, retryable=True)

    @staticmethod
    def _normalize_command_record(
        command_record: Mapping[str, Any],
    ) -> tuple[VoiceCommand, str, str] | str:
        if not isinstance(command_record, Mapping):
            return "voice_command_record_invalid"
        try:
            command = voice_command_from_dict(dict(command_record))
            normalized = voice_command_to_dict(command)
            canonical = _canonical_json(normalized)
        except (TypeError, ValueError):
            return "voice_command_record_invalid"
        if (
            not command.command_id
            or not command.command_kind
            or not command.idempotency_key
            or not command.causation_id
        ):
            return "voice_command_record_invalid"
        return command, canonical, sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_command_observations(
        *,
        command_id: str,
        observation_records: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, int, str] | str:
        if (
            not isinstance(command_id, str)
            or not command_id
            or not isinstance(observation_records, tuple)
            or not observation_records
        ):
            return "voice_command_observations_invalid"
        observations: list[VoiceEvent] = []
        normalized_records: list[dict[str, Any]] = []
        canonical_records: list[str] = []
        try:
            for record in observation_records:
                if not isinstance(record, Mapping):
                    return "voice_command_observations_invalid"
                observation = voice_event_from_dict(dict(record))
                normalized = voice_event_to_dict(observation)
                observations.append(observation)
                normalized_records.append(normalized)
                canonical_records.append(_canonical_json(normalized))
        except (TypeError, ValueError):
            return "voice_command_observations_invalid"
        completion_indexes = [
            index
            for index, observation in enumerate(observations)
            if observation.payload.get("command_id") == command_id
        ]
        if completion_indexes != [len(observations) - 1]:
            return "voice_command_completion_observation_invalid"
        return (
            _canonical_json_value(normalized_records),
            len(normalized_records),
            _record_set_digest(canonical_records),
        )
