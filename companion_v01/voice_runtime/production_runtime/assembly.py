"""Serialized conversation Host and ordered construction of its runtime ports."""
from __future__ import annotations

import threading
from typing import Any, Callable

from voicecore import VoiceEvent, initial_snapshot

from ..candidate import AkaneVoiceCandidateValidationCommandExecutor
from ..durable_ports import FileVoiceAudioArtifactPort, FileVoiceTextArtifactPort, SqliteVoiceRuntimeJournal
from ..host import AkaneVoiceRuntimeHost
from ..playback_delivery import AkaneVoicePlaybackCommandExecutor
from ..realtime_protocol import VoiceRealtimeCoordinatorResolution
from ..semantic_pulse import AkaneVoiceSemanticPulseCommandExecutor
from ..thinking_agent import AkaneThinkingAgentCommandExecutor
from ..tts_executor import AkaneVoiceTTSCommandExecutor, VoiceCommandRouterExecutor
from .events import MemcoreVoiceProjectionPort
from .lifecycle import _RuntimeResources


class _SerializedVoiceRuntimeHost:
    """Serialize one conversation while model work runs off-thread."""

    def __init__(self, host: AkaneVoiceRuntimeHost) -> None:
        self._host = host
        self._guard = threading.RLock()
        self._after_drive: Callable[[], Any] | None = None
        self._after_observation: Callable[[], Any] | None = None

    def set_after_drive(self, callback: Callable[[], Any] | None) -> None:
        with self._guard:
            self._after_drive = callback

    def set_after_observation(self, callback: Callable[[], Any]) -> None:
        self._after_observation = callback

    @property
    def snapshot(self) -> Any:
        return self._host.snapshot

    def accept_event(self, event: VoiceEvent) -> Any:
        with self._guard:
            result = self._host.accept_event(event)
            # A playback ACK may settle and persist the final projection with
            # no pending command, so drive_once will not necessarily run again.
            if self._after_observation is not None:
                self._after_observation()
            return result

    def drive_once(self) -> Any:
        with self._guard:
            result = self._host.drive_once()
            callback = self._after_drive
            if callback is not None:
                callback()
            return result

    def drain_projection_outbox(self) -> Any:
        with self._guard:
            result = self._host.drain_projection_outbox()
            if self._after_observation is not None:
                self._after_observation()
            return result

    def drain_command_receipts(self) -> Any:
        with self._guard:
            return self._host.drain_command_receipts()


