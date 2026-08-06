from __future__ import annotations

import hashlib
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from capcore_adapter_speech import (
    ASRSessionOpenResult,
    ASRSessionUpdate,
    NormalizedASRSession,
    PCMStreamNormalizer,
)
from voicecore import VoiceEvent, initial_snapshot

from ..background_tasks import BackgroundTaskRunner
from .asr_bridge import VoiceASRSessionBridge
from .asr_provider import build_voice_asr_provider
from .asr_realtime_turn import VoiceASRRealtimeTurnCoordinator
from .candidate import AkaneVoiceCandidateValidationCommandExecutor
from .durable_ports import (
    FileVoiceAudioArtifactPort,
    FileVoiceTextArtifactPort,
    SqliteVoiceRuntimeJournal,
)
from .host import (
    AkaneVoiceRuntimeHost,
    VoiceHostPortResult,
)
from .playback_delivery import (
    VOICE_PLAYBACK_OUTPUT_MODE,
    AkaneVoicePlaybackCommandExecutor,
    VoicePlaybackDeliveryChannel,
)
from .realtime_transport import (
    VoiceRealtimeCoordinatorResolution,
    VoiceRealtimeOpenRequest,
)
from .semantic_pulse import AkaneVoiceSemanticPulseCommandExecutor
from .thinking_agent import (
    AkaneThinkingAgentCommandExecutor,
    VoiceThinkingStartResult,
)
from .tts_executor import (
    AkaneVoiceTTSCommandExecutor,
    VoiceCommandRouterExecutor,
)


_SAFE_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class _SerializedVoiceRuntimeHost:
    """Serialize one conversation while model work runs off-thread."""

    def __init__(self, host: AkaneVoiceRuntimeHost) -> None:
        self._host = host
        self._guard = threading.RLock()
        self._after_drive: Callable[[], Any] | None = None

    def set_after_drive(self, callback: Callable[[], Any] | None) -> None:
        with self._guard:
            self._after_drive = callback

    @property
    def snapshot(self) -> Any:
        return self._host.snapshot

    def accept_event(self, event: VoiceEvent) -> Any:
        with self._guard:
            return self._host.accept_event(event)

    def drive_once(self) -> Any:
        with self._guard:
            result = self._host.drive_once()
            callback = self._after_drive
            if callback is not None:
                callback()
            return result

    def drain_projection_outbox(self) -> Any:
        with self._guard:
            return self._host.drain_projection_outbox()

    def drain_command_receipts(self) -> Any:
        with self._guard:
            return self._host.drain_command_receipts()


