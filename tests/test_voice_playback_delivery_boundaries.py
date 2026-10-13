from __future__ import annotations

import asyncio
import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.voice_runtime.durable_ports import FileVoiceAudioArtifactPort, FileVoiceTextArtifactPort
from companion_v01.voice_runtime.host import VoiceHostPortResult
from companion_v01.voice_runtime.playback_delivery import (
    AkaneVoicePlaybackCommandExecutor,
    VoicePlaybackDeliveryChannel,
)


class PlaybackBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        options = dict(state_dir=Path(temp.name), conversation_id="conversation-1", conversation_generation=1)
        texts = FileVoiceTextArtifactPort(**options)
        audios = FileVoiceAudioArtifactPort(**options)
        text_ref = texts.put_text(artifact_key="text", text="真实文本", media_type="text/plain").artifact_ref
        audio_ref = audios.put_audio(artifact_key="audio", audio=b"complete-audio", media_type="audio/wav").artifact_ref
        self.snapshot = {
            "conversation_id": "conversation-1",
            "conversation_generation": 1,
            "input_turns": {"turn-1": {"voice_session_id": "session-1"}},
            "responses": {
                "response-1": {"voice_turn_id": "turn-1", "source_turn_revision": 1, "response_generation": 1}
            },
            "speech_units": {
                "unit-1": {
                    "response_id": "response-1",
                    "response_generation": 1,
                    "ordinal": 0,
                    "text_artifact_ref": text_ref,
                    "audio_artifact_ref": audio_ref,
                }
            },
        }
        self.command = {
            "command_id": "command-1",
            "command_kind": "enqueue_playback",
            "idempotency_key": "audio-key",
            "payload": {
                "response_id": "response-1",
                "speech_unit_id": "unit-1",
                "response_generation": 1,
                "ordinal": 0,
                "audio_artifact_ref": audio_ref,
            },
        }
        self.events = []
        self.host = SimpleNamespace(
            snapshot=SimpleNamespace(
                responses={},
                input_turns={"turn-1": SimpleNamespace(voice_session_id="session-1")},
                speech_units={},
                pending_commands={},
            ),
            accept_event=self.accept,
        )
        self.channel = VoicePlaybackDeliveryChannel(
            host=self.host,
            voice_turn_id="turn-1",
            conversation_id="conversation-1",
            conversation_generation=1,
            text_artifacts=texts,
            audio_artifacts=audios,
        )
        self.executor = AkaneVoicePlaybackCommandExecutor(conversation_id="conversation-1", conversation_generation=1)
        self.executor.register_channel("turn-1", self.channel)

    def accept(self, event):
        self.events.append(event)
        return SimpleNamespace(accepted=True, status="accepted", reason="", retryable=False)

    def test_expression_survives_speech_artifact_to_real_playback_header(self):
        from companion_v01.voice_runtime.speech_artifact import encode_speech_artifact, MEDIA_TYPE
        from companion_v01.voice_runtime.realtime_events import speech_payload
        stored = self.channel.text_artifacts.put_text(
            artifact_key="expression",
            text=encode_speech_artifact({"display_text": "有些害羞。", "text": "少し恥ずかしいです。",
                                        "style": "calm", "emotion": "shy"}),
            media_type=MEDIA_TYPE,
        )
        self.snapshot["speech_units"]["unit-1"]["text_artifact_ref"] = stored.artifact_ref
        request = self.offered()
        self.assertEqual(request.emotion, "shy")
        header = speech_payload(request, protocol_version=2)
        self.assertEqual((header["text"], header["emotion"]), ("有些害羞。", "shy"))
        self.assertNotIn("style", header)
        self.assertTrue(self.channel.offer(self.command, self.snapshot).ok)

    def test_legacy_playback_header_does_not_gain_an_empty_expression_field(self):
        from companion_v01.voice_runtime.realtime_events import speech_payload
        self.assertNotIn("emotion", speech_payload(self.offered(), protocol_version=1))

    def offered(self):
        result = self.channel.offer(self.command, self.snapshot)
        self.assertEqual(result.status, "offered")
        return result.request

    def sent(self):
        request = self.offered()
        self.assertTrue(self.channel.mark_sent(request.delivery_id).ok)
        return request

    def started(self):
        request = self.sent()
        self.assertTrue(self.channel.acknowledge("client.playback.enqueued", {"delivery_id": request.delivery_id}).ok)
        self.assertTrue(
            self.channel.acknowledge(
                "client.playback.started", {"delivery_id": request.delivery_id, "resume_token": "token"}
            ).ok
        )
        return request

    def control(self, kind="duck_playback"):
        request = self.started()
        command = {
            "command_id": "control-command",
            "command_kind": kind,
            "idempotency_key": "control-key",
            "payload": {"speech_unit_id": "unit-1", "resume_token": "token"},
        }
        self.assertTrue(self.channel.offer_control(command, self.snapshot).ok)
        control = self.channel.take_control_outbound()
        self.assertTrue(self.channel.mark_control_sent(control.control_id).ok)
        return request, command, control

    def rejected(self, event):
        self.events.append(event)
        return SimpleNamespace(accepted=False, status="failed", reason="fixture_storage_unavailable", retryable=True)

    def test_failed_close_reports_the_cause_and_can_retry(self):
        request = self.offered()
        with patch.object(self.host, "accept_event", self.rejected):
            result = self.channel.close(reason="client_disconnected")
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual(result.reason, "fixture_storage_unavailable")
        self.assertTrue(self.channel.closed)
        retry = self.channel.close(reason="different_retry_reason")
        self.assertTrue(retry.ok)
        self.assertEqual(self.channel._records[request.delivery_id].state, "terminal")
        self.assertEqual(self.events[-1].payload["reason_code"], "client_disconnected")
        self.assertEqual(self.channel.close(reason="again").status, "duplicate")

    def test_failed_close_cannot_leave_audio_available_for_send(self):
        request = self.offered()
        with patch.object(self.host, "accept_event", self.rejected):
            self.channel.close(reason="client_disconnected")
        self.assertIsNone(self.channel.take_outbound())
        self.assertFalse(self.channel.mark_sent(request.delivery_id).ok)

    def test_failed_close_cannot_leave_control_available_for_send(self):
        _, command, _ = self.control()
        command = {**command, "command_id": "next-control", "idempotency_key": "next-control"}
        self.assertTrue(self.channel.offer_control(command, self.snapshot).ok)
        with patch.object(self.host, "accept_event", self.rejected):
            self.channel.close(reason="client_disconnected")
        self.assertIsNone(self.channel.take_control_outbound())
        control_id = self.channel._control_by_command_id["next-control"]
        self.assertFalse(self.channel.mark_control_sent(control_id).ok)

    def test_control_close_failure_is_reported_and_only_unfinished_work_is_retried(self):
        request, _, control = self.control()
        accepted_before = len(self.events)

        def fail_control(event):
            return (
                self.rejected(event)
                if event.event_kind == "voice.playback.control_acknowledged"
                else self.accept(event)
            )

        with patch.object(self.host, "accept_event", fail_control):
            result = self.channel.close(reason="client_disconnected")
        self.assertFalse(result.ok)
        self.assertEqual(self.channel._records[request.delivery_id].state, "terminal")
        self.assertEqual(self.channel._controls[control.control_id].state, "sent")
        self.assertTrue(self.channel.close(reason="retry").ok)
        events = self.events[accepted_before:]
        self.assertEqual(sum(event.event_kind == "voice.playback.interrupted" for event in events), 1)

    def test_host_exception_during_close_is_structured_and_retryable(self):
        self.offered()
        with patch.object(self.host, "accept_event", side_effect=OSError("fixture_storage_failed")):
            result = self.channel.close(reason="client_disconnected")
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertTrue(self.channel.close(reason="retry").ok)

    def test_closed_empty_channel_does_not_wait_forever(self):
        self.channel.close(reason="client_disconnected")

        async def wait():
            await asyncio.wait_for(self.channel.wait_activity(), timeout=0.1)

        asyncio.run(wait())

    def test_loop_closed_between_check_and_notification_is_harmless(self):
        class Loop:
            closed = False

            def is_closed(self):
                return self.closed

            def call_soon_threadsafe(self, callback):
                self.closed = True
                raise RuntimeError("fixture_loop_closed")

        self.channel._loop = Loop()
        self.channel._activity = SimpleNamespace(set=lambda: None)
        self.channel.notify_runtime_change()

    def test_non_mapping_receipts_return_structured_failures(self):
        for payload in (None, [], "invalid"):
            with self.subTest(kind=type(payload).__name__):
                result = self.channel.acknowledge("client.playback.enqueued", payload)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "voice_playback_ack_contract_invalid")

    def test_huge_control_volume_is_rejected_without_overflow(self):
        _, _, control = self.control()
        result = self.channel.acknowledge(
            "client.playback.control_ack",
            {
                "control_id": control.control_id,
                "command_id": control.command_id,
                "action": control.action,
                "status": "applied",
                "applied_volume": 10**1000,
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_playback_applied_volume_invalid")

    def test_invalid_control_receipt_releases_its_dispatch_admission(self):
        _, _, control = self.control()
        payload = {
            "control_id": control.control_id,
            "command_id": control.command_id,
            "action": control.action,
            "status": "applied",
            "applied_volume": 10**1000,
        }
        self.assertFalse(self.channel.acknowledge("client.playback.control_ack", payload).ok)
        self.assertTrue(self.channel.acknowledge("client.playback.control_ack", {**payload, "applied_volume": 0.25}).ok)

    def receipt_failure(self, *, raises):
        for stage in ("enqueued", "started", "completed", "control_ack"):
            with self.subTest(stage=stage):
                self.channel = VoicePlaybackDeliveryChannel(
                    host=self.host,
                    voice_turn_id="turn-1",
                    conversation_id="conversation-1",
                    conversation_generation=1,
                    text_artifacts=self.channel.text_artifacts,
                    audio_artifacts=self.channel.audio_artifacts,
                )
                if stage == "control_ack":
                    _, _, control = self.control()
                    payload = {
                        "control_id": control.control_id,
                        "command_id": control.command_id,
                        "action": control.action,
                        "status": "applied",
                    }
                    record = self.channel._controls[control.control_id]
                else:
                    request = self.started() if stage == "completed" else self.sent()
                    if stage == "started":
                        self.assertTrue(
                            self.channel.acknowledge(
                                "client.playback.enqueued", {"delivery_id": request.delivery_id}
                            ).ok
                        )
                    payload = {"delivery_id": request.delivery_id}
                    record = self.channel._records[request.delivery_id]
                previous = record.state
                if raises:
                    replacement = patch.object(self.host, "accept_event", side_effect=OSError("fixture_storage_failed"))
                else:
                    replacement = patch.object(self.host, "accept_event", self.rejected)
                with replacement:
                    result = self.channel.acknowledge("client.playback." + stage, payload)
                self.assertFalse(result.ok)
                self.assertTrue(result.retryable)
                self.assertEqual(record.state, previous)
                self.assertTrue(self.channel.acknowledge("client.playback." + stage, payload).ok)

    def test_rejected_receipts_preserve_state_and_retryability(self):
        self.receipt_failure(raises=False)

    def test_host_exceptions_during_receipts_are_structured_and_release_admission(self):
        self.receipt_failure(raises=True)

    def test_retryability_is_preserved_from_the_host_journal_result(self):
        request = self.sent()
        rejected = SimpleNamespace(
            accepted=False,
            status="failed",
            reason="voice_journal_write_failed",
            journal_result=VoiceHostPortResult.failed("voice_journal_write_failed", retryable=True),
        )
        with patch.object(self.host, "accept_event", return_value=rejected):
            result = self.channel.acknowledge("client.playback.enqueued", {"delivery_id": request.delivery_id})
        self.assertFalse(result.ok)
        self.assertTrue(result.retryable)
        self.assertEqual(self.channel._records[request.delivery_id].state, "sent")

    def test_host_observation_dispatch_does_not_hold_the_channel_state_lock(self):
        request = self.sent()

        def accept_outside_lock(event):
            def another_thread():
                acquired = self.channel._guard.acquire(timeout=0.5)
                if acquired:
                    self.channel._guard.release()
                return acquired

            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(another_thread).result(timeout=1))
            return self.accept(event)

        with patch.object(self.host, "accept_event", accept_outside_lock):
            self.assertTrue(
                self.channel.acknowledge("client.playback.enqueued", {"delivery_id": request.delivery_id}).ok
            )
            self.assertTrue(self.channel.close(reason="client_disconnected").ok)

    def test_stop_control_and_audio_receipt_share_the_target_admission(self):
        request, _, control = self.control(kind="stop_playback")
        payload = {
            "control_id": control.control_id,
            "command_id": control.command_id,
            "action": control.action,
            "status": "applied",
        }
        entered, release = threading.Event(), threading.Event()

        def slow_accept(event):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture_timeout")
            return self.accept(event)

        with ThreadPoolExecutor(max_workers=1) as executor, patch.object(self.host, "accept_event", slow_accept):
            future = executor.submit(self.channel.acknowledge, "client.playback.control_ack", payload)
            try:
                self.assertTrue(entered.wait(2))
                result = self.channel.acknowledge("client.playback.completed", {"delivery_id": request.delivery_id})
                self.assertFalse(result.ok)
                self.assertTrue(result.retryable)
            finally:
                release.set()
            self.assertTrue(future.result(timeout=3).ok)
        self.assertEqual(self.channel._records[request.delivery_id].terminal_kind, "interrupted")

    def test_invalid_utf8_idempotency_keys_do_not_raise(self):
        result = self.channel.offer({**self.command, "idempotency_key": "\ud800"}, self.snapshot)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "voice_playback_command_invalid")

    def test_delivery_identity_collisions_do_not_replace_an_existing_record(self):
        first = self.offered()
        result = self.channel.offer({**self.command, "command_id": "other-command"}, self.snapshot)
        self.assertFalse(result.ok)
        self.assertEqual(self.channel._records[first.delivery_id].request.command_id, "command-1")

    def test_control_identity_collisions_do_not_replace_an_existing_record(self):
        _, command, first = self.control()
        result = self.channel.offer_control({**command, "command_id": "other-command"}, self.snapshot)
        self.assertFalse(result.ok)
        self.assertEqual(self.channel._controls[first.control_id].request.command_id, "control-command")

    def test_foreign_snapshots_do_not_offer_audio_or_route_commands(self):
        for changes in (
            {"conversation_id": "other"},
            {"conversation_generation": 2},
            {"conversation_generation": True},
        ):
            with self.subTest(field=next(iter(changes))):
                snapshot = {**self.snapshot, **changes}
                self.assertFalse(self.channel.offer(self.command, snapshot).ok)
                self.assertEqual(self.executor.execute(self.command, snapshot).status, "failed")
        self.assertEqual(self.channel._records, {})

    def test_unit_context_mismatches_are_rejected_before_artifact_delivery(self):
        for changes in (
            {"response_id": "other"},
            {"response_generation": 2},
            {"ordinal": 3},
            {"audio_artifact_ref": "other"},
        ):
            with self.subTest(field=next(iter(changes))):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["speech_units"]["unit-1"].update(changes)
                self.assertFalse(self.channel.offer(self.command, snapshot).ok)
        self.assertEqual(self.channel._records, {})

    def test_malformed_executor_context_is_structured_even_without_a_channel(self):
        self.executor.unregister_channel("turn-1", self.channel)
        for changes in ({"source_turn_revision": None}, {"response_generation": "bad"}):
            with self.subTest(field=next(iter(changes))):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["responses"]["response-1"].update(changes)
                self.assertEqual(self.executor.execute(self.command, snapshot).status, "failed")

    def test_channel_registered_for_a_different_owner_is_not_used(self):
        self.executor.conversation_generation = 2
        snapshot = {**self.snapshot, "conversation_generation": 2}
        self.assertEqual(self.executor.execute(self.command, snapshot).status, "failed")
        self.assertEqual(self.channel._records, {})

    def receipt_race(self, stage):
        if stage == "control_ack":
            _, _, control = self.control()
            payload = {
                "control_id": control.control_id,
                "command_id": control.command_id,
                "action": control.action,
                "status": "applied",
                "applied_volume": 0.25,
            }
        else:
            request = self.started() if stage == "completed" else self.sent()
            if stage == "started":
                self.assertTrue(
                    self.channel.acknowledge("client.playback.enqueued", {"delivery_id": request.delivery_id}).ok
                )
            payload = {"delivery_id": request.delivery_id, "played_ms": 100, "resume_token": "original"}
        entered, release = threading.Event(), threading.Event()
        calls = []

        def slow_accept(event):
            calls.append(event)
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("fixture_timeout")
            return self.accept(event)

        with ThreadPoolExecutor(max_workers=1) as executor, patch.object(self.host, "accept_event", slow_accept):
            future = executor.submit(self.channel.acknowledge, "client.playback." + stage, payload)
            try:
                self.assertTrue(entered.wait(2))
                duplicate = self.channel.acknowledge("client.playback." + stage, payload)
                self.assertFalse(duplicate.ok)
                self.assertTrue(duplicate.retryable)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            self.assertTrue(future.result(timeout=3).ok)
        self.assertEqual(self.channel.acknowledge("client.playback." + stage, payload).status, "duplicate")

    def test_enqueue_receipts_have_one_in_flight_dispatch(self):
        self.receipt_race("enqueued")

    def test_start_receipts_have_one_in_flight_dispatch(self):
        self.receipt_race("started")

    def test_terminal_receipts_have_one_in_flight_dispatch(self):
        self.receipt_race("completed")

    def test_control_receipts_have_one_in_flight_dispatch(self):
        self.receipt_race("control_ack")

    def test_close_during_a_receipt_settles_after_that_receipt_without_regressing_state(self):
        request = self.sent()
        invoked = False

        def close_during_accept(event):
            nonlocal invoked
            self.accept(event)
            if not invoked:
                invoked = True
                closing = self.channel.close(reason="client_disconnected")
                self.assertFalse(closing.ok)
                self.assertTrue(closing.retryable)
            return SimpleNamespace(accepted=True, status="accepted", reason="")

        with patch.object(self.host, "accept_event", close_during_accept):
            self.assertTrue(
                self.channel.acknowledge("client.playback.enqueued", {"delivery_id": request.delivery_id}).ok
            )
        self.assertEqual(self.channel._records[request.delivery_id].state, "terminal")
        self.assertEqual(self.events[-1].event_kind, "voice.playback.interrupted")
        self.assertEqual(self.channel.close(reason="again").status, "duplicate")

    def test_restarted_executor_settles_an_orphan_without_replaying_audio(self):
        fresh = AkaneVoicePlaybackCommandExecutor(conversation_id="conversation-1", conversation_generation=1)
        recovered = fresh.recover(self.command, self.snapshot)
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(recovered.observations[0].payload["reason_code"], "voice_playback_runtime_restarted")
        self.assertEqual(self.channel._records, {})

    def test_minimal_legacy_snapshot_can_only_settle_an_orphan(self):
        snapshot = {
            key: value
            for key, value in self.snapshot.items()
            if key not in {"conversation_id", "conversation_generation"}
        }
        self.assertEqual(self.executor.execute(self.command, snapshot).status, "failed")
        orphan = AkaneVoicePlaybackCommandExecutor(conversation_id="conversation-1", conversation_generation=1)
        result = orphan.recover(self.command, snapshot)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.observations[0].event_kind, "voice.playback.failed")
        self.assertEqual(self.channel._records, {})

    def test_replacing_a_channel_keeps_the_new_registration_when_the_old_one_unregisters(self):
        replacement = SimpleNamespace(
            conversation_id="conversation-1", conversation_generation=1, voice_turn_id="turn-1"
        )
        self.executor.register_channel("turn-1", replacement)
        self.executor.unregister_channel("turn-1", self.channel)
        self.assertIs(self.executor._channels["turn-1"], replacement)

    def test_completed_receipt_is_duplicate_and_changed_receipt_is_a_conflict(self):
        request = self.started()
        payload = {"delivery_id": request.delivery_id, "played_ms": 100}
        self.assertTrue(self.channel.acknowledge("client.playback.completed", payload).ok)
        count = len(self.events)
        self.assertEqual(self.channel.acknowledge("client.playback.completed", payload).status, "duplicate")
        self.assertFalse(self.channel.acknowledge("client.playback.completed", {**payload, "played_ms": 101}).ok)
        self.assertEqual(len(self.events), count)
