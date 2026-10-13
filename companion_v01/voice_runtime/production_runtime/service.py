"""One BotRuntime's voice service, caches and background-work ownership."""
from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from capcore_adapter_speech import ASRSessionOpenResult

from ...background_tasks import BackgroundTaskRunner
from ..asr_provider import build_voice_asr_provider
from ..candidate import AkaneVoiceCandidateValidationCommandExecutor
from ..durable_ports import FileVoiceAudioArtifactPort, FileVoiceTextArtifactPort
from ..playback_delivery import AkaneVoicePlaybackCommandExecutor, VoicePlaybackDeliveryChannel
from ..realtime_protocol import VoiceRealtimeCoordinatorResolution, VoiceRealtimeOpenRequest
from ..semantic_pulse import AkaneVoiceSemanticPulseCommandExecutor
from ..thinking_agent import AkaneThinkingAgentCommandExecutor
from ..tts_executor import AkaneVoiceTTSCommandExecutor
from . import sessions as _sessions
from .assembly import RuntimeAssembly, _SerializedVoiceRuntimeHost
from .coordination import RuntimeCoordination
from .events import _safe_reason
from .lifecycle import RuntimeLifecycle, _RuntimeResources
from .recovery import RuntimeRecovery
from .sessions import AkaneVoiceRuntimeCall, VoiceRuntimeCallOpenResult, _PendingASRCancellation


class AkaneVoiceRuntimeService(RuntimeCoordination, RuntimeAssembly, RuntimeRecovery, RuntimeLifecycle):
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
        self._tts_executors: dict[str, AkaneVoiceTTSCommandExecutor] = {}
        self._text_artifacts: dict[str, FileVoiceTextArtifactPort] = {}
        self._audio_artifacts: dict[str, FileVoiceAudioArtifactPort] = {}
        self._delivery_channels: dict[
            tuple[str, str],
            VoicePlaybackDeliveryChannel,
        ] = {}
        self._background_tasks = BackgroundTaskRunner(default_workers=1)
        self._guard = threading.RLock()
        self._closed = False
        self._shutdown_guard = threading.Lock()
        self._shutdown_resources: _RuntimeResources | None = None
        self._shutdown_status: dict[str, Any] | None = None
        self._shutdown_host_count = 0
        self._initializing_resources: dict[str, _RuntimeResources] = {}
        self._opening_calls = 0
        self._unclaimed_asr_sessions: dict[int, _PendingASRCancellation] = {}

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
        with self._guard:
            invalid = self._validate_open_request(request)
            if invalid is not None:
                return self._call_failure(invalid)
            self._opening_calls += 1
        try:
            try:
                opened = await adapter.open_session(
                    filename="akane_voice_input.pcm",
                    content_type="audio/pcm",
                    language=request.language,
                )
            except Exception:
                return VoiceRuntimeCallOpenResult.failed(
                    "asr_provider_open_failed", retryable=True,
                )
            if not isinstance(opened, ASRSessionOpenResult) or not opened.ok or opened.session is None:
                return VoiceRuntimeCallOpenResult.failed(
                    _safe_reason(getattr(opened, "reason", ""), fallback="asr_provider_open_failed"),
                    status="unavailable",
                    retryable=bool(getattr(opened, "retryable", False)),
                    safe_public_summary=str(getattr(opened, "safe_public_summary", "") or ""),
                )
            with self._guard:
                closed = self._closed
                if not closed and bool(getattr(opened.session, "supports_turn_commit", False)):
                    return VoiceRuntimeCallOpenResult.succeeded(
                        AkaneVoiceRuntimeCall(
                            service=self,
                            open_request=request,
                            context=context,
                            provider_session=opened.session,
                            voice_session_id=f"voice_session_{uuid.uuid4().hex}",
                        )
                    )
            cancelled = await self._cancel_unclaimed_session(opened.session)
            if not cancelled:
                return VoiceRuntimeCallOpenResult.failed("asr_provider_cancel_failed", retryable=True)
            if closed:
                return VoiceRuntimeCallOpenResult.failed("voice_runtime_service_closed", retryable=True)
            return VoiceRuntimeCallOpenResult.failed(
                "asr_provider_commit_turn_unsupported", status="unsupported",
                safe_public_summary="当前语音识别服务不支持连续多轮通话。",
            )
        finally:
            with self._guard:
                self._opening_calls -= 1

    async def _cancel_unclaimed_session(self, session: Any) -> bool:
        pending = _PendingASRCancellation(session, asyncio.get_running_loop())
        with self._guard:
            self._unclaimed_asr_sessions[id(session)] = pending
        cancelled = await pending.cancel()
        if cancelled:
            with self._guard:
                self._unclaimed_asr_sessions.pop(id(session), None)
        return cancelled

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


# Resolve the call owner forward reference after both classes exist.
_sessions.AkaneVoiceRuntimeService = AkaneVoiceRuntimeService