class RuntimeAssembly:
    """Assembly methods share the owning service state and lock."""

    def _resolve_host(
        self,
        *,
        canonical_conversation_id: str,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        tts_client: Any,
        conversation_ref: str = "",
        private_lease: Any = None,
    ) -> _SerializedVoiceRuntimeHost | VoiceRealtimeCoordinatorResolution:
        with self._guard:
            pending_cleanup = self._initializing_resources.get(canonical_conversation_id)
        if pending_cleanup is not None and not self._cleanup_failed_initialization(
            canonical_conversation_id, pending_cleanup,
        ):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_runtime_initialization_cleanup_pending", status="unavailable", retryable=True,
            )
        owned = _RuntimeResources()
        published = False
        try:
            with self._guard:
                if self._closed:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        "voice_runtime_service_closed", status="unavailable", retryable=True,
                    )
                existing = self._hosts.get(canonical_conversation_id)
                if existing is not None:
                    published = True
                    return existing
                self._initializing_resources[canonical_conversation_id] = owned
                if private_lease is not None:
                    owned.leases.append(private_lease)
                journal = SqliteVoiceRuntimeJournal(
                    state_dir=self.state_dir,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                )
                base_snapshot = initial_snapshot(
                    canonical_conversation_id,
                    conversation_generation=1,
                )
                replayed = journal.replay(base_snapshot)
                if not replayed.ok or replayed.replay is None:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        replayed.reason or "voice_journal_replay_failed",
                        status="unavailable",
                        retryable=False,
                        safe_public_summary="实时语音状态无法恢复，本轮没有开始。",
                    )
                projection_port = MemcoreVoiceProjectionPort(
                    manager=manager,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
                text_artifacts = FileVoiceTextArtifactPort(
                    state_dir=self.state_dir,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                )
                audio_artifacts = FileVoiceAudioArtifactPort(
                    state_dir=self.state_dir,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                )
                thinking_executor = AkaneThinkingAgentCommandExecutor(
                    engine=self.engine,
                    memcore_manager=manager,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                    text_artifacts=text_artifacts,
                    background_tasks=self._background_tasks,
                    conversation_ref=conversation_ref,
                )
                owned.executors.append(thinking_executor)
                semantic_executor = AkaneVoiceSemanticPulseCommandExecutor(
                    engine=self.engine,
                    memcore_manager=manager,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                    text_artifacts=text_artifacts,
                    background_tasks=self._background_tasks,
                    runtime_metrics=self.runtime_metrics,
                )
                owned.executors.append(semantic_executor)
                candidate_executor = AkaneVoiceCandidateValidationCommandExecutor(
                    engine=self.engine,
                    memcore_manager=manager,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                    background_tasks=self._background_tasks,
                    generation_starter=(
                        lambda host, voice_turn_id: thinking_executor.start_ready_generations(
                            host,
                            voice_turn_id=voice_turn_id,
                        )
                    ),
                )
                owned.executors.append(candidate_executor)
                tts_executor = AkaneVoiceTTSCommandExecutor(
                    tts_client=tts_client,
                    text_artifacts=text_artifacts,
                    audio_artifacts=audio_artifacts,
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                    background_tasks=self._background_tasks,
                )
                owned.executors.append(tts_executor)
                playback_executor = AkaneVoicePlaybackCommandExecutor(
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                )
                command_executor = VoiceCommandRouterExecutor(
                    {
                        "start_response_generation": thinking_executor,
                        "cancel_response_generation": thinking_executor,
                        "start_tts": tts_executor,
                        "enqueue_playback": playback_executor,
                        "duck_playback": playback_executor,
                        "resume_playback": playback_executor,
                        "stop_playback": playback_executor,
                        "request_semantic_pulse": semantic_executor,
                        "validate_response_candidate": candidate_executor,
                    }
                )
                raw_host = AkaneVoiceRuntimeHost(
                    conversation_id=canonical_conversation_id,
                    conversation_generation=1,
                    journal=journal,
                    projection_port=projection_port,
                    command_executor=command_executor,
                    restored_snapshot=replayed.replay.snapshot,
                )
                host = _SerializedVoiceRuntimeHost(raw_host)
                host.private_lease = private_lease
                if private_lease is not None:
                    host.set_after_observation(lambda: private_lease.settle_failed_inputs(host, thinking_executor))
                tts_executor.bind_host(host, notify_delivery=playback_executor.notify_runtime_change)
                stale_generation_ids = tuple(
                    str(response.response_id)
                    for response in host.snapshot.responses.values()
                    if str(getattr(response.state, "value", "") or "") in {"generating", "streaming"}
                )
                semantic_executor.bind_host(host)
                candidate_executor.bind_host(host)
                pending = host.drain_projection_outbox()
                if not pending.quiescent:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        pending.reason or "voice_projection_recovery_failed",
                        status="unavailable",
                        retryable=pending.status == "deferred",
                        safe_public_summary="实时语音记忆投影尚未恢复，本轮没有开始。",
                    )
                receipts = host.drain_command_receipts()
                if not receipts.quiescent:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        receipts.reason or "voice_command_recovery_failed",
                        status="unavailable",
                        retryable=receipts.status == "deferred",
                        safe_public_summary="实时语音回复状态尚未恢复，本轮没有开始。",
                    )
                discarded_candidates = candidate_executor.discard_restarted_candidates(host)
                if not discarded_candidates.ok:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        discarded_candidates.reason or "voice_candidate_restart_discard_failed",
                        status="unavailable",
                        retryable=discarded_candidates.retryable,
                        safe_public_summary="上次未确认的候选回复无法安全清理，本轮没有开始。",
                    )
                recovered_commands = self._recover_pending_commands(host)
                if not recovered_commands.ok:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        recovered_commands.reason or "voice_response_command_recovery_failed",
                        status="unavailable",
                        retryable=recovered_commands.retryable,
                        safe_public_summary=(
                            recovered_commands.safe_public_summary or "实时语音回复命令无法恢复，本轮没有开始。"
                        ),
                    )
                failed_stale_generations = thinking_executor.fail_restarted_generations(
                    host,
                    response_ids=stale_generation_ids,
                )
                if not failed_stale_generations.ok:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        failed_stale_generations.reason or "voice_thinking_restart_recovery_failed",
                        status="unavailable",
                        retryable=failed_stale_generations.retryable,
                        safe_public_summary="上次中断的语音回复无法安全收口，本轮没有开始。",
                    )
                restored = thinking_executor.restore_generating_jobs(self._snapshot_record(host))
                if restored.status == "failed":
                    return VoiceRealtimeCoordinatorResolution.failed(
                        restored.reason or "voice_thinking_recovery_failed",
                        status="unavailable",
                        retryable=restored.retryable,
                        safe_public_summary="实时语音回复状态无法恢复，本轮没有开始。",
                    )
                semantic_executor.enable_live_commands()
                candidate_executor.enable_live_commands()
                tts_executor.enable_live_commands()

                def after_drive() -> Any:
                    try:
                        # Overlap decisions can commit after the ASR callback has
                        # returned, or advance while playback controls await ACK.
                        for turn_id in {attempt.voice_turn_id for attempt in host.snapshot.interruption_attempts.values()}:
                            turn = host.snapshot.input_turns.get(turn_id)
                            if turn is not None and turn.state.value == "committed" and turn.disposition.value == "message":
                                thinking_executor.start_ready_generations(
                                    host,
                                    voice_turn_id=turn_id,
                                    commitment="committed",
                                )
                        return thinking_executor.start_ready_generations(host, commitment="speculative")
                    finally:
                        playback_executor.notify_all_channels()
                        if private_lease is not None:
                            private_lease.settle_failed_inputs(host, thinking_executor)

                host.set_after_drive(after_drive)
                if restored.status == "started":
                    owned.pending_background = True
                    started = thinking_executor.start_ready_generations(host)
                    if not started.ok:
                        return VoiceRealtimeCoordinatorResolution.failed(
                            started.reason or "voice_thinking_recovery_start_failed",
                            status="unavailable",
                            retryable=started.retryable,
                            safe_public_summary=(
                                started.safe_public_summary or "实时语音回复恢复任务无法启动，本轮没有开始。"
                            ),
                        )
                self._hosts[canonical_conversation_id] = host
                self._thinking_executors[canonical_conversation_id] = thinking_executor
                self._semantic_executors[canonical_conversation_id] = semantic_executor
                self._candidate_executors[canonical_conversation_id] = candidate_executor
                self._playback_executors[canonical_conversation_id] = playback_executor
                self._tts_executors[canonical_conversation_id] = tts_executor
                self._text_artifacts[canonical_conversation_id] = text_artifacts
                self._audio_artifacts[canonical_conversation_id] = audio_artifacts
                self._initializing_resources.pop(canonical_conversation_id)
                published = True
                return host
        finally:
            if not published:
                self._cleanup_failed_initialization(canonical_conversation_id, owned)
