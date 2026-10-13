from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from companion_v01.voice_runtime.durable_ports import (
    FileVoiceAudioArtifactPort,
    FileVoiceTextArtifactPort,
    SqliteVoiceRuntimeJournal,
)
from voicecore import VoiceCommand, voice_command_to_dict, voice_event_to_dict
from voicecore.testing import EventFactory


def canonical(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def directory_alias(case, link, target):
    # Both sides and cleanup stay within this test's own temporary directory.
    link.relative_to(case.root)
    target.resolve().relative_to(case.root.resolve())
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            case.skipTest("directory_aliases_unavailable")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False)
        if result.returncode:
            case.skipTest("directory_aliases_unavailable")
    case.addCleanup(os.rmdir if os.name == "nt" else Path.unlink, link)
    case.assertNotEqual(link.resolve(), link)


class VoiceJournalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.factory = EventFactory()
        self.journal = self.make_journal()
        self.event = voice_event_to_dict(self.factory.make("voice.input.activity_started", voice_turn_id="turn-1"))
        self.projection = {
            "projection_id": "projection-1",
            "target": "runtime",
            "kind": "accepted",
            "source_event_id": self.event["event_id"],
            "payload": {},
        }
        self.assertTrue(self.journal.append_transition(self.event, (self.projection,)).ok)
        self.database = next(self.root.rglob("journal.sqlite3"))
        self.command = VoiceCommand("command-1", "start_thinking_agent", "key-1", self.event["event_id"])

    def make_journal(self, root=None):
        return SqliteVoiceRuntimeJournal(
            state_dir=root or self.root, conversation_id="conversation-1", conversation_generation=1
        )

    def edit(self, sql, parameters=()):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(sql, parameters)

    def tables(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def begin(self):
        self.assertTrue(self.journal.begin_command(voice_command_to_dict(self.command)).ok)

    def observation(self, **changes):
        return voice_event_to_dict(
            self.factory.make(
                "voice.response.created",
                voice_turn_id="turn-1",
                response_id="response-1",
                response_generation=1,
                payload={"command_id": self.command.command_id, "purpose": "content", "commitment": "committed"},
                **changes,
            )
        )

    def test_missing_receipt_table_is_not_recreated_as_an_empty_recovery(self):
        self.begin()
        self.edit("DROP TABLE voice_command_receipts")
        before = self.tables()
        result = self.journal.load_unfinished_commands()
        self.assertEqual(self.tables(), before)
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())

    def test_missing_outbox_table_is_not_recreated_during_a_read(self):
        self.edit("DROP TABLE voice_projection_outbox")
        before = self.tables()
        result = self.journal.load_pending_projections()
        self.assertEqual(self.tables(), before)
        self.assertFalse(result.ok)

    def test_missing_schema_metadata_is_not_silently_reset(self):
        self.edit("DELETE FROM voice_journal_meta")
        result = self.journal.load_events()
        self.assertFalse(result.ok)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM voice_journal_meta").fetchall(), [])

    def test_incompatible_schema_does_not_acquire_missing_tables(self):
        self.edit("UPDATE voice_journal_meta SET schema_version = 4")
        self.edit("DROP TABLE voice_command_receipts")
        before = self.tables()
        result = self.journal.load_events()
        self.assertEqual(self.tables(), before)
        self.assertFalse(result.ok)

    def test_existing_empty_database_is_not_claimed_as_valid_history(self):
        journal = self.make_journal(self.root / "empty")
        journal._database_path.parent.mkdir(parents=True)
        journal._database_path.touch()
        result = journal.load_events()
        self.assertFalse(result.ok)
        self.assertEqual(journal._database_path.read_bytes(), b"")

    def test_database_removed_before_open_is_not_recreated_by_a_read(self):
        connect = sqlite3.connect

        def removed_before_open(database, *args, **kwargs):
            self.database.unlink()
            return connect(database, *args, **kwargs)

        with patch.object(sqlite3, "connect", side_effect=removed_before_open):
            result = self.journal.load_events()
        self.assertFalse(result.ok)
        self.assertFalse(self.database.exists())

    def corrupt_event(self):
        self.edit("UPDATE voice_events SET event_json = '{broken' WHERE ordinal = 1")

    def test_corrupt_event_blocks_projection_delivery(self):
        self.corrupt_event()
        result = self.journal.load_pending_projections()
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())

    def test_corrupt_event_blocks_a_new_command_claim(self):
        self.corrupt_event()
        result = self.journal.begin_command(voice_command_to_dict(self.command))
        self.assertFalse(result.ok)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM voice_command_receipts").fetchone()[0], 0)

    def test_corrupt_event_blocks_further_journal_publication(self):
        self.corrupt_event()
        second = voice_event_to_dict(self.factory.make("voice.input.activity_started", voice_turn_id="turn-2"))
        result = self.journal.append(second)
        self.assertFalse(result.ok)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM voice_events").fetchone()[0], 1)

    def test_corrupt_projection_blocks_further_journal_publication(self):
        self.edit("UPDATE voice_projection_outbox SET projection_json = '{broken'")
        second = voice_event_to_dict(self.factory.make("voice.input.activity_started", voice_turn_id="turn-2"))
        self.assertFalse(self.journal.append(second).ok)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM voice_events").fetchone()[0], 1)

    def test_projection_shape_is_checked_even_with_matching_digests(self):
        encoded = canonical({**self.projection, "target": []})
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.edit("UPDATE voice_projection_outbox SET projection_json = ?, projection_sha256 = ?", (encoded, digest))
        self.edit("UPDATE voice_events SET projection_set_sha256 = ?", (digest,))
        result = self.journal.load_pending_projections()
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())

    def test_loading_events_uses_one_snapshot_while_another_journal_appends(self):
        second = voice_event_to_dict(self.factory.make("voice.input.activity_started", voice_turn_id="turn-2"))
        validate = self.journal._validated_projection_outbox
        writer = self.make_journal()

        def concurrent_write(connection):
            self.assertTrue(writer.append(second).ok)
            validate(connection)

        with patch.object(self.journal, "_validated_projection_outbox", side_effect=concurrent_write):
            result = self.journal.load_events()
        self.assertTrue(result.ok)
        self.assertEqual(result.events[0].event_id, self.event["event_id"])
        self.assertEqual(len(result.events), 1)
        self.assertEqual(len(writer.load_events().events), 2)

    def assert_foreign_observation_rejected(self, **changes):
        self.begin()
        result = self.journal.store_command_observations(self.command.command_id, (self.observation(**changes),))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_command_observation_identity_mismatch")
        loaded = self.journal.load_unfinished_commands()
        self.assertTrue(loaded.ok)
        self.assertEqual(loaded.records[0].phase, "executing")
        self.assertEqual(loaded.records[0].observation_records, ())

    def test_other_conversation_observations_do_not_poison_an_executing_receipt(self):
        self.assert_foreign_observation_rejected(conversation_id="other-conversation")

    def test_other_generation_observations_do_not_poison_an_executing_receipt(self):
        self.assert_foreign_observation_rejected(conversation_generation=2)

    def test_stored_foreign_observation_is_rejected_even_with_matching_digests(self):
        self.begin()
        observation = self.observation()
        self.assertTrue(self.journal.store_command_observations(self.command.command_id, (observation,)).ok)
        observation["conversation_id"] = "other-conversation"
        encoded = canonical([observation])
        digest = hashlib.sha256(canonical(observation).encode()).hexdigest()
        self.edit(
            "UPDATE voice_command_receipts SET observations_json = ?, observation_set_sha256 = ?", (encoded, digest)
        )
        result = self.journal.load_unfinished_commands()
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())

    def test_invalid_utf8_in_a_stored_observation_blocks_writes_without_raising(self):
        self.begin()
        observation = self.observation()
        self.assertTrue(self.journal.store_command_observations(self.command.command_id, (observation,)).ok)
        observation["payload"]["text"] = "\ud800"
        self.edit("UPDATE voice_command_receipts SET observations_json = ?", (json.dumps([observation]),))
        result = self.journal.mark_command_completed(self.command.command_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_command_receipt_unreadable")

    def test_command_receipt_with_missing_causation_event_is_rejected(self):
        self.begin()
        record = voice_command_to_dict(self.command)
        record["causation_id"] = "missing-event"
        encoded = canonical(record)
        self.edit(
            "UPDATE voice_command_receipts SET causation_event_id = ?, command_json = ?, command_sha256 = ?",
            ("missing-event", encoded, hashlib.sha256(encoded.encode()).hexdigest()),
        )
        result = self.journal.load_unfinished_commands()
        self.assertFalse(result.ok)
        self.assertEqual(result.records, ())

    def test_invalid_projection_shape_and_utf8_return_structured_failures(self):
        for changes in ({"target": []}, {"target": {}}, {"payload": {"text": "\ud800"}}):
            with self.subTest(field=next(iter(changes))):
                result = self.journal.append_transition(self.event, ({**self.projection, **changes},))
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "voice_projection_record_invalid")

    def test_invalid_utf8_in_event_and_command_returns_structured_failures(self):
        record = {**self.event, "payload": {"text": "\ud800"}}
        result = self.journal.append(record)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_journal_event_invalid")
        record = {**voice_command_to_dict(self.command), "idempotency_key": "\ud800"}
        result = self.journal.begin_command(record)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_command_record_invalid")

    def test_dangling_database_link_is_not_an_empty_recovery(self):
        journal = self.make_journal(self.root / "dangling")
        journal._database_path.parent.mkdir(parents=True)
        try:
            journal._database_path.symlink_to(self.root / "missing.sqlite3")
        except OSError:
            self.skipTest("symbolic_links_unavailable")
        for load in (journal.load_events, journal.load_pending_projections, journal.load_unfinished_commands):
            with self.subTest(port=load.__name__):
                self.assertFalse(load().ok)

    def test_namespace_alias_cannot_open_another_conversation_journal(self):
        journal = self.make_journal(self.root / "alias")
        other = SqliteVoiceRuntimeJournal(
            state_dir=self.root / "alias", conversation_id="other-conversation", conversation_generation=1
        )
        other._conversation_root.mkdir(parents=True)
        directory_alias(self, journal._conversation_root, other._conversation_root)
        with self.assertRaisesRegex(ValueError, "voice_runtime_storage_path_invalid"):
            self.make_journal(self.root / "alias")

    def test_directory_alias_added_after_creation_is_rejected_by_every_journal_port(self):
        journal = self.make_journal(self.root / "alias-after-creation")
        target = self.root / "alias-target"
        target.mkdir()
        journal._conversation_root.parent.mkdir(parents=True)
        directory_alias(self, journal._conversation_root, target)
        for operation in (
            journal.load_events,
            journal.load_pending_projections,
            journal.load_unfinished_commands,
            lambda: journal.append(self.event),
            lambda: journal.begin_command(voice_command_to_dict(self.command)),
            lambda: journal.mark_projection_delivered("projection-1"),
            lambda: journal.mark_command_completed("command-1"),
        ):
            with self.subTest(port=operation.__name__):
                self.assertFalse(operation().ok)
        self.assertEqual(list(target.iterdir()), [])

    def test_detected_dangling_database_link_is_rejected_before_missing_file_check(self):
        journal = self.make_journal(self.root / "detected-dangling")
        is_symlink = Path.is_symlink

        def detected(path):
            return path == journal._database_path or is_symlink(path)

        with patch.object(Path, "is_symlink", detected):
            for load in (journal.load_events, journal.load_pending_projections, journal.load_unfinished_commands):
                with self.subTest(port=load.__name__):
                    self.assertFalse(load().ok)

    def test_missing_database_still_has_an_empty_initial_state(self):
        journal = self.make_journal(self.root / "new")
        self.assertTrue(journal.load_events().ok)
        self.assertTrue(journal.load_pending_projections().ok)
        self.assertTrue(journal.load_unfinished_commands().ok)
        self.assertFalse(journal._database_path.exists())

    def test_valid_receipt_reopens_and_rejects_conflicting_results(self):
        self.begin()
        observation = self.observation()
        self.assertTrue(self.journal.store_command_observations(self.command.command_id, (observation,)).ok)
        reopened = self.make_journal()
        duplicate = reopened.store_command_observations(self.command.command_id, (observation,))
        self.assertEqual(duplicate.status, "duplicate")
        conflict = reopened.store_command_observations(
            self.command.command_id, ({**observation, "response_id": "other"},)
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.reason, "voice_command_observation_conflict")
        self.assertEqual(reopened.load_unfinished_commands().records[0].observation_records, (observation,))


class VoiceArtifactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def port(self, kind, *, root=None, conversation_id="conversation-1"):
        cls = FileVoiceTextArtifactPort if kind == "text" else FileVoiceAudioArtifactPort
        return cls(state_dir=root or self.root, conversation_id=conversation_id, conversation_generation=1)

    def put(self, port, kind, value, key="immutable-key"):
        if kind == "text":
            return port.put_text(artifact_key=key, text=value, media_type="text/plain")
        return port.put_audio(artifact_key=key, audio=value, media_type="audio/wav")

    def concurrent_writes(self, kind):
        for same_content in (False, True):
            with self.subTest(same_content=same_content):
                root = self.root / str(same_content)
                ports = [self.port(kind, root=root), self.port(kind, root=root)]
                values = ["first", "first" if same_content else "second"]
                if kind == "audio":
                    values = [value.encode() for value in values]
                target_name = hashlib.sha256(b"immutable-key").hexdigest() + (".json" if kind == "text" else ".bin")
                barrier = threading.Barrier(2)
                exists = Path.exists

                def race(path):
                    present = exists(path)
                    if path.name == target_name and not present:
                        barrier.wait(timeout=5)
                    return present

                with patch.object(Path, "exists", race), ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(self.put, port, kind, value) for port, value in zip(ports, values)]
                    results = [future.result(timeout=8) for future in futures]
                self.assertEqual(sum(result.status == "succeeded" for result in results), 1)
                if same_content:
                    self.assertEqual(sum(result.status == "duplicate" for result in results), 1)
                else:
                    failure = next(result for result in results if not result.ok)
                    self.assertEqual(failure.reason, f"voice_{kind}_artifact_conflict")
                winner = next(index for index, result in enumerate(results) if result.status == "succeeded")
                read = (
                    ports[0].read_text(results[winner].artifact_ref)
                    if kind == "text"
                    else ports[0].read_audio(results[winner].artifact_ref)
                )
                self.assertEqual(read.text if kind == "text" else read.audio, values[winner])
                self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_independent_text_ports_cannot_overwrite_each_other(self):
        self.concurrent_writes("text")

    def test_independent_audio_ports_cannot_overwrite_each_other(self):
        self.concurrent_writes("audio")

    def test_invalid_utf8_text_and_artifact_keys_return_structured_failures(self):
        for kind, value, key, reason in (
            ("text", "\ud800", "key", "voice_text_artifact_text_invalid"),
            ("text", "valid", "\ud800", "voice_text_artifact_key_invalid"),
            ("audio", b"valid", "\ud800", "voice_audio_artifact_key_invalid"),
        ):
            with self.subTest(kind=kind, reason=reason):
                result = self.put(self.port(kind), kind, value, key)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, reason)

    def test_text_metadata_with_invalid_utf8_or_empty_media_type_is_unreadable(self):
        port = self.port("text")
        written = self.put(port, "text", "valid")
        path = next(self.root.rglob("text_artifacts/*.json"))
        original = json.loads(path.read_text(encoding="utf-8"))
        for changes in ({"text": "\ud800"}, {"media_type": ""}, {"media_type": "\ud800"}):
            with self.subTest(field=next(iter(changes))):
                path.write_text(json.dumps({**original, **changes}), encoding="utf-8")
                self.assertFalse(port.read_text(written.artifact_ref).ok)

    def test_audio_byte_length_requires_an_integer(self):
        port = self.port("audio")
        written = self.put(port, "audio", b"a")
        path = next(self.root.rglob("audio_artifacts/*.bin"))
        envelope = path.read_bytes()
        from companion_v01.voice_runtime.durable_ports import _AUDIO_ARTIFACT_MAGIC

        start = len(_AUDIO_ARTIFACT_MAGIC) + 4
        end = start + int.from_bytes(envelope[start - 4 : start], "big")
        metadata = json.loads(envelope[start:end])
        for length in (True, 1.0):
            with self.subTest(length_type=type(length).__name__):
                encoded = canonical({**metadata, "byte_length": length}).encode()
                path.write_bytes(_AUDIO_ARTIFACT_MAGIC + len(encoded).to_bytes(4, "big") + encoded + envelope[end:])
                self.assertFalse(port.read_audio(written.artifact_ref).ok)

    def test_artifact_directory_alias_cannot_write_outside_its_namespace(self):
        for kind in ("text", "audio"):
            with self.subTest(kind=kind):
                port = self.port(kind, root=self.root / kind)
                outside = self.root / (kind + "-outside")
                outside.mkdir()
                port._artifact_dir.parent.mkdir(parents=True)
                directory_alias(self, port._artifact_dir, outside)
                result = self.put(port, kind, "private" if kind == "text" else b"private")
                self.assertFalse(result.ok)
                self.assertEqual(list(outside.iterdir()), [])

    def test_artifact_directory_alias_cannot_read_another_conversation(self):
        for kind in ("text", "audio"):
            with self.subTest(kind=kind):
                owner = self.port(kind, root=self.root / kind, conversation_id="other-conversation")
                written = self.put(owner, kind, "private" if kind == "text" else b"private")
                port = self.port(kind, root=self.root / kind)
                port._artifact_dir.parent.mkdir(parents=True)
                directory_alias(self, port._artifact_dir, owner._artifact_dir)
                result = (
                    port.read_text(written.artifact_ref) if kind == "text" else port.read_audio(written.artifact_ref)
                )
                self.assertFalse(result.ok)

    def test_failed_atomic_write_leaves_no_candidate_and_can_retry(self):
        for kind in ("text", "audio"):
            with self.subTest(kind=kind):
                port = self.port(kind)
                value = "valid" if kind == "text" else b"valid"
                with patch.object(os, "fsync", side_effect=OSError("fixture_sync_failed")):
                    result = self.put(port, kind, value)
                self.assertFalse(result.ok)
                self.assertTrue(result.retryable)
                self.assertEqual(list(self.root.rglob("*.tmp")), [])
                self.assertTrue(self.put(port, kind, value).ok)
