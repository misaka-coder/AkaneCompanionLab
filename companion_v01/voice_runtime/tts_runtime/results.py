"""Durable audio recovery and deterministic TTS observations."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from voicecore import VoiceEvent

from ..host import VoiceCommandExecutionResult


class TTSResults:
    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        command_id = str(command_record.get("command_id") or "") if isinstance(command_record, Mapping) else ""
        with self._guard:
            job = self._jobs.get(command_id)
            if job is not None:
                if job.command != command_record:
                    return VoiceCommandExecutionResult.failed("voice_tts_job_conflict")
                if job.result is None or job.result.in_progress or job.result.outcome_known:
                    return job.result or VoiceCommandExecutionResult.running("voice_tts_running")
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, _payload, common = context
        artifact_ref = self._artifact_ref(command)
        try:
            recovered = self.audio_artifacts.read_audio(artifact_ref)
        except Exception:
            return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
        if not recovered.ok:
            return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
        origin = getattr(recovered, "origin", {})
        result = self._audio_result(command=command, common=common, artifact_ref=artifact_ref, origin=origin)
        with self._guard:
            if job is not None and self._jobs.get(command_id) is job:
                if job.result is not None and job.result.outcome_known:
                    return job.result
                job.result = result
        return result

    def _audio_result(
        self, *, command: Mapping[str, Any], common: Mapping[str, Any], artifact_ref: str,
        origin: Mapping[str, str] | None,
    ) -> VoiceCommandExecutionResult:
        with self._guard:
            if self._command_cancelled(str(command["command_id"])):
                return self._failed_observation(
                    command=command, common=common,
                    reason="voice_tts_executor_closed" if self._closed else "invocation_cancelled",
                    started=True, origin=origin,
                )
            return self._ready_observations(command=command, common=common, artifact_ref=artifact_ref, origin=origin)

    def _ready_observations(
        self, *, command: Mapping[str, Any], common: Mapping[str, Any], artifact_ref: str,
        origin: Mapping[str, str] | None,
    ) -> VoiceCommandExecutionResult:
        return VoiceCommandExecutionResult.succeeded(
            self._event(
                command=command,
                common=common,
                stage="started",
                event_kind="voice.tts.started",
                payload=self._provider_payload(origin),
            ),
            self._event(
                command=command,
                common=common,
                stage="ready",
                event_kind="voice.tts.ready",
                payload={
                    "command_id": str(command["command_id"]),
                    "audio_artifact_ref": artifact_ref,
                    **self._provider_payload(origin),
                },
            ),
        )

    def _failed_observation(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        reason: str,
        started: bool,
        origin: Mapping[str, str] | None = None,
    ) -> VoiceCommandExecutionResult:
        observations = []
        if started:
            observations.append(
                self._event(
                    command=command,
                    common=common,
                    stage="started",
                    event_kind="voice.tts.started",
                    payload=self._provider_payload(origin),
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
                    **self._provider_payload(origin),
                },
            )
        )
        return VoiceCommandExecutionResult.succeeded(*observations)

    def _provider_payload(self, origin: Mapping[str, str] | None = None) -> dict[str, str]:
        if origin is not None:
            return ({"provider_id": origin["plugin_id"], "provider_generation": origin.get("generation_id", "")}
                    if origin.get("plugin_id") else {})
        return {"provider_id": self.provider_id} if self.provider_id else {}

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
