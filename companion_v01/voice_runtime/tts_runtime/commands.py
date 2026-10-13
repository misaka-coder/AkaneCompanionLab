"""Command routing, artifact ports and TTS command lineage."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..durable_ports import VoiceAudioArtifactReadResult, VoiceAudioArtifactResult, VoiceTextArtifactReadResult
from ..host import VoiceCommandExecutionResult, VoiceCommandExecutor

logger = logging.getLogger("akane.voice_tts")


@dataclass
class _TTSJob:
    command: dict[str, Any]
    snapshot: dict[str, Any]
    result: VoiceCommandExecutionResult | None = None
    started: bool = False
    receipt_settled: bool = False

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


class TTSCommandContext:
    @staticmethod
    def _make_job(
        command: dict[str, Any], common: Mapping[str, Any], result: VoiceCommandExecutionResult | None = None,
    ) -> _TTSJob:
        # Retain this unit's lineage, rather than the full conversation snapshot.
        return _TTSJob(
            command=command, result=result,
            snapshot={
                "responses": {
                    str(common["response_id"]): {
                        "voice_turn_id": common["voice_turn_id"],
                        "source_turn_revision": common["turn_revision"],
                        "response_generation": common["response_generation"],
                    },
                },
                "input_turns": {
                    str(common["voice_turn_id"]): {"voice_session_id": common["voice_session_id"]},
                },
            },
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
