"""Synchronous durable generation and cancellation observations."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..host import VoiceCommandExecutionResult
from .context import _GenerationJob


class ThinkingCommands:
    """Generation commands share the owning job registry and cancellation fence."""

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return self._execute_or_recover(
            command_record=command_record,
            snapshot_record=snapshot_record,
        )

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return self._execute_or_recover(
            command_record=command_record,
            snapshot_record=snapshot_record,
        )

    def _execute_or_recover(
        self,
        *,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        with self._guard:
            if self._closed:
                return VoiceCommandExecutionResult.failed("voice_thinking_executor_closed", retryable=True)
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return VoiceCommandExecutionResult.failed("voice_command_contract_invalid")
        command = dict(command_record)
        payload = command.get("payload")
        if not isinstance(payload, Mapping):
            return VoiceCommandExecutionResult.failed("voice_command_payload_invalid")
        command_kind = str(command.get("command_kind") or "")
        if command_kind == "cancel_response_generation":
            return self._cancel_generation(
                command=command,
                payload=payload,
                snapshot_record=snapshot_record,
            )
        if command_kind != "start_response_generation":
            return VoiceCommandExecutionResult.deferred(f"voice_command_not_connected:{command_kind or 'unknown'}")

        command_id = str(command.get("command_id") or "")
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        turn_revision = payload.get("turn_revision")
        if (
            not command_id
            or not voice_turn_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
            or turn_revision < 1
        ):
            return VoiceCommandExecutionResult.failed("voice_generation_command_invalid")
        event_factory = self._event_factory_for_turn(voice_turn_id)
        response_id = str(payload.get("response_id") or "").strip()
        if not response_id:
            commitment = str(payload.get("commitment") or "committed")
            candidate_id = str(payload.get("candidate_id") or "").strip()
            if commitment == "speculative" and not candidate_id:
                return VoiceCommandExecutionResult.failed("voice_candidate_id_missing")
            response_id = self._response_id(command)
            return VoiceCommandExecutionResult.succeeded(
                event_factory.make(
                    "voice.response.created",
                    voice_turn_id=voice_turn_id,
                    response_id=response_id,
                    turn_revision=turn_revision,
                    response_generation=1,
                    payload={
                        "purpose": str(payload.get("purpose") or "content"),
                        "commitment": commitment,
                        **({"candidate_id": candidate_id, "playable": False} if candidate_id else {}),
                        "command_id": command_id,
                    },
                )
            )

        response_generation = payload.get("response_generation")
        if isinstance(response_generation, bool) or not isinstance(response_generation, int) or response_generation < 1:
            return VoiceCommandExecutionResult.failed("voice_generation_command_invalid")
        commitment = str(payload.get("commitment") or "committed")
        candidate_id = str(payload.get("candidate_id") or "").strip()
        resolved = (
            self._resolve_candidate_turn_context(
                snapshot_record=snapshot_record,
                voice_turn_id=voice_turn_id,
                turn_revision=turn_revision,
            )
            if commitment == "speculative"
            else self._resolve_turn_context(
                snapshot_record=snapshot_record,
                voice_turn_id=voice_turn_id,
            )
        )
        if not resolved.get("ok"):
            return VoiceCommandExecutionResult.succeeded(
                event_factory.make(
                    "voice.response.failed",
                    voice_turn_id=voice_turn_id,
                    response_id=response_id,
                    turn_revision=turn_revision,
                    response_generation=response_generation,
                    payload={
                        "stage": "thinking_agent",
                        "reason_code": str(resolved.get("reason") or "voice_thinking_turn_context_unavailable"),
                        "retryable": str(resolved.get("status") or "")
                        in {
                            "failed",
                            "unavailable",
                        },
                        "safe_public_summary": "语音内容已经收到，但回复上下文暂时无法恢复。",
                        "affected_ids": [voice_turn_id, response_id],
                        "command_id": command_id,
                    },
                )
            )
        job = _GenerationJob(
            response_id=response_id,
            voice_turn_id=voice_turn_id,
            response_generation=response_generation,
            turn_revision=turn_revision,
            commitment=commitment,
            candidate_id=candidate_id,
            source_id=str(resolved["source_id"]),
            memcore_turn_id=str(resolved["turn_id"]),
            message=str(resolved["text"]),
            timestamp=int(resolved.get("timestamp") or 0),
            event_factory=event_factory,
            speech_delivery_enabled=self._speech_delivery_enabled(voice_turn_id),
            delivery_notifier=self._delivery_notifier(voice_turn_id),
            presentation_sink=self._presentation_sinks.get(voice_turn_id),
        )
        with self._guard:
            if self._closed:
                return VoiceCommandExecutionResult.failed("voice_thinking_executor_closed", retryable=True)
            existing = self._jobs.get(response_id)
            if existing is not None and existing != job:
                return VoiceCommandExecutionResult.failed("voice_generation_job_conflict")
            self._jobs[response_id] = job
        return VoiceCommandExecutionResult.succeeded(
            event_factory.make(
                "voice.response.generation_started",
                voice_turn_id=voice_turn_id,
                response_id=response_id,
                turn_revision=turn_revision,
                response_generation=response_generation,
                payload={"command_id": command_id},
            )
        )

    def _cancel_generation(
        self,
        *,
        command: Mapping[str, Any],
        payload: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        command_id = str(command.get("command_id") or "").strip()
        response_id = str(payload.get("response_id") or "").strip()
        response_generation = payload.get("response_generation")
        responses = snapshot_record.get("responses")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        if (
            not command_id
            or not response_id
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
            or response_generation < 1
            or not isinstance(response, Mapping)
        ):
            return VoiceCommandExecutionResult.failed("voice_generation_cancel_command_invalid")
        voice_turn_id = str(response.get("voice_turn_id") or "").strip()
        turn_revision = response.get("source_turn_revision")
        if (
            not voice_turn_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
            or turn_revision < 1
            or int(response.get("response_generation") or 0) != response_generation
        ):
            return VoiceCommandExecutionResult.failed("voice_generation_cancel_target_invalid")
        try:
            event_factory = self._event_factory_for_turn(voice_turn_id)
        except RuntimeError:
            return VoiceCommandExecutionResult.failed(
                "voice_generation_cancel_context_unavailable",
                retryable=True,
            )
        with self._guard:
            self._cancel_requested_response_ids.add(response_id)
        return VoiceCommandExecutionResult.succeeded(
            event_factory.make(
                "voice.response.generation_cancelled",
                voice_turn_id=voice_turn_id,
                response_id=response_id,
                turn_revision=turn_revision,
                response_generation=response_generation,
                payload={"command_id": command_id},
            )
        )

    def _generation_cancel_requested(self, response_id: str) -> bool:
        with self._guard:
            return self._closed or response_id in self._cancel_requested_response_ids