class AkaneVoiceEventFactory:
    """Create production VoiceCore events without importing its test helpers."""

    def __init__(
        self,
        *,
        conversation_id: str,
        voice_session_id: str,
        conversation_generation: int,
        producer: str = "akane.voice_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.conversation_id = str(conversation_id or "")
        self.voice_session_id = str(voice_session_id or "")
        self.conversation_generation = int(conversation_generation)
        self.producer = str(producer or "akane.voice_runtime")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent:
        now = self.clock().astimezone(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=str(overrides.pop("event_id", f"voice_evt_{uuid.uuid4().hex}")),
            event_kind=str(event_kind or ""),
            occurred_at=str(overrides.pop("occurred_at", now)),
            recorded_at=str(overrides.pop("recorded_at", now)),
            conversation_id=str(overrides.pop("conversation_id", self.conversation_id)),
            voice_session_id=str(overrides.pop("voice_session_id", self.voice_session_id)),
            conversation_generation=int(overrides.pop("conversation_generation", self.conversation_generation)),
            producer=str(overrides.pop("producer", self.producer)),
            **overrides,
        )


class MemcoreVoiceProjectionPort:
    """Thin VoiceCore projection adapter over MemcoreManager's typed writer."""

    def __init__(
        self,
        *,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> None:
        self.manager = manager
        self.profile_user_id = str(profile_user_id or "")
        self.session_id = str(session_id or "")
        self.character_pack_id = str(character_pack_id or "")

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        if not isinstance(projection_record, Mapping):
            return VoiceHostPortResult.failed("voice_projection_record_invalid")
        normalized_record = dict(projection_record)
        target = str(normalized_record.get("target") or "").strip().lower()
        kind = str(normalized_record.get("kind") or "").strip().lower()
        if target in {"host", "runtime"} and kind.startswith("voice."):
            payload = normalized_record.get("payload")
            normalized_record = {
                **normalized_record,
                "target": "memcore",
                "kind": f"event.{kind}",
                "payload": {
                    **(dict(payload) if isinstance(payload, Mapping) else {}),
                    "projection_target": target,
                    "projection_kind": kind,
                },
            }
        try:
            result = self.manager.record_voice_projection(
                normalized_record,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
                actor_stable_id=self.profile_user_id,
            )
        except Exception:
            return VoiceHostPortResult.failed(
                "voice_memcore_projection_failed",
                retryable=True,
            )
        if not isinstance(result, Mapping):
            return VoiceHostPortResult.failed("voice_memcore_projection_result_invalid")
        if bool(result.get("ok")):
            return VoiceHostPortResult.succeeded()
        status = _safe_reason(result.get("status"), fallback="failed")
        reason = _safe_reason(
            result.get("reason"),
            fallback=f"voice_memcore_projection_{status}",
        )
        return VoiceHostPortResult.failed(
            reason,
            retryable=status in {"failed", "unavailable"},
        )


@dataclass(frozen=True)
class VoiceRuntimeCallOpenResult:
    status: str
    reason: str = ""
    call: AkaneVoiceRuntimeCall | None = None
    retryable: bool = False
    safe_public_summary: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.call is not None

    @classmethod
    def succeeded(
        cls,
        call: AkaneVoiceRuntimeCall,
    ) -> VoiceRuntimeCallOpenResult:
        return cls(status="ready", call=call)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        status: str = "unavailable",
        retryable: bool = False,
        safe_public_summary: str = "",
    ) -> VoiceRuntimeCallOpenResult:
        return cls(
            status=status,
            reason=str(reason or "voice_realtime_call_unavailable"),
            retryable=bool(retryable),
            safe_public_summary=str(safe_public_summary or ""),
        )


@dataclass(frozen=True)
class _VoiceRuntimeContext:
    provider: Any
    character_pack_id: str
    canonical_conversation_id: str
    host: _SerializedVoiceRuntimeHost
    thinking_executor: AkaneThinkingAgentCommandExecutor
    playback_executor: AkaneVoicePlaybackCommandExecutor | None
    text_artifacts: FileVoiceTextArtifactPort | None
    audio_artifacts: FileVoiceAudioArtifactPort | None


class AkaneVoiceRuntimeCall:
    """Own one provider ASR session across multiple VoiceCore input turns."""

    def __init__(
        self,
        *,
        service: AkaneVoiceRuntimeService,
        open_request: VoiceRealtimeOpenRequest,
        context: _VoiceRuntimeContext,
        provider_session: NormalizedASRSession,
        voice_session_id: str,
    ) -> None:
        self.service = service
        self.open_request = open_request
        self.context = context
        self.provider_session = provider_session
        self.voice_session_id = str(voice_session_id or "")
        self.provider_id = str(getattr(context.provider, "provider_id", "") or "")
        self._active_coordinator: VoiceASRRealtimeTurnCoordinator | None = None
        self._turn_coordinators: dict[str, VoiceASRRealtimeTurnCoordinator] = {}
        self._used_voice_turn_ids: set[str] = set()
        self._used_audio_stream_ids: set[str] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def create_turn(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> VoiceRealtimeCoordinatorResolution:
        if self._closed:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_call_closed",
                status="unavailable",
            )
        if not self._same_call_identity(request):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_call_identity_conflict",
                status="invalid_request",
            )
        if self._active_coordinator is not None and not self._active_coordinator.terminal:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_call_input_turn_active",
                status="conflict",
            )
        if request.voice_turn_id in self._used_voice_turn_ids or request.audio_stream_id in self._used_audio_stream_ids:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_call_turn_identity_reused",
                status="conflict",
            )
        resolved = self.service._build_turn_coordinator(
            request=request,
            context=self.context,
            voice_session_id=self.voice_session_id,
            provider_session=self.provider_session,
        )
        if resolved.ready:
            self._active_coordinator = resolved.coordinator
            self._turn_coordinators[request.voice_turn_id] = resolved.coordinator
            self._used_voice_turn_ids.add(request.voice_turn_id)
            self._used_audio_stream_ids.add(request.audio_stream_id)
        return resolved

    def cancel_response(self, *, voice_turn_id: str, reason: str) -> Any:
        coordinator = self._turn_coordinators.get(str(voice_turn_id or ""))
        if coordinator is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_call_turn_unknown",
                status="not_found",
            )
        return coordinator.cancel_response(reason=str(reason or "client_cancelled"))

    def release_response(self, voice_turn_id: str) -> None:
        """Drop the delivery lookup after the response is durably terminal."""

        turn_id = str(voice_turn_id or "")
        self._turn_coordinators.pop(turn_id, None)

    async def finish(self) -> ASRSessionUpdate:
        if self._closed:
            return ASRSessionUpdate.duplicate(self.provider_session.mode)
        if self._active_coordinator is not None and not self._active_coordinator.terminal:
            return ASRSessionUpdate.failed(
                self.provider_session.mode,
                "voice_realtime_call_input_turn_active",
            )
        result = await self.provider_session.finish_call()
        if result.ok:
            self._closed = True
            self._turn_coordinators.clear()
            self._active_coordinator = None
        return result

    async def cancel(self) -> ASRSessionUpdate:
        if self._closed:
            return ASRSessionUpdate.duplicate(self.provider_session.mode)
        result = await self.provider_session.cancel()
        if result.status in {"cancelled", "duplicate"}:
            self._closed = True
            self._turn_coordinators.clear()
            self._active_coordinator = None
        return result

    def _same_call_identity(self, request: VoiceRealtimeOpenRequest) -> bool:
        if not isinstance(request, VoiceRealtimeOpenRequest):
            return False
        fields = (
            "profile_user_id",
            "conversation_id",
            "session_id",
            "disposition",
            "language",
            "character_pack_id",
            "input_format",
            "sample_rate",
            "channels",
            "output_mode",
        )
        return all(getattr(request, field) == getattr(self.open_request, field) for field in fields)


