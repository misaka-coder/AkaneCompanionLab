from __future__ import annotations

import asyncio
import hashlib
import inspect
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from voicecore import VoiceEvent

from .durable_ports import (
    VoiceAudioArtifactReadResult,
    VoiceAudioArtifactResult,
    VoiceTextArtifactReadResult,
)
from .host import VoiceCommandExecutionResult, VoiceCommandExecutor


class VoiceReadableTextArtifactPort(Protocol):
    def read_text(self, artifact_ref: str) -> VoiceTextArtifactReadResult: ...


class VoiceAudioArtifactPort(Protocol):
    def put_audio(
        self,
        *,
        artifact_key: str,
        audio: bytes,
        media_type: str,
    ) -> VoiceAudioArtifactResult: ...

    def read_audio(self, artifact_ref: str) -> VoiceAudioArtifactReadResult: ...


class VoiceCommandRouterExecutor:
    """Route provider-neutral VoiceCore commands to one effect authority."""

    def __init__(self, routes: Mapping[str, VoiceCommandExecutor]) -> None:
        normalized: dict[str, VoiceCommandExecutor] = {}
        for command_kind, executor in routes.items():
            kind = str(command_kind or "").strip()
            if not kind or executor is None or kind in normalized:
                raise ValueError("voice_command_route_invalid")
            normalized[kind] = executor
        if not normalized:
            raise ValueError("voice_command_routes_required")
        self._routes = normalized

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        executor = self._resolve(command_record)
        if executor is None:
            return VoiceCommandExecutionResult.deferred(self._unsupported_reason(command_record))
        return executor.execute(command_record, snapshot_record)

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        executor = self._resolve(command_record)
        if executor is None:
            return VoiceCommandExecutionResult.deferred(self._unsupported_reason(command_record))
        recover = getattr(executor, "recover", None)
        if not callable(recover):
            return VoiceCommandExecutionResult(
                status="deferred",
                reason="voice_command_recovery_unavailable",
                retryable=True,
                outcome_known=False,
            )
        return recover(command_record, snapshot_record)

    def _resolve(
        self,
        command_record: Mapping[str, Any],
    ) -> VoiceCommandExecutor | None:
        if not isinstance(command_record, Mapping):
            return None
        return self._routes.get(str(command_record.get("command_kind") or ""))

    @staticmethod
    def _unsupported_reason(command_record: Mapping[str, Any]) -> str:
        kind = (
            str(command_record.get("command_kind") or "unknown") if isinstance(command_record, Mapping) else "unknown"
        )
        return f"voice_command_not_connected:{kind}"


