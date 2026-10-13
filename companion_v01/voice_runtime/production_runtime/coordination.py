"""Validate ingress, resolve its ownership and wire an actual input turn."""
from __future__ import annotations

from capcore_adapter_speech import NormalizedASRSession, PCMStreamNormalizer

from ..asr_bridge import VoiceASRSessionBridge
from ..asr_realtime_turn import VoiceASRRealtimeTurnCoordinator
from ..playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE, VoicePlaybackDeliveryChannel
from ..private_session import PrivateVoiceCoordinator, PrivateVoiceLease, PrivateVoiceMemory
from ..realtime_protocol import VoiceRealtimeCoordinatorResolution, VoiceRealtimeOpenRequest
from .events import AkaneVoiceEventFactory, _provider_public_summary, _safe_reason
from .sessions import _VoiceRuntimeContext


class RuntimeCoordination:
    """Coordination methods share the owning service state and lock."""

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
        try:
            canonical_conversation_id = self._canonical_conversation_id(
                request=request,
                character_pack_id=character_pack_id,
            )
        except (TypeError, UnicodeError):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_identity_invalid", status="invalid_request",
            )
        policy = getattr(self.engine, "owner_private_policy", None)
        payload = {"user_id": request.session_id, "real_user_id": request.profile_user_id,
                   "character_pack_id": character_pack_id, "client_mode": "desktop_pet",
                   "_memory_desktop_ref": request.conversation_ref}
        private_lease = None
        if policy is not None and policy.resolve(payload).shared:
            # Old desktop journals must not replay discarded history into QQ.
            canonical_conversation_id += "_owner_private_v1"
            coordinator = getattr(self.engine, "turn_coordinator", None)
            if coordinator is None:
                return VoiceRealtimeCoordinatorResolution.failed("voice_private_coordinator_unavailable")
            with self._guard:
                existing = self._hosts.get(canonical_conversation_id)
                private_lease = getattr(existing, "private_lease", None) or PrivateVoiceLease(coordinator, character_pack_id)
            manager = PrivateVoiceMemory(manager, policy, payload, private_lease)
        try:
            host_result = self._resolve_host(
                canonical_conversation_id=canonical_conversation_id,
                manager=manager,
                profile_user_id=request.profile_user_id,
                session_id=request.session_id,
                character_pack_id=character_pack_id,
                tts_client=resolved_tts_client,
                conversation_ref=request.conversation_ref,
                private_lease=private_lease,
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
            private_lease=getattr(host_result, "private_lease", private_lease),
        )

    def _build_turn_coordinator(
        self,
        *,
        request: VoiceRealtimeOpenRequest,
        context: _VoiceRuntimeContext,
        voice_session_id: str,
        provider_session: NormalizedASRSession | None = None,
    ) -> VoiceRealtimeCoordinatorResolution:
        invalid = self._validate_open_request(request)
        if invalid is not None:
            return invalid
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
                    lambda voice_turn_id: self._release_owned_delivery_channel(
                        context.canonical_conversation_id,
                        voice_turn_id,
                        delivery_channel,
                    )
                ),
            )
            with self._guard:
                if self._closed:
                    return VoiceRealtimeCoordinatorResolution.failed("voice_runtime_service_closed", retryable=True)
                key = (context.canonical_conversation_id, request.voice_turn_id)
                if key in self._delivery_channels:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        "voice_realtime_turn_delivery_active", status="conflict",
                    )
                context.playback_executor.register_channel(request.voice_turn_id, delivery_channel)
                self._delivery_channels[key] = delivery_channel
        try:
            context.thinking_executor.register_turn(
                voice_turn_id=request.voice_turn_id,
                event_factory=event_factory,
                speech_delivery_enabled=delivery_channel is not None,
                delivery_notifier=(delivery_channel.notify_runtime_change if delivery_channel is not None else None),
                presentation_sink=(delivery_channel.stage_presentation if delivery_channel is not None else None),
            )
            coordinator_class = PrivateVoiceCoordinator if context.private_lease else VoiceASRRealtimeTurnCoordinator
            coordinator = coordinator_class(
                **({"private_lease": context.private_lease,
                    "private_admission": lambda source: self.engine.owner_private_policy.note_input({
                        "user_id": request.session_id, "real_user_id": request.profile_user_id,
                        "character_pack_id": context.character_pack_id, "client_mode": "desktop_pet",
                        "_memory_desktop_ref": request.conversation_ref}, source_id=source)
                    } if context.private_lease else {}),
                adapter=(None if provider_session is not None else context.provider.adapter),
                provider_session=provider_session,
                retain_provider_session=provider_session is not None,
                bridge=VoiceASRSessionBridge(
                    host=context.host,
                    event_factory=event_factory,
                    voice_turn_id=request.voice_turn_id,
                    audio_stream_id=request.audio_stream_id,
                    disposition=request.disposition,
                    supersede_prior_responses=True,
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
                self._discard_turn_channel(context, request, delivery_channel)
            invalid = self._validate_open_request(request)
            if invalid is not None:
                return invalid
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_coordinator_invalid",
                status="invalid_request",
            )
        with self._guard:
            if not self._closed:
                return VoiceRealtimeCoordinatorResolution.succeeded(
                    coordinator,
                    provider_id=str(getattr(context.provider, "provider_id", "") or ""),
                    voice_session_id=voice_session_id,
                    delivery_channel=delivery_channel,
                )
        if delivery_channel is not None:
            self._discard_turn_channel(context, request, delivery_channel)
        return VoiceRealtimeCoordinatorResolution.failed("voice_runtime_service_closed", retryable=True)

    def _discard_turn_channel(self, context: _VoiceRuntimeContext, request: VoiceRealtimeOpenRequest,
                              channel: VoicePlaybackDeliveryChannel) -> None:
        try:
            closed = channel.close(reason="voice_realtime_coordinator_invalid")
        except Exception:
            return
        if closed.ok:
            self._release_owned_delivery_channel(context.canonical_conversation_id, request.voice_turn_id, channel)
