"""Durable command receipt recovery through the owning Host state."""
from __future__ import annotations

from voicecore import TransitionStatus, voice_command_from_dict, voice_event_from_dict, voice_event_to_dict

from .contracts import VoiceCommandReceiptDrainResult, VoiceCommandReceiptRecord, VoiceHostDispatchResult


class HostReceipts:
    """Durable command receipt recovery through the owning Host state."""

    def drain_command_receipts(self) -> VoiceCommandReceiptDrainResult:
        loaded = self._load_unfinished_commands()
        if not loaded.ok:
            return VoiceCommandReceiptDrainResult(
                status="deferred" if loaded.retryable else "failed",
                reason=loaded.reason or "command_receipt_load_failed",
            )
        records = tuple(loaded.records)
        dispatch_results: list[VoiceHostDispatchResult] = []
        for index, receipt in enumerate(records):
            try:
                command = voice_command_from_dict(receipt.command_record)
            except (TypeError, ValueError):
                return self._command_receipt_failure(
                    reason="command_receipt_record_invalid",
                    records=records,
                    index=index,
                    dispatch_results=dispatch_results,
                )
            pending_command = self.snapshot.pending_commands.get(command.command_id)
            if pending_command is None:
                settled = (
                    self._release_command(command.command_id)
                    if receipt.phase == "executing"
                    else self._mark_command_completed(command.command_id)
                )
                if not settled.ok:
                    return self._command_receipt_failure(
                        reason=settled.reason or "superseded_command_receipt_settlement_failed",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                        retryable=settled.retryable,
                    )
                continue
            if pending_command != command:
                return self._command_receipt_failure(
                    reason="command_receipt_pending_command_conflict",
                    records=records,
                    index=index,
                    dispatch_results=dispatch_results,
                )
            observation_records = receipt.observation_records
            if receipt.phase == "executing":
                recovery = self._recover_command(command)
                if recovery.in_progress:
                    # A live asynchronous effect owns this receipt. It must not
                    # be released/retried or block unrelated control commands.
                    continue
                if recovery.status == "not_started":
                    released = self._release_command(command.command_id)
                    if not released.ok:
                        return self._command_receipt_failure(
                            reason=released.reason or "command_receipt_release_failed",
                            records=records,
                            index=index,
                            dispatch_results=dispatch_results,
                            retryable=released.retryable,
                        )
                    continue
                if recovery.status != "succeeded":
                    return self._command_receipt_failure(
                        reason=recovery.reason or "command_recovery_deferred",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                        retryable=recovery.retryable,
                    )
                stored = self._store_command_observations(command, recovery.observations)
                if not stored.ok:
                    return self._command_receipt_failure(
                        reason=stored.reason or "command_observation_store_failed",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                        retryable=stored.retryable,
                    )
                observation_records = tuple(voice_event_to_dict(observation) for observation in recovery.observations)
            elif receipt.phase != "observations_pending":
                return self._command_receipt_failure(
                    reason="command_receipt_phase_invalid",
                    records=records,
                    index=index,
                    dispatch_results=dispatch_results,
                )

            for observation_record in observation_records:
                try:
                    observation = voice_event_from_dict(observation_record)
                except (TypeError, ValueError):
                    return self._command_receipt_failure(
                        reason="command_observation_record_invalid",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                    )
                dispatch = self.accept_event(observation)
                dispatch_results.append(dispatch)
                if dispatch.status == TransitionStatus.DUPLICATE.value:
                    continue
                if not dispatch.accepted:
                    return self._command_receipt_failure(
                        reason=dispatch.reason or "command_observation_dispatch_failed",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                        retryable=(
                            dispatch.status == TransitionStatus.DEFERRED.value
                            or (dispatch.journal_result is not None and dispatch.journal_result.retryable)
                        ),
                    )
                if dispatch.projection_drain_result is not None and not dispatch.projection_drain_result.quiescent:
                    return self._command_receipt_failure(
                        reason=dispatch.projection_drain_result.reason or "command_observation_projection_failed",
                        records=records,
                        index=index,
                        dispatch_results=dispatch_results,
                        retryable=dispatch.projection_drain_result.status == "deferred",
                    )
            completed = self._mark_command_completed(command.command_id)
            if not completed.ok:
                return self._command_receipt_failure(
                    reason=completed.reason or "command_receipt_completion_failed",
                    records=records,
                    index=index,
                    dispatch_results=dispatch_results,
                    retryable=completed.retryable,
                )
        return VoiceCommandReceiptDrainResult(
            status="succeeded",
            reason="",
            dispatch_results=tuple(dispatch_results),
        )

    @staticmethod
    def _command_receipt_failure(
        *,
        reason: str,
        records: tuple[VoiceCommandReceiptRecord, ...],
        index: int,
        dispatch_results: list[VoiceHostDispatchResult],
        retryable: bool = False,
    ) -> VoiceCommandReceiptDrainResult:
        return VoiceCommandReceiptDrainResult(
            status="deferred" if retryable else "failed",
            reason=reason,
            dispatch_results=tuple(dispatch_results),
            pending_receipt_command_ids=tuple(
                str(record.command_record.get("command_id") or "") for record in records[index:]
            ),
        )
