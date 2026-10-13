"""Production service failures, close ownership and late request boundaries."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
import tempfile
import threading
import unittest

from capcore_adapter_speech import ASRSessionMode, ASRSessionOpenResult, NormalizedASRSession

from companion_v01.voice_runtime.playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE, VoicePlaybackDeliveryResult
from companion_v01.voice_runtime.production import AkaneVoiceRuntimeService
from companion_v01.voice_runtime.realtime_protocol import VoiceRealtimeOpenRequest
from companion_v01.voice_runtime.thinking_agent import VoiceThinkingStartResult


def _request(**overrides):
    request = VoiceRealtimeOpenRequest(
        profile_user_id="owner", conversation_id="conversation", session_id="desktop",
        voice_turn_id="turn-1", audio_stream_id="audio-1", disposition="message",
        language="zh", character_pack_id="character", input_format="s16le",
        sample_rate=16000, channels=1,
    )
    return replace(request, **overrides)


class _RawSession:
    def __init__(self):
        self.cancel_count = 0

    async def commit_turn(self):
        return {"quality": "final", "stable_text": "test", "provider_receipt_id": "final-1"}

    async def finish_call(self):
        return None

    async def cancel(self):
        self.cancel_count += 1


class _Adapter:
    def __init__(self):
        self.raw = _RawSession()
        self.before_open = None

    async def open_session(self, **kwargs):
        if self.before_open is not None:
            await self.before_open()
        return ASRSessionOpenResult.succeeded(
            ASRSessionMode.STREAMING,
            NormalizedASRSession(provider_session=self.raw, mode=ASRSessionMode.STREAMING),
        )


class VoiceProductionBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = SimpleNamespace(enabled=True, available=True,
            record_voice_projection=lambda *args, **kwargs: {"ok": True},
            resolve_voice_projection_turn=lambda **kwargs: {"ok": False})
        self.adapter = _Adapter()
        self.service = AkaneVoiceRuntimeService(
            engine=SimpleNamespace(memcore_manager=self.manager), settings=SimpleNamespace(),
            state_dir=self.root / "voice", instance_id="instance", bot_id="bot",
            tts_client=SimpleNamespace(provider_id="test-tts"),
            provider_builder=lambda settings: SimpleNamespace(ready=True, adapter=self.adapter, provider_id="test-asr"),
        )
        self.addCleanup(self.service.close)
        self.assembly = sys.modules[self.service._resolve_host.__func__.__module__]

    def _resolve(self, **overrides):
        kwargs = dict(canonical_conversation_id="conversation-owned", manager=self.manager,
            profile_user_id="owner", session_id="desktop", character_pack_id="character", tts_client=None)
        kwargs.update(overrides)
        return self.service._resolve_host(**kwargs)

    def _track_executors(self, stack):
        created = []
        names = ("AkaneThinkingAgentCommandExecutor", "AkaneVoiceSemanticPulseCommandExecutor",
                 "AkaneVoiceCandidateValidationCommandExecutor", "AkaneVoiceTTSCommandExecutor")
        for name in names:
            factory = getattr(self.assembly, name)

            def construct(*args, _factory=factory, **kwargs):
                executor = _factory(*args, **kwargs)
                executor.close = Mock(wraps=executor.close)
                created.append(executor)
                return executor

            stack.enter_context(patch.object(self.assembly, name, side_effect=construct))
        return created

    def _assert_recovery_cleanup(self, target, method, value):
        with ExitStack() as stack:
            created = self._track_executors(stack)
            stack.enter_context(patch.object(target, method, return_value=value))
            failed = self._resolve()
        self.assertFalse(getattr(failed, "ready", False))
        self.assertEqual(failed.reason, "controlled_recovery_failure")
        self.assertFalse(self.service._hosts)
        self.assertEqual(len(created), 4)
        self.assertTrue(all(executor.close.call_count == 1 for executor in created))
        resolved = self._resolve()
        self.assertIs(resolved, self.service._hosts["conversation-owned"])

    @staticmethod
    def _failure():
        return SimpleNamespace(ok=False, quiescent=False, status="deferred",
            reason="controlled_recovery_failure", retryable=True, safe_public_summary="")

    def test_projection_recovery_failure_releases_all_constructed_executors(self):
        self._assert_recovery_cleanup(self.assembly.AkaneVoiceRuntimeHost, "drain_projection_outbox", self._failure())

    def test_command_receipt_recovery_failure_releases_all_constructed_executors(self):
        self._assert_recovery_cleanup(self.assembly.AkaneVoiceRuntimeHost, "drain_command_receipts", self._failure())

    def test_candidate_recovery_failure_releases_all_constructed_executors(self):
        factory = self.assembly.AkaneVoiceCandidateValidationCommandExecutor
        self._assert_recovery_cleanup(factory, "discard_restarted_candidates", self._failure())

    def test_pending_command_recovery_failure_releases_all_constructed_executors(self):
        self._assert_recovery_cleanup(self.service, "_recover_pending_commands", self._failure())

    def test_stale_generation_recovery_failure_releases_all_constructed_executors(self):
        factory = self.assembly.AkaneThinkingAgentCommandExecutor
        self._assert_recovery_cleanup(factory, "fail_restarted_generations", self._failure())

    def test_job_restoration_failure_releases_all_constructed_executors(self):
        factory = self.assembly.AkaneThinkingAgentCommandExecutor
        failed = VoiceThinkingStartResult(status="failed", reason="controlled_recovery_failure")
        self._assert_recovery_cleanup(factory, "restore_generating_jobs", failed)

    def test_late_constructor_exception_releases_already_constructed_executors(self):
        with ExitStack() as stack:
            created = self._track_executors(stack)
            stack.enter_context(patch.object(self.assembly, "AkaneVoiceRuntimeHost",
                side_effect=OSError("private path and token")))
            failed = self.service.create_coordinator(_request())
        self.assertEqual(failed.reason, "voice_runtime_host_initialization_failed")
        self.assertNotIn("private path", str(failed))
        self.assertFalse(self.service._hosts)
        self.assertEqual(len(created), 4)
        self.assertTrue(all(executor.close.call_count == 1 for executor in created))

    def test_failed_restored_start_is_not_cached_as_a_usable_host(self):
        factory = self.assembly.AkaneThinkingAgentCommandExecutor
        with ExitStack() as stack:
            created = self._track_executors(stack)
            stack.enter_context(patch.object(factory, "restore_generating_jobs",
                return_value=VoiceThinkingStartResult(status="started")))
            stack.enter_context(patch.object(factory, "start_ready_generations",
                return_value=VoiceThinkingStartResult(status="failed", reason="controlled_recovery_failure")))
            first = self._resolve()
            second = self._resolve()
        self.assertEqual(first.reason, "controlled_recovery_failure")
        self.assertEqual(getattr(second, "reason", ""), "controlled_recovery_failure")
        self.assertFalse(self.service._hosts)
        self.assertTrue(all(executor.close.call_count == 1 for executor in created))

    def test_failed_initialization_cleanup_is_retried_before_another_host_is_constructed(self):
        with ExitStack() as stack:
            created = self._track_executors(stack)
            factory = self.assembly.AkaneVoiceRuntimeHost

            def fail_host(**kwargs):
                executor = created[0]
                close = executor.close
                attempts = []

                def retry_close():
                    attempts.append(True)
                    if len(attempts) < 3:
                        raise OSError("private resource detail")
                    return close()

                executor.close = Mock(side_effect=retry_close)
                raise OSError("private storage detail")

            with patch.object(self.assembly, "AkaneVoiceRuntimeHost", side_effect=fail_host):
                with self.assertRaises(OSError):
                    self._resolve()
            pending = self._resolve()
            self.assertEqual(pending.reason, "voice_runtime_initialization_cleanup_pending")
            self.assertFalse(self.service._hosts)
            self.assertEqual(len(created), 4)
            with patch.object(self.assembly, "AkaneVoiceRuntimeHost", wraps=factory):
                ready = self._resolve()
            self.assertIs(ready, self.service._hosts["conversation-owned"])
            self.assertFalse(self.service._initializing_resources)

    def test_failed_initialization_cleanup_does_not_hold_the_service_state_lock(self):
        acquired = []
        with ExitStack() as stack:
            created = self._track_executors(stack)

            def failed_host(**kwargs):
                executor = created[0]
                close = executor.close

                def cleanup():
                    def take_lock():
                        locked = self.service._guard.acquire(timeout=1)
                        acquired.append(locked)
                        if locked:
                            self.service._guard.release()

                    thread = threading.Thread(target=take_lock)
                    thread.start()
                    thread.join(timeout=2)
                    return close()

                executor.close = cleanup
                raise OSError("controlled initialization failure")

            stack.enter_context(patch.object(self.assembly, "AkaneVoiceRuntimeHost", side_effect=failed_host))
            self.assertFalse(self.service.create_coordinator(_request()).ready)
        self.assertEqual(acquired, [True])

    def test_failed_restored_start_waits_for_old_work_before_a_retry(self):
        release = threading.Event()
        self.addCleanup(release.set)
        factory = self.assembly.AkaneThinkingAgentCommandExecutor

        def failed_start(executor, host):
            self.service._background_tasks.submit(lane=f"voice-thinking-{executor.conversation_id[-16:]}",
                name="controlled-restore", fn=release.wait, kwargs={"timeout": 4})
            return VoiceThinkingStartResult(status="failed", reason="controlled_recovery_failure")

        with patch.object(factory, "restore_generating_jobs", return_value=VoiceThinkingStartResult(status="started")), \
                patch.object(factory, "start_ready_generations", new=failed_start):
            self.assertEqual(self._resolve().reason, "controlled_recovery_failure")
            self.assertFalse(self.service._hosts)
            self.assertEqual(self._resolve().reason, "voice_runtime_initialization_cleanup_pending")
        release.set()
        self.assertTrue(self.service.wait_idle(timeout=2))
        self.assertIs(self._resolve(), self.service._hosts["conversation-owned"])

    def test_closed_service_cannot_construct_a_new_host(self):
        self.service.close()
        rejected = self._resolve()
        self.assertEqual(getattr(rejected, "reason", ""), "voice_runtime_service_closed")
        self.assertFalse(self.service._hosts)
        self.assertFalse(self.service.state_dir.exists())

    def test_close_during_provider_resolution_cannot_repopulate_the_service(self):
        def resolve(settings):
            self.service.close()
            return SimpleNamespace(ready=True, adapter=self.adapter)

        self.service.provider_builder = resolve
        failed = self.service.create_coordinator(_request())
        self.assertEqual(failed.reason, "voice_runtime_service_closed")
        self.assertFalse(self.service._hosts)
        self.assertFalse(self.service._delivery_channels)

    def test_a_late_turn_context_cannot_register_after_close(self):
        context = self.service._prepare_runtime_context(_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE))
        self.service.close()
        rejected = self.service._build_turn_coordinator(request=_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE),
            context=context, voice_session_id="session-1")
        self.assertEqual(rejected.reason, "voice_runtime_service_closed")
        self.assertFalse(self.service._delivery_channels)
        self.assertFalse(context.playback_executor._channels)

    def test_close_during_turn_registration_is_reported_as_service_unavailable(self):
        request = _request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE)
        context = self.service._prepare_runtime_context(request)
        register = context.thinking_executor.register_turn

        def close_then_register(**kwargs):
            self.service.close()
            register(**kwargs)

        with patch.object(context.thinking_executor, "register_turn", side_effect=close_then_register):
            rejected = self.service._build_turn_coordinator(request=request, context=context,
                voice_session_id="session-1")
        self.assertEqual(rejected.status, "unavailable")
        self.assertEqual(rejected.reason, "voice_runtime_service_closed")
        self.assertFalse(self.service._delivery_channels)

    def test_invalid_identity_encoding_is_rejected_before_creating_storage(self):
        rejected = self.service.create_coordinator(_request(conversation_id="invalid\ud800"))
        self.assertEqual(rejected.status, "invalid_request")
        self.assertEqual(rejected.reason, "voice_realtime_identity_invalid")
        self.assertFalse(self.service.state_dir.exists())

    def test_duplicate_active_turn_keeps_the_original_delivery_channel(self):
        request = _request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE)
        first = self.service.create_coordinator(request)
        self.assertTrue(first.ready)
        second = self.service.create_coordinator(request)
        self.assertEqual(second.status, "conflict")
        self.assertIs(next(iter(self.service._delivery_channels.values())), first.delivery_channel)
        self.assertFalse(first.delivery_channel.closed)

    def test_failed_turn_construction_releases_its_unused_delivery_channel(self):
        failed = self.service.create_coordinator(_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE,
            input_format="invalid-pcm"))
        self.assertEqual(failed.reason, "voice_realtime_coordinator_invalid")
        self.assertFalse(self.service._delivery_channels)
        retried = self.service.create_coordinator(_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE))
        self.assertTrue(retried.ready)

    def test_old_channel_terminal_notification_cannot_remove_a_replacement(self):
        request = _request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE)
        first = self.service.create_coordinator(request)
        self.assertTrue(first.ready)
        key = next(iter(self.service._delivery_channels))
        replacement = SimpleNamespace(close=Mock(return_value=VoicePlaybackDeliveryResult(status="accepted")))
        executor = self.service._playback_executors[key[0]]
        self.service._delivery_channels[key] = replacement
        executor.register_channel(key[1], replacement)
        first.delivery_channel.terminal_notifier(key[1])
        self.assertIs(self.service._delivery_channels.get(key), replacement)
        self.assertIs(executor._channels.get(key[1]), replacement)

    def test_concurrent_private_contexts_use_the_lease_owned_by_the_shared_host(self):
        self.service.engine.owner_private_policy = SimpleNamespace(
            resolve=lambda payload: SimpleNamespace(shared=True), scope=lambda payload: nullcontext(),
            store=None,
        )
        self.service.engine.turn_coordinator = SimpleNamespace()
        barrier = threading.Barrier(2)
        contexts, failures = [], []
        resolve = self.service._resolve_host

        def wait_then_resolve(**kwargs):
            barrier.wait(timeout=3)
            return resolve(**kwargs)

        def prepare():
            try:
                contexts.append(self.service._prepare_runtime_context(_request()))
            except Exception as error:
                failures.append(type(error).__name__)

        with patch.object(self.service, "_resolve_host", side_effect=wait_then_resolve):
            threads = [threading.Thread(target=prepare) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=4)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertFalse(failures)
        self.assertEqual(len(contexts), 2)
        self.assertIs(contexts[0].host, contexts[1].host)
        self.assertIs(contexts[0].private_lease, contexts[1].private_lease)
        self.assertIs(contexts[0].private_lease, contexts[0].host.private_lease)

    def test_call_identity_includes_the_host_issued_conversation_reference(self):
        async def exercise():
            request = _request(conversation_ref="trusted-ref-a")
            opened = await self.service.open_call(request)
            self.assertTrue(opened.ready)
            rejected = opened.call.create_turn(replace(request, conversation_ref="trusted-ref-b"))
            self.assertEqual(rejected.reason, "voice_realtime_call_identity_conflict")
            self.assertFalse(opened.call._turn_coordinators)
            await opened.call.cancel()

        asyncio.run(exercise())

    def test_call_that_opens_after_shutdown_is_cancelled_before_becoming_ready(self):
        async def exercise():
            entered, release = asyncio.Event(), asyncio.Event()

            async def wait_for_release():
                entered.set()
                await release.wait()

            self.adapter.before_open = wait_for_release
            opening = asyncio.create_task(self.service.open_call(_request()))
            await asyncio.wait_for(entered.wait(), timeout=2)
            try:
                closing = self.service.close()
                self.assertEqual(closing["status"], "degraded")
            finally:
                release.set()
            opened = await asyncio.wait_for(opening, timeout=2)
            self.assertFalse(opened.ready)
            self.assertEqual(opened.reason, "voice_runtime_service_closed")
            self.assertEqual(self.adapter.raw.cancel_count, 1)
            self.assertEqual(self.service.close()["status"], "stopped")

        asyncio.run(exercise())

    def test_failed_asr_open_uses_a_safe_reason(self):
        async def failed_open(**kwargs):
            return ASRSessionOpenResult(status="failed", mode=ASRSessionMode.STREAMING,
                reason="private path and token", retryable=True)

        self.adapter.open_session = failed_open
        failed = asyncio.run(self.service.open_call(_request()))
        self.assertEqual(failed.reason, "asr_provider_open_failed")
        self.assertNotIn("private path", str(failed))

    def test_failed_unclaimed_asr_cancel_remains_owned_until_a_successful_retry(self):
        async def exercise():
            attempts = []

            async def cancel():
                attempts.append(True)
                if len(attempts) == 1:
                    raise OSError("private provider detail")

            session = SimpleNamespace(supports_turn_commit=False, cancel=cancel)

            async def unsupported_open(**kwargs):
                return ASRSessionOpenResult.succeeded(ASRSessionMode.STREAMING, session)

            self.adapter.open_session = unsupported_open
            failed = await self.service.open_call(_request())
            self.assertEqual(failed.reason, "asr_provider_cancel_failed")
            self.assertNotIn("private provider detail", str(failed))
            self.assertEqual(self.service.close()["status"], "degraded")
            for _ in range(20):
                pending = next(iter(self.service._unclaimed_asr_sessions.values()))
                if pending.future is not None and pending.future.done():
                    break
                await asyncio.sleep(0)
            self.assertEqual(self.service.close()["status"], "stopped")
            self.assertEqual(len(attempts), 2)

        asyncio.run(exercise())

    def _attach(self, *, channel=None, executor=None, lease=None):
        if channel is not None:
            self.service._delivery_channels[("conversation", "turn")] = channel
        if executor is not None:
            self.service._thinking_executors["conversation"] = executor
        if lease is not None:
            self.service._hosts["conversation"] = SimpleNamespace(private_lease=lease)

    def test_failed_channel_close_is_reported_and_can_be_retried(self):
        channel = SimpleNamespace(close=Mock(side_effect=[
            VoicePlaybackDeliveryResult(status="failed", reason="voice_journal_busy", retryable=True),
            VoicePlaybackDeliveryResult(status="accepted"),
        ]))
        self._attach(channel=channel)
        first = self.service.close()
        self.assertEqual(first["status"], "degraded")
        self.assertIn("voice_journal_busy", first["failures"])
        self.assertEqual(self.service.close()["status"], "stopped")
        self.assertEqual(channel.close.call_count, 2)

    def test_running_workers_do_not_report_stopped_on_a_second_close(self):
        with patch.object(self.service._background_tasks, "close", side_effect=[False, False, True]):
            self.assertEqual(self.service.close()["status"], "degraded")
            self.assertEqual(self.service.close()["status"], "degraded")
            self.assertEqual(self.service.close()["status"], "stopped")

    def test_private_lease_is_released_only_after_workers_really_stop(self):
        lease = SimpleNamespace(close=Mock())
        self._attach(lease=lease)
        with patch.object(self.service._background_tasks, "close", side_effect=[False, True]):
            self.assertEqual(self.service.close()["status"], "degraded")
            lease.close.assert_not_called()
            self.assertEqual(self.service.close()["status"], "stopped")
        lease.close.assert_called_once()

    def test_failed_channel_keeps_the_private_lease_until_settlement(self):
        channel = SimpleNamespace(close=Mock(side_effect=[
            VoicePlaybackDeliveryResult(status="failed", reason="voice_journal_busy"),
            VoicePlaybackDeliveryResult(status="accepted"),
        ]))
        lease = SimpleNamespace(close=Mock())
        self._attach(channel=channel, lease=lease)
        self.assertEqual(self.service.close()["status"], "degraded")
        lease.close.assert_not_called()
        self.assertEqual(self.service.close()["status"], "stopped")
        lease.close.assert_called_once()

    def test_one_executor_close_exception_does_not_skip_other_cleanup(self):
        broken = SimpleNamespace(close=Mock(side_effect=[OSError("private path and token"), None]))
        good = SimpleNamespace(close=Mock())
        self._attach(executor=broken)
        self.service._tts_executors["conversation"] = good
        with patch.object(self.service._background_tasks, "close", return_value=True) as drain:
            first = self.service.close()
            self.assertEqual(first["status"], "degraded")
            self.assertNotIn("private path", str(first))
            good.close.assert_called_once()
            drain.assert_called_once()
            self.assertEqual(self.service.close()["status"], "stopped")
        self.assertEqual(broken.close.call_count, 2)
        good.close.assert_called_once()

    def test_channel_close_exception_does_not_skip_executor_or_worker_cleanup(self):
        channel = SimpleNamespace(close=Mock(side_effect=[OSError("private path and token"),
            VoicePlaybackDeliveryResult(status="accepted")]))
        executor = SimpleNamespace(close=Mock())
        self._attach(channel=channel, executor=executor)
        with patch.object(self.service._background_tasks, "close", return_value=True) as drain:
            first = self.service.close()
            self.assertEqual(first["status"], "degraded")
            self.assertNotIn("private path", str(first))
            executor.close.assert_called_once()
            drain.assert_called_once()
            self.assertEqual(self.service.close()["status"], "stopped")

    def test_private_lease_close_failure_is_retained_for_a_later_retry(self):
        lease = SimpleNamespace(close=Mock(side_effect=[OSError("private lease detail"), None]))
        self._attach(lease=lease)
        first = self.service.close()
        self.assertEqual(first["status"], "degraded")
        self.assertNotIn("private lease detail", str(first))
        self.assertEqual(self.service.close()["status"], "stopped")
        self.assertEqual(lease.close.call_count, 2)

    def test_concurrent_close_cannot_claim_stopped_while_first_close_is_pending(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []

        def blocked_close(**kwargs):
            entered.set()
            release.wait(timeout=3)
            return True

        with patch.object(self.service._background_tasks, "close", side_effect=blocked_close):
            thread = threading.Thread(target=lambda: outcomes.append(self.service.close()))
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            try:
                self.assertEqual(self.service.close()["status"], "degraded")
            finally:
                release.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes[0]["status"], "stopped")

    def test_external_cleanup_runs_outside_the_service_state_lock(self):
        acquired = []

        def close_channel(**kwargs):
            def take_lock():
                locked = self.service._guard.acquire(timeout=1)
                acquired.append(locked)
                if locked:
                    self.service._guard.release()

            thread = threading.Thread(target=take_lock)
            thread.start()
            thread.join(timeout=2)
            return VoicePlaybackDeliveryResult(status="accepted")

        self._attach(channel=SimpleNamespace(close=close_channel))
        self.assertEqual(self.service.close()["status"], "stopped")
        self.assertEqual(acquired, [True])

    def test_completed_close_is_idempotent_without_reclosing_resources(self):
        executor = SimpleNamespace(close=Mock())
        self._attach(executor=executor)
        self.assertEqual(self.service.close()["status"], "stopped")
        self.assertEqual(self.service.close()["status"], "stopped")
        executor.close.assert_called_once()

    def test_two_turns_in_the_same_conversation_share_one_host(self):
        first = self.service.create_coordinator(_request())
        second = self.service.create_coordinator(_request(voice_turn_id="turn-2", audio_stream_id="audio-2"))
        self.assertTrue(first.ready)
        self.assertTrue(second.ready)
        self.assertIs(first.coordinator.bridge.host, second.coordinator.bridge.host)
        self.assertEqual(len(self.service._hosts), 1)
