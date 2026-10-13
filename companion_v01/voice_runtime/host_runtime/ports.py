"""Typed effect port validation and structured failure boundaries."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from voicecore import VoiceCommand, VoiceEvent, voice_command_to_dict, voice_event_to_dict

from .contracts import VoiceCommandReceiptLoadResult, VoiceCommandReceiptRecord, VoiceHostPortResult, VoiceProjectionOutboxLoadResult


class HostPorts:
    """Typed effect port validation and structured failure boundaries."""

    def _append_transition(
        self,
        event_record: Mapping[str, Any],
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        try:
            result = self.journal.append_transition(event_record, projection_records)
        except Exception:
            return VoiceHostPortResult.failed("journal_append_failed", retryable=True)
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_journal_result",
            invalid_status_reason="invalid_journal_status",
        )

    def _begin_command(self, command: VoiceCommand) -> VoiceHostPortResult:
        try:
            result = self.journal.begin_command(voice_command_to_dict(command))
        except Exception:
            return VoiceHostPortResult.failed("command_receipt_begin_failed", retryable=True)
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_command_receipt_begin_result",
            invalid_status_reason="invalid_command_receipt_begin_status",
        )

    def _store_command_observations(
        self,
        command: VoiceCommand,
        observations: tuple[VoiceEvent, ...],
    ) -> VoiceHostPortResult:
        try:
            observation_records = tuple(voice_event_to_dict(observation) for observation in observations)
        except (TypeError, ValueError):
            return VoiceHostPortResult.failed("command_observation_not_serializable")
        try:
            result = self.journal.store_command_observations(
                command.command_id,
                observation_records,
            )
        except Exception:
            return VoiceHostPortResult.failed(
                "command_observation_store_failed",
                retryable=True,
            )
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_command_observation_store_result",
            invalid_status_reason="invalid_command_observation_store_status",
        )

    def _release_command(self, command_id: str) -> VoiceHostPortResult:
        try:
            result = self.journal.release_command(command_id)
        except Exception:
            return VoiceHostPortResult.failed("command_receipt_release_failed", retryable=True)
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_command_receipt_release_result",
            invalid_status_reason="invalid_command_receipt_release_status",
        )

    def _load_unfinished_commands(self) -> VoiceCommandReceiptLoadResult:
        try:
            result = self.journal.load_unfinished_commands()
        except Exception:
            return VoiceCommandReceiptLoadResult.failed(
                "command_receipt_load_failed",
                retryable=True,
            )
        if not isinstance(result, VoiceCommandReceiptLoadResult):
            return VoiceCommandReceiptLoadResult.failed("invalid_command_receipt_load_result")
        if result.status not in {"succeeded", "failed"}:
            return VoiceCommandReceiptLoadResult.failed("invalid_command_receipt_load_status")
        if result.status == "succeeded" and any(
            not isinstance(record, VoiceCommandReceiptRecord) for record in result.records
        ):
            return VoiceCommandReceiptLoadResult.failed("invalid_command_receipt_record")
        return result

    def _mark_command_completed(self, command_id: str) -> VoiceHostPortResult:
        try:
            result = self.journal.mark_command_completed(command_id)
        except Exception:
            return VoiceHostPortResult.failed(
                "command_receipt_completion_failed",
                retryable=True,
            )
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_command_receipt_completion_result",
            invalid_status_reason="invalid_command_receipt_completion_status",
        )

    def _load_pending_projections(self) -> VoiceProjectionOutboxLoadResult:
        try:
            result = self.journal.load_pending_projections()
        except Exception:
            return VoiceProjectionOutboxLoadResult.failed(
                "projection_outbox_load_failed",
                retryable=True,
            )
        if not isinstance(result, VoiceProjectionOutboxLoadResult):
            return VoiceProjectionOutboxLoadResult.failed("invalid_projection_outbox_load_result")
        if result.status not in {"succeeded", "failed"}:
            return VoiceProjectionOutboxLoadResult.failed("invalid_projection_outbox_load_status")
        if result.status == "succeeded" and any(not isinstance(record, Mapping) for record in result.records):
            return VoiceProjectionOutboxLoadResult.failed("invalid_projection_outbox_record")
        return result

    def _mark_projection_delivered(self, projection_id: str) -> VoiceHostPortResult:
        try:
            result = self.journal.mark_projection_delivered(projection_id)
        except Exception:
            return VoiceHostPortResult.failed(
                "projection_acknowledgement_failed",
                retryable=True,
            )
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_projection_acknowledgement_result",
            invalid_status_reason="invalid_projection_acknowledgement_status",
        )

    @staticmethod
    def _validated_host_port_result(
        result: Any,
        *,
        invalid_result_reason: str,
        invalid_status_reason: str,
    ) -> VoiceHostPortResult:
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed(invalid_result_reason)
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed(invalid_status_reason)
        return result

    def _emit_projection(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        try:
            result = self.projection_port.emit(projection_record)
        except Exception:
            return VoiceHostPortResult.failed("projection_emit_failed", retryable=True)
        return self._validated_host_port_result(
            result,
            invalid_result_reason="invalid_projection_result",
            invalid_status_reason="invalid_projection_status",
        )