class AkaneVoiceTTSCommandExecutor:
    """Execute VoiceCore ``start_tts`` against a real TTS client.

    Synthesized bytes become one immutable, path-free artifact before
    ``voice.tts.ready`` is emitted. This executor never emits playback facts;
    a client acknowledgement remains the only delivery authority.
    """

    def __init__(
        self,
        *,
        tts_client: Any,
        text_artifacts: VoiceReadableTextArtifactPort,
        audio_artifacts: VoiceAudioArtifactPort,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        self.tts_client = tts_client
        self.text_artifacts = text_artifacts
        self.audio_artifacts = audio_artifacts
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, payload, common = context
        text_result = self.text_artifacts.read_text(str(payload.get("text_artifact_ref") or ""))
        if not text_result.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_text_artifact_unavailable",
                started=False,
            )
        if self.tts_client is None or not callable(getattr(self.tts_client, "synthesize", None)):
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_provider_unavailable",
                started=False,
            )

        try:
            synthesized = _resolve_awaitable(self.tts_client.synthesize(text_result.text))
            audio, media_type = _coerce_synthesized_audio(synthesized)
        except Exception:
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_synthesis_failed",
                started=True,
            )

        artifact_key = self._artifact_key(command)
        stored = self.audio_artifacts.put_audio(
            artifact_key=artifact_key,
            audio=audio,
            media_type=media_type,
        )
        if not stored.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason=(
                    stored.reason
                    if stored.reason.startswith("voice_audio_artifact_")
                    else "voice_audio_artifact_write_failed"
                ),
                started=True,
            )
        return VoiceCommandExecutionResult.succeeded(
            self._event(
                command=command,
                common=common,
                stage="started",
                event_kind="voice.tts.started",
                payload={},
            ),
            self._event(
                command=command,
                common=common,
                stage="ready",
                event_kind="voice.tts.ready",
                payload={
                    "command_id": str(command["command_id"]),
                    "audio_artifact_ref": stored.artifact_ref,
                },
            ),
        )

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, _payload, common = context
        artifact_ref = self._artifact_ref(command)
        recovered = self.audio_artifacts.read_audio(artifact_ref)
        if not recovered.ok:
            return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
        return VoiceCommandExecutionResult.succeeded(
            self._event(
                command=command,
                common=common,
                stage="started",
                event_kind="voice.tts.started",
                payload={},
            ),
            self._event(
                command=command,
                common=common,
                stage="ready",
                event_kind="voice.tts.ready",
                payload={
                    "command_id": str(command["command_id"]),
                    "audio_artifact_ref": artifact_ref,
                },
            ),
        )

    def _command_context(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_tts_command_contract_invalid"
        command = dict(command_record)
        payload_value = command.get("payload")
        if (
            command.get("command_kind") != "start_tts"
            or not isinstance(payload_value, Mapping)
            or not str(command.get("command_id") or "")
            or not str(command.get("idempotency_key") or "")
        ):
            return "voice_tts_command_invalid"
        payload = dict(payload_value)
        response_id = str(payload.get("response_id") or "")
        speech_unit_id = str(payload.get("speech_unit_id") or "")
        text_artifact_ref = str(payload.get("text_artifact_ref") or "")
        response_generation = payload.get("response_generation")
        responses = snapshot_record.get("responses")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        if (
            not response_id
            or not speech_unit_id
            or not text_artifact_ref
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
            or not isinstance(response, Mapping)
        ):
            return "voice_tts_command_invalid"
        voice_turn_id = str(response.get("voice_turn_id") or "")
        turn_revision = response.get("source_turn_revision")
        input_turns = snapshot_record.get("input_turns")
        input_turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        voice_session_id = str(input_turn.get("voice_session_id") or "") if isinstance(input_turn, Mapping) else ""
        if (
            not voice_turn_id
            or not voice_session_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
        ):
            return "voice_tts_snapshot_context_invalid"
        return (
            command,
            payload,
            {
                "voice_turn_id": voice_turn_id,
                "voice_session_id": voice_session_id,
                "response_id": response_id,
                "speech_unit_id": speech_unit_id,
                "turn_revision": turn_revision,
                "response_generation": response_generation,
            },
        )

    def _failed_observation(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        reason: str,
        started: bool,
    ) -> VoiceCommandExecutionResult:
        observations = []
        if started:
            observations.append(
                self._event(
                    command=command,
                    common=common,
                    stage="started",
                    event_kind="voice.tts.started",
                    payload={},
                )
            )
        observations.append(
            self._event(
                command=command,
                common=common,
                stage="failed",
                event_kind="voice.tts.failed",
                payload={
                    "command_id": str(command["command_id"]),
                    "reason_code": str(reason or "voice_tts_failed")[:128],
                },
            )
        )
        return VoiceCommandExecutionResult.succeeded(*observations)

    def _event(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        stage: str,
        event_kind: str,
        payload: Mapping[str, Any],
    ) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=self._event_id(command, stage),
            event_kind=event_kind,
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=str(common["voice_session_id"]),
            conversation_generation=self.conversation_generation,
            producer="akane.voice_tts",
            voice_turn_id=str(common["voice_turn_id"]),
            response_id=str(common["response_id"]),
            speech_unit_id=str(common["speech_unit_id"]),
            turn_revision=int(common["turn_revision"]),
            response_generation=int(common["response_generation"]),
            causation_id=str(command.get("command_id") or ""),
            payload=dict(payload),
        )

    @staticmethod
    def _artifact_key(command: Mapping[str, Any]) -> str:
        return f"voice-tts:{str(command.get('idempotency_key') or '')}"

    @classmethod
    def _artifact_ref(cls, command: Mapping[str, Any]) -> str:
        digest = hashlib.sha256(cls._artifact_key(command).encode("utf-8")).hexdigest()
        return f"voice-audio:{digest}"

    @staticmethod
    def _event_id(command: Mapping[str, Any], stage: str) -> str:
        material = f"{str(command.get('command_id') or '')}:{stage}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"voice_evt_{digest[:32]}"


def _coerce_synthesized_audio(value: Any) -> tuple[bytes, str]:
    if isinstance(value, bytes):
        audio = value
        media_type = "audio/mpeg"
    else:
        audio = getattr(value, "audio", None)
        media_type = getattr(value, "media_type", None)
    if not isinstance(audio, bytes) or not audio:
        raise ValueError("voice_tts_audio_invalid")
    normalized_media_type = str(media_type or "audio/mpeg").strip().lower()
    return audio, normalized_media_type


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(value))
        except BaseException as exc:  # preserved and reraised on the caller
            failure.append(exc)

    worker = threading.Thread(
        target=run,
        name=f"voice-tts-await-{uuid.uuid4().hex[:8]}",
        daemon=True,
    )
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    if not result:
        raise RuntimeError("voice_tts_awaitable_no_result")
    return result[0]
