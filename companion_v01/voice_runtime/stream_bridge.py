from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from voicecore import VoiceEvent

from .host import (
    AkaneVoiceRuntimeHost,
    VoiceHostDispatchResult,
    VoiceHostDriveResult,
)


@dataclass(frozen=True)
class VoiceTextArtifactResult:
    status: str
    artifact_ref: str = ""
    reason: str = ""
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "duplicate"} and bool(self.artifact_ref)

    @classmethod
    def succeeded(cls, artifact_ref: str) -> VoiceTextArtifactResult:
        return cls(status="succeeded", artifact_ref=artifact_ref)

    @classmethod
    def duplicate(cls, artifact_ref: str) -> VoiceTextArtifactResult:
        return cls(status="duplicate", artifact_ref=artifact_ref)

    @classmethod
    def failed(cls, reason: str, *, retryable: bool = False) -> VoiceTextArtifactResult:
        return cls(status="failed", reason=reason, retryable=retryable)


class VoiceTextArtifactPort(Protocol):
    def put_text(
        self,
        *,
        artifact_key: str,
        text: str,
        media_type: str,
    ) -> VoiceTextArtifactResult: ...


class VoiceStreamEventFactory(Protocol):
    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent: ...


@dataclass(frozen=True)
class VoiceStreamBridgeResult:
    status: str
    reason: str
    source_event_type: str
    dispatch_result: VoiceHostDispatchResult | None = None
    drive_result: VoiceHostDriveResult | None = None
    artifact_result: VoiceTextArtifactResult | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