class AkaneVoiceRuntimeService:
    """Own durable VoiceCore hosts for one BotRuntime instance."""

    def __init__(
        self,
        *,
        engine: Any,
        settings: Any,
        state_dir: Path,
        instance_id: str,
        bot_id: str,
        default_character_pack_id: str = "",
        tts_client: Any = None,
        tts_client_resolver: Callable[..., Any] | None = None,
        runtime_metrics: Any = None,
        provider_builder: Callable[[Any], Any] = build_voice_asr_provider,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.state_dir = Path(state_dir)
        self.instance_id = str(instance_id or "")
        self.bot_id = str(bot_id or "")
        self.default_character_pack_id = str(default_character_pack_id or "")
        self.tts_client = tts_client
        self.tts_client_resolver = tts_client_resolver
        self.runtime_metrics = runtime_metrics
        self.provider_builder = provider_builder
        self._hosts: dict[str, _SerializedVoiceRuntimeHost] = {}
        self._thinking_executors: dict[
            str,
            AkaneThinkingAgentCommandExecutor,
        ] = {}
        self._semantic_executors: dict[
            str,
            AkaneVoiceSemanticPulseCommandExecutor,
        ] = {}
        self._candidate_executors: dict[
            str,
            AkaneVoiceCandidateValidationCommandExecutor,
        ] = {}
        self._playback_executors: dict[str, AkaneVoicePlaybackCommandExecutor] = {}
        self._text_artifacts: dict[str, FileVoiceTextArtifactPort] = {}
        self._audio_artifacts: dict[str, FileVoiceAudioArtifactPort] = {}
        self._delivery_channels: dict[
            tuple[str, str],
            VoicePlaybackDeliveryChannel,
        ] = {}
        self._background_tasks = BackgroundTaskRunner(default_workers=1)
        self._guard = threading.RLock()
        self._closed = False

    def create_coordinator(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> VoiceRealtimeCoordinatorResolution:
        invalid = self._validate_open_request(request)
        if invalid is not None:
            return invalid
        context = self._prepare_runtime_context(request)
        if isinstance(context, VoiceRealtimeCoordinatorResolution):
            return context
        return self._build_turn_coordinator(
            request=request,
            context=context,
            voice_session_id=f"voice_session_{uuid.uuid4().hex}",
        )

    async def open_call(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> VoiceRuntimeCallOpenResult:
        invalid = self._validate_open_request(request)
        if invalid is not None:
            return self._call_failure(invalid)
        context = self._prepare_runtime_context(request)
        if isinstance(context, VoiceRealtimeCoordinatorResolution):
            return self._call_failure(context)
        adapter = getattr(context.provider, "adapter", None)
        try:
            opened = await adapter.open_session(
                filename="akane_voice_input.pcm",
                content_type="audio/pcm",
                language=request.language,
            )
        except Exception:
            return VoiceRuntimeCallOpenResult.failed(
                "asr_provider_open_failed",
                retryable=True,
            )
        if not isinstance(opened, ASRSessionOpenResult) or not opened.ok or opened.session is None:
            return VoiceRuntimeCallOpenResult.failed(
                str(getattr(opened, "reason", "") or "asr_provider_open_failed"),
                status="unavailable",
                retryable=bool(getattr(opened, "retryable", False)),
                safe_public_summary=str(getattr(opened, "safe_public_summary", "") or ""),
            )
        if not bool(getattr(opened.session, "supports_turn_commit", False)):
            await opened.session.cancel()
            return VoiceRuntimeCallOpenResult.failed(
                "asr_provider_commit_turn_unsupported",
                status="unsupported",
                safe_public_summary="当前语音识别服务不支持连续多轮通话。",
            )
        return VoiceRuntimeCallOpenResult.succeeded(
            AkaneVoiceRuntimeCall(
                service=self,
                open_request=request,
                context=context,
                provider_session=opened.session,
                voice_session_id=f"voice_session_{uuid.uuid4().hex}",
            )
        )

    def _validate_open_request(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> VoiceRealtimeCoordinatorResolution | None:
        if not isinstance(request, VoiceRealtimeOpenRequest):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_open_request_invalid",
                status="invalid_request",
            )
        with self._guard:
            if self._closed:
                return VoiceRealtimeCoordinatorResolution.failed(
                    "voice_runtime_service_closed",
                    status="unavailable",
                    retryable=True,
                )
        return None

    def _prepare_runtime_context(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> _VoiceRuntimeContext | VoiceRealtimeCoordinatorResolution:
        manager = self._memcore_manager()
        if manager is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_memcore_unavailable",
                status="unavailable",
                retryable=True,
                safe_public_summary="记忆时间线暂时不可用，实时语音没有开始。",
            )
        try:
            provider = self.provider_builder(self.settings)
        except Exception:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_asr_provider_build_failed",
                status="unavailable",
                retryable=True,
            )
        if not bool(getattr(provider, "ready", False)) or getattr(provider, "adapter", None) is None:
            reason = _safe_reason(
                getattr(provider, "reason", ""),
                fallback="voice_asr_provider_unavailable",
            )
            return VoiceRealtimeCoordinatorResolution.failed(
                reason,
                status=_safe_reason(getattr(provider, "status", ""), fallback="unavailable"),
                retryable=str(getattr(provider, "status", "")) == "unavailable",
                safe_public_summary=_provider_public_summary(reason),
            )
        character_pack_id = request.character_pack_id or self.default_character_pack_id
        resolved_tts_client = self._resolve_tts_client(
            profile_user_id=request.profile_user_id,
            session_id=request.session_id,
            character_pack_id=character_pack_id,
        )
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE and resolved_tts_client is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_tts_provider_unavailable",
                status="unavailable",
                retryable=False,
                safe_public_summary="当前角色的语音合成服务暂时不可用，本轮没有开始。",
            )
        canonical_conversation_id = self._canonical_conversation_id(
            request=request,
            character_pack_id=character_pack_id,
        )
        try:
            host_result = self._resolve_host(
                canonical_conversation_id=canonical_conversation_id,
                manager=manager,
                profile_user_id=request.profile_user_id,
                session_id=request.session_id,
                character_pack_id=character_pack_id,
                tts_client=resolved_tts_client,
            )
        except Exception:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_runtime_host_initialization_failed",
                status="unavailable",
                retryable=True,
                safe_public_summary="实时语音状态暂时无法初始化，本轮没有开始。",
            )
        if isinstance(host_result, VoiceRealtimeCoordinatorResolution):
            return host_result
        with self._guard:
            thinking_executor = self._thinking_executors.get(canonical_conversation_id)
            playback_executor = self._playback_executors.get(canonical_conversation_id)
            text_artifacts = self._text_artifacts.get(canonical_conversation_id)
            audio_artifacts = self._audio_artifacts.get(canonical_conversation_id)
        if thinking_executor is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_thinking_executor_unavailable",
                status="unavailable",
                retryable=True,
                safe_public_summary="实时语音回复服务暂时不可用，本轮没有开始。",
            )
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE and (
            playback_executor is None or text_artifacts is None or audio_artifacts is None
        ):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_playback_delivery_unavailable",
                status="unavailable",
                retryable=True,
                safe_public_summary="实时语音播放通道暂时不可用，本轮没有开始。",
            )
        return _VoiceRuntimeContext(
            provider=provider,
            character_pack_id=character_pack_id,
            canonical_conversation_id=canonical_conversation_id,
            host=host_result,
            thinking_executor=thinking_executor,
            playback_executor=playback_executor,
            text_artifacts=text_artifacts,
            audio_artifacts=audio_artifacts,
        )

    def _build_turn_coordinator(
        self,
        *,
        request: VoiceRealtimeOpenRequest,
        context: _VoiceRuntimeContext,
        voice_session_id: str,
        provider_session: NormalizedASRSession | None = None,
    ) -> VoiceRealtimeCoordinatorResolution:
        event_factory = AkaneVoiceEventFactory(
            conversation_id=context.canonical_conversation_id,
            voice_session_id=voice_session_id,
            conversation_generation=1,
        )
        delivery_channel: VoicePlaybackDeliveryChannel | None = None
        if request.output_mode == VOICE_PLAYBACK_OUTPUT_MODE:
            assert context.playback_executor is not None
            assert context.text_artifacts is not None
            assert context.audio_artifacts is not None
            delivery_channel = VoicePlaybackDeliveryChannel(
                host=context.host,
                voice_turn_id=request.voice_turn_id,
                conversation_id=context.canonical_conversation_id,
                conversation_generation=1,
                text_artifacts=context.text_artifacts,
                audio_artifacts=context.audio_artifacts,
                terminal_notifier=(
                    lambda voice_turn_id: self._release_delivery_channel(
                        context.canonical_conversation_id,
                        voice_turn_id,
                    )
                ),
            )
            context.playback_executor.register_channel(
                request.voice_turn_id,
                delivery_channel,
            )
            with self._guard:
                self._delivery_channels[(context.canonical_conversation_id, request.voice_turn_id)] = delivery_channel
        try:
            context.thinking_executor.register_turn(
                voice_turn_id=request.voice_turn_id,
                event_factory=event_factory,
                speech_delivery_enabled=delivery_channel is not None,
                delivery_notifier=(delivery_channel.notify_runtime_change if delivery_channel is not None else None),
            )
            coordinator = VoiceASRRealtimeTurnCoordinator(
                adapter=(None if provider_session is not None else context.provider.adapter),
                provider_session=provider_session,
                retain_provider_session=provider_session is not None,
                bridge=VoiceASRSessionBridge(
                    host=context.host,
                    event_factory=event_factory,
                    voice_turn_id=request.voice_turn_id,
                    audio_stream_id=request.audio_stream_id,
                    disposition=request.disposition,
                ),
                language=request.language,
                pcm_normalizer=PCMStreamNormalizer(
                    input_format=request.input_format,
                    input_sample_rate=request.sample_rate,
                    input_channels=request.channels,
                ),
                interruption_runtime_driver=context.host.drive_once,
                response_starter=(
                    lambda: self._start_response_generation(
                        host=context.host,
                        executor=context.thinking_executor,
                        voice_turn_id=request.voice_turn_id,
                    )
                ),
            )
        except (RuntimeError, TypeError, ValueError):
            if delivery_channel is not None:
                delivery_channel.close(reason="voice_realtime_coordinator_invalid")
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_coordinator_invalid",
                status="invalid_request",
            )
        return VoiceRealtimeCoordinatorResolution.succeeded(
            coordinator,
            provider_id=str(getattr(context.provider, "provider_id", "") or ""),
            voice_session_id=voice_session_id,
            delivery_channel=delivery_channel,
        )

    @staticmethod
    def _call_failure(
        failure: VoiceRealtimeCoordinatorResolution,
    ) -> VoiceRuntimeCallOpenResult:
        return VoiceRuntimeCallOpenResult.failed(
            failure.reason,
            status=failure.status,
            retryable=failure.retryable,
            safe_public_summary=failure.safe_public_summary,
        )

    def close(self) -> dict[str, Any]:
        with self._guard:
            if self._closed:
                return {"status": "stopped", "reason": "already_stopped"}
            self._closed = True
            host_count = len(self._hosts)
            executors = [
                *self._thinking_executors.values(),
                *self._semantic_executors.values(),
                *self._candidate_executors.values(),
            ]
            delivery_channels = list(self._delivery_channels.values())
            self._hosts.clear()
            self._thinking_executors.clear()
            self._semantic_executors.clear()
            self._candidate_executors.clear()
            self._playback_executors.clear()
            self._text_artifacts.clear()
            self._audio_artifacts.clear()
            self._delivery_channels.clear()
        for channel in delivery_channels:
            channel.close(reason="voice_runtime_stopped")
        for executor in executors:
            executor.close()
        background_stopped = self._background_tasks.close(timeout=10.0)
        return {
            "status": "stopped" if background_stopped else "degraded",
            "reason": "" if background_stopped else "voice_thinking_shutdown_incomplete",
            "host_count": host_count,
        }

    def wait_idle(self, *, timeout: float = 10.0) -> bool:
        return self._background_tasks.wait_idle(timeout=timeout)

    def _release_delivery_channel(
        self,
        canonical_conversation_id: str,
        voice_turn_id: str,
    ) -> None:
        with self._guard:
            channel = self._delivery_channels.pop(
                (canonical_conversation_id, voice_turn_id),
                None,
            )
            executor = self._playback_executors.get(canonical_conversation_id)
        if channel is not None and executor is not None:
            executor.unregister_channel(voice_turn_id, channel)

    def _resolve_host(
        self,
        *,
        canonical_conversation_id: str,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        tts_client: Any,
    ) -> _SerializedVoiceRuntimeHost | VoiceRealtimeCoordinatorResolution:
        with self._guard:
            existing = self._hosts.get(canonical_conversation_id)
            if existing is not None:
                return existing
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
            )
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
            tts_executor = AkaneVoiceTTSCommandExecutor(
                tts_client=tts_client,
                text_artifacts=text_artifacts,
                audio_artifacts=audio_artifacts,
                conversation_id=canonical_conversation_id,
                conversation_generation=1,
            )
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
            host.set_after_drive(
                lambda: thinking_executor.start_ready_generations(
                    host,
                    commitment="speculative",
                )
            )
            self._hosts[canonical_conversation_id] = host
            self._thinking_executors[canonical_conversation_id] = thinking_executor
            self._semantic_executors[canonical_conversation_id] = semantic_executor
            self._candidate_executors[canonical_conversation_id] = candidate_executor
            self._playback_executors[canonical_conversation_id] = playback_executor
            self._text_artifacts[canonical_conversation_id] = text_artifacts
            self._audio_artifacts[canonical_conversation_id] = audio_artifacts
            if restored.status == "started":
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
            return host

    def _resolve_tts_client(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> Any:
        resolver = self.tts_client_resolver
        if not callable(resolver):
            return self.tts_client
        try:
            return resolver(
                profile_user_id=str(profile_user_id or ""),
                session_id=str(session_id or ""),
                character_pack_id=str(character_pack_id or ""),
            )
        except Exception:
            return None

    @staticmethod
    def _snapshot_record(host: _SerializedVoiceRuntimeHost) -> dict[str, Any]:
        from voicecore import snapshot_to_dict

        return snapshot_to_dict(host.snapshot)

    @staticmethod
    def _recover_pending_commands(
        host: _SerializedVoiceRuntimeHost,
    ) -> VoiceThinkingStartResult:
        seen_pending: set[tuple[str, ...]] = set()
        while host.snapshot.pending_commands:
            pending_ids = tuple(host.snapshot.pending_commands)
            if pending_ids in seen_pending:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_command_recovery_stalled",
                    retryable=True,
                )
            seen_pending.add(pending_ids)
            pending = [host.snapshot.pending_commands[command_id] for command_id in pending_ids]
            if any(
                str(getattr(command, "command_kind", "") or "")
                not in {
                    "start_response_generation",
                    "cancel_response_generation",
                    "start_tts",
                    "enqueue_playback",
                    "request_semantic_pulse",
                    "validate_response_candidate",
                }
                for command in pending
            ):
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_command_recovery_unsupported",
                    retryable=False,
                )
            driven = host.drive_once()
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_command_recovery_failed",
                    retryable=(
                        driven.status == "deferred" or any(result.retryable for result in driven.command_results)
                    ),
                )
        return VoiceThinkingStartResult(status="completed")

    @staticmethod
    def _start_response_generation(
        *,
        host: _SerializedVoiceRuntimeHost,
        executor: AkaneThinkingAgentCommandExecutor,
        voice_turn_id: str,
    ) -> VoiceThinkingStartResult:
        seen_states: set[tuple[tuple[str, ...], str, str]] = set()
        while True:
            responses = [
                item
                for item in host.snapshot.responses.values()
                if str(getattr(item, "voice_turn_id", "") or "") == str(voice_turn_id or "")
            ]
            response = next(
                (
                    item
                    for item in responses
                    if str(getattr(getattr(item, "state", None), "value", "") or "") == "generating"
                ),
                None,
            )
            if response is None:
                response = next(
                    (
                        item
                        for item in reversed(responses)
                        if str(
                            getattr(
                                getattr(item, "state", None),
                                "value",
                                "",
                            )
                            or ""
                        )
                        not in {"failed", "cancelled", "discarded"}
                    ),
                    responses[-1] if responses else None,
                )
            response_state = str(getattr(getattr(response, "state", None), "value", "") or "")
            response_id = str(getattr(response, "response_id", "") or "")
            if response_state == "generating":
                return executor.start_ready_generations(
                    host,
                    voice_turn_id=voice_turn_id,
                )
            pending_ids = tuple(host.snapshot.pending_commands)
            if response_state == "completed":
                return VoiceThinkingStartResult(
                    status="completed",
                    response_id=response_id,
                )
            if response_state in {"failed", "cancelled", "discarded"} and not pending_ids:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_start_terminal",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复没有进入生成状态。",
                )
            if (
                response_state in {"streaming", "generated"}
                and str(
                    getattr(
                        getattr(response, "commitment", None),
                        "value",
                        "",
                    )
                    or ""
                )
                in {"speculative", "committed"}
                and not pending_ids
            ):
                return VoiceThinkingStartResult(
                    status="started",
                    response_id=response_id,
                )
            state_key = (pending_ids, response_id, response_state)
            if not pending_ids or state_key in seen_states:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_start_stalled",
                    retryable=True,
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复生成暂时没有启动。",
                )
            seen_states.add(state_key)
            driven = host.drive_once()
            if driven.status == "deferred" and any(
                command.command_kind == "validate_response_candidate"
                for command in host.snapshot.pending_commands.values()
            ):
                return VoiceThinkingStartResult(
                    status="started",
                    response_id=response_id,
                )
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_response_start_failed",
                    retryable=driven.status == "deferred",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复生成暂时无法启动。",
                )

    def _memcore_manager(self) -> Any | None:
        resolver = getattr(self.engine, "_memcore_manager_if_enabled", None)
        try:
            manager = resolver() if callable(resolver) else getattr(self.engine, "memcore_manager", None)
        except Exception:
            return None
        if (
            manager is None
            or not bool(getattr(manager, "enabled", False))
            or not bool(getattr(manager, "available", False))
            or not callable(getattr(manager, "record_voice_projection", None))
            or not callable(getattr(manager, "resolve_voice_projection_turn", None))
        ):
            return None
        return manager

    def _canonical_conversation_id(
        self,
        *,
        request: VoiceRealtimeOpenRequest,
        character_pack_id: str,
    ) -> str:
        material = "\0".join(
            (
                self.instance_id,
                self.bot_id,
                request.profile_user_id,
                request.session_id,
                character_pack_id,
                request.conversation_id,
            )
        )
        return "voice_conversation_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_reason(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if _SAFE_REASON.fullmatch(text) else str(fallback or "voice_runtime_failed")


def _provider_public_summary(reason: str) -> str:
    if reason == "fun_asr_realtime_disabled":
        return "实时语音识别尚未开启。"
    if reason in {"dashscope_api_key_missing", "dashscope_api_host_missing"}:
        return "实时语音识别配置不完整。"
    if reason == "fun_asr_realtime_config_invalid":
        return "实时语音识别配置无效。"
    return "实时语音识别服务暂时不可用。"
