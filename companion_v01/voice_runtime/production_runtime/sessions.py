"""Call identity, shared turn context and one continuous ASR provider session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from capcore_adapter_speech import ASRSessionUpdate, NormalizedASRSession

from ..asr_realtime_turn import VoiceASRRealtimeTurnCoordinator
from ..durable_ports import FileVoiceAudioArtifactPort, FileVoiceTextArtifactPort
from ..playback_delivery import AkaneVoicePlaybackCommandExecutor
from ..realtime_protocol import VoiceRealtimeCoordinatorResolution, VoiceRealtimeOpenRequest
from ..thinking_agent import AkaneThinkingAgentCommandExecutor
from .assembly import _SerializedVoiceRuntimeHost

if TYPE_CHECKING:
    from .service import AkaneVoiceRuntimeService


@dataclass
class _PendingASRCancellation:
    session: Any
    loop: Any
    future: Any = None

    async def cancel(self) -> bool:
        try:
            result = await self.session.cancel()
        except Exception:
            return False
        return result is None or getattr(result, "status", "") in {"cancelled", "duplicate"}


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
    private_lease: Any = None


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
            "conversation_ref",
        )
        return all(getattr(request, field) == getattr(self.open_request, field) for field in fields)