class VoiceResponseStreamBridge:
    """Translate normalized LLM stream events into one VoiceCore response.

    MemCore's streaming parser remains the only sentence-boundary authority.
    ``speech_chunk`` is intentionally ignored here; complete
    ``speech_segment`` events become delivery units, while ``final`` carries
    the one authoritative assistant body used by the memory projection.
    """

    _IGNORED_EVENT_TYPES = {
        "delivery_hint",
        "final_ui",
        "metadata_ready",
        "speech_chunk",
        "ui",
    }

    def __init__(
        self,
        *,
        host: AkaneVoiceRuntimeHost,
        response_id: str,
        text_artifacts: VoiceTextArtifactPort,
        event_factory: VoiceStreamEventFactory,
    ) -> None:
        self.host = host
        self.response_id = str(response_id or "")
        self.text_artifacts = text_artifacts
        self.event_factory = event_factory
        self._segments: dict[int, str] = {}
        self._next_segment_index = 0
        self._final_speech: str | None = None
        self._final_memory_metadata: dict[str, Any] | None = None

    def accept_stream_event(self, stream_event: Mapping[str, Any]) -> VoiceStreamBridgeResult:
        if not isinstance(stream_event, Mapping):
            return self._failed("", "stream_event_must_be_object")
        event_type = str(stream_event.get("type") or "")
        if event_type == "speech_segment":
            return self._accept_speech_segment(stream_event)
        if event_type == "final":
            return self._accept_final(stream_event)
        if event_type in self._IGNORED_EVENT_TYPES:
            return VoiceStreamBridgeResult(
                status="ignored",
                reason="stream_event_not_actionable",
                source_event_type=event_type,
            )
        return self._failed(event_type, "unsupported_stream_event")

    def _accept_speech_segment(self, stream_event: Mapping[str, Any]) -> VoiceStreamBridgeResult:
        event_type = "speech_segment"
        if self._final_speech is not None:
            return self._failed(event_type, "response_stream_already_finalized")
        index = stream_event.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return self._failed(event_type, "speech_segment_index_invalid")
        text = stream_event.get("text")
        if not isinstance(text, str) or not text:
            return self._failed(event_type, "speech_segment_text_invalid")
        if index < self._next_segment_index:
            if self._segments.get(index) == text:
                return VoiceStreamBridgeResult(
                    status="duplicate",
                    reason="speech_segment_already_accepted",
                    source_event_type=event_type,
                )
            return self._failed(event_type, "speech_segment_duplicate_conflict")
        if index != self._next_segment_index:
            return self._failed(event_type, "speech_segment_out_of_order")

        response = self.host.snapshot.responses.get(self.response_id)
        if response is None:
            return self._failed(event_type, "voice_response_missing")
        if response.state.value not in {"generating", "streaming"}:
            return self._failed(event_type, "voice_response_not_streaming")

        unit_key = self._unit_key(index)
        artifact_result = self._put_text_artifact(unit_key=unit_key, text=text)
        if not artifact_result.ok:
            return VoiceStreamBridgeResult(
                status="failed",
                reason=artifact_result.reason or "text_artifact_write_failed",
                source_event_type=event_type,
                artifact_result=artifact_result,
            )

        try:
            event = self.event_factory.make(
                "voice.speech_unit.declared",
                voice_turn_id=response.voice_turn_id,
                response_id=response.response_id,
                speech_unit_id=f"speech_{unit_key}",
                turn_revision=response.source_turn_revision,
                response_generation=response.response_generation,
                payload={
                    "ordinal": index,
                    "purpose": response.purpose,
                    "text_artifact_ref": artifact_result.artifact_ref,
                },
            )
        except Exception:
            return VoiceStreamBridgeResult(
                status="failed",
                reason="voice_event_build_failed",
                source_event_type=event_type,
                artifact_result=artifact_result,
            )

        dispatch = self.host.accept_event(event)
        if not dispatch.accepted:
            return VoiceStreamBridgeResult(
                status="failed",
                reason=dispatch.reason or "speech_unit_dispatch_failed",
                source_event_type=event_type,
                dispatch_result=dispatch,
                artifact_result=artifact_result,
            )

        self._segments[index] = text
        self._next_segment_index += 1
        drive = self._drive_one_layer()
        if drive is not None and drive.status == "failed":
            return VoiceStreamBridgeResult(
                status="failed",
                reason=drive.reason or "speech_unit_drive_failed",
                source_event_type=event_type,
                dispatch_result=dispatch,
                drive_result=drive,
                artifact_result=artifact_result,
            )
        return VoiceStreamBridgeResult(
            status="accepted",
            reason="",
            source_event_type=event_type,
            dispatch_result=dispatch,
            drive_result=drive,
            artifact_result=artifact_result,
        )

    def _accept_final(self, stream_event: Mapping[str, Any]) -> VoiceStreamBridgeResult:
        event_type = "final"
        payload = stream_event.get("payload")
        if not isinstance(payload, Mapping):
            return self._failed(event_type, "final_payload_must_be_object")
        speech = payload.get("speech")
        if not isinstance(speech, str):
            return self._failed(event_type, "final_speech_must_be_string")
        memory_metadata = payload.get("memory_metadata")
        if memory_metadata is not None and not isinstance(memory_metadata, Mapping):
            return self._failed(event_type, "final_memory_metadata_must_be_object")
        normalized_metadata = dict(memory_metadata) if memory_metadata is not None else None
        if self._final_speech is not None:
            if self._final_speech == speech and self._final_memory_metadata == normalized_metadata:
                return VoiceStreamBridgeResult(
                    status="duplicate",
                    reason="response_final_already_accepted",
                    source_event_type=event_type,
                )
            return self._failed(event_type, "response_final_conflict")

        response = self.host.snapshot.responses.get(self.response_id)
        if response is None:
            return self._failed(event_type, "voice_response_missing")
        if response.state.value not in {"generating", "streaming"}:
            return self._failed(event_type, "voice_response_not_streaming")

        event_payload: dict[str, Any] = {
            "full_text": speech,
            "full_text_status": "available",
        }
        if normalized_metadata is not None:
            event_payload["memory_metadata"] = normalized_metadata
        try:
            event = self.event_factory.make(
                "voice.response.generation_completed",
                voice_turn_id=response.voice_turn_id,
                response_id=response.response_id,
                turn_revision=response.source_turn_revision,
                response_generation=response.response_generation,
                payload=event_payload,
            )
        except Exception:
            return self._failed(event_type, "voice_event_build_failed")

        dispatch = self.host.accept_event(event)
        if not dispatch.accepted:
            return VoiceStreamBridgeResult(
                status="failed",
                reason=dispatch.reason or "generation_completed_dispatch_failed",
                source_event_type=event_type,
                dispatch_result=dispatch,
            )
        self._final_speech = speech
        self._final_memory_metadata = normalized_metadata
        drive = self._drive_one_layer()
        if drive is not None and drive.status == "failed":
            return VoiceStreamBridgeResult(
                status="failed",
                reason=drive.reason or "generation_completed_drive_failed",
                source_event_type=event_type,
                dispatch_result=dispatch,
                drive_result=drive,
            )
        return VoiceStreamBridgeResult(
            status="accepted",
            reason="",
            source_event_type=event_type,
            dispatch_result=dispatch,
            drive_result=drive,
        )

    def _put_text_artifact(self, *, unit_key: str, text: str) -> VoiceTextArtifactResult:
        try:
            result = self.text_artifacts.put_text(
                artifact_key=f"voice-text:{unit_key}",
                text=text,
                media_type="text/plain; charset=utf-8",
            )
        except Exception:
            return VoiceTextArtifactResult.failed("text_artifact_write_failed", retryable=True)
        if not isinstance(result, VoiceTextArtifactResult):
            return VoiceTextArtifactResult.failed("invalid_text_artifact_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceTextArtifactResult.failed("invalid_text_artifact_status")
        if result.status in {"succeeded", "duplicate"} and not result.artifact_ref:
            return VoiceTextArtifactResult.failed("text_artifact_ref_missing")
        return result

    def _drive_one_layer(self) -> VoiceHostDriveResult | None:
        if not self.host.snapshot.pending_commands:
            return None
        return self.host.drive_once()

    def _unit_key(self, index: int) -> str:
        response = self.host.snapshot.responses[self.response_id]
        digest = sha256(
            (
                f"{self.host.snapshot.conversation_id}:"
                f"{self.host.snapshot.conversation_generation}:"
                f"{response.response_id}:{response.response_generation}:{index}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        return digest

    @staticmethod
    def _failed(event_type: str, reason: str) -> VoiceStreamBridgeResult:
        return VoiceStreamBridgeResult(
            status="failed",
            reason=reason,
            source_event_type=event_type,
        )
