"""Command execution, validation and one-layer driving on the owning Host."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from voicecore import TransitionStatus, VoiceCommand, VoiceEvent, snapshot_to_dict, voice_command_to_dict

from .contracts import VoiceCommandExecutionResult, VoiceHostDispatchResult, VoiceHostDriveResult, VoiceHostPortResult


class HostCommands:
    """Command execution, validation and one-layer driving on the owning Host."""

    def execute_command(self, command: VoiceCommand) -> VoiceCommandExecutionResult:
        pending = self.snapshot.pending_commands.get(command.command_id)
        if pending is None or pending != command:
            return VoiceCommandExecutionResult.failed("command_not_pending")
        try:
            result = self.command_executor.execute(
                voice_command_to_dict(command),
                snapshot_to_dict(self.snapshot),
            )
        except Exception:
            return VoiceCommandExecutionResult.unknown("command_executor_failed")
        return self._validate_command_execution_result(
            command=command,
            result=result,
            allow_not_started=False,
        )

    def _recover_command(self, command: VoiceCommand) -> VoiceCommandExecutionResult:
        recover = getattr(self.command_executor, "recover", None)
        if not callable(recover):
            return VoiceCommandExecutionResult(
                status="deferred",
                reason="command_recovery_unavailable",
                retryable=True,
                outcome_known=False,
            )
        try:
            result = recover(
                voice_command_to_dict(command),
                snapshot_to_dict(self.snapshot),
            )
        except Exception:
            return VoiceCommandExecutionResult(
                status="deferred",
                reason="command_recovery_failed",
                retryable=True,
                outcome_known=False,
            )
        return self._validate_command_execution_result(
            command=command,
            result=result,
            allow_not_started=True,
        )

    @staticmethod
    def _validate_command_execution_result(
        *,
        command: VoiceCommand,
        result: Any,
        allow_not_started: bool,
    ) -> VoiceCommandExecutionResult:
        if not isinstance(result, VoiceCommandExecutionResult):
            return VoiceCommandExecutionResult.unknown("invalid_command_executor_result")
        if result.in_progress and (result.status != "deferred" or result.outcome_known or result.observations):
            return VoiceCommandExecutionResult.unknown("invalid_command_in_progress_result")
        allowed_statuses = {"succeeded", "deferred", "failed"}
        if allow_not_started:
            allowed_statuses.add("not_started")
        if result.status not in allowed_statuses:
            return VoiceCommandExecutionResult.unknown("invalid_command_executor_status")
        if result.status in {"succeeded", "not_started"} and not result.outcome_known:
            return VoiceCommandExecutionResult.unknown("invalid_command_outcome_result")
        if result.status == "not_started":
            if result.observations:
                return VoiceCommandExecutionResult.unknown("not_started_observation_conflict")
            return result
        if result.status != "succeeded":
            if result.observations:
                return VoiceCommandExecutionResult.unknown("failed_command_observation_conflict")
            return result
        if not result.observations:
            return VoiceCommandExecutionResult.unknown("command_observation_missing")
        if any(
            not isinstance(observation, VoiceEvent) or not isinstance(observation.payload, Mapping)
            for observation in result.observations
        ):
            return VoiceCommandExecutionResult.unknown("invalid_command_observation")
        completion_indexes = [
            index
            for index, observation in enumerate(result.observations)
            if observation.payload.get("command_id") == command.command_id
        ]
        if completion_indexes != [len(result.observations) - 1]:
            return VoiceCommandExecutionResult.unknown("command_completion_observation_invalid")
        return result

    def drive_once(self) -> VoiceHostDriveResult:
        command_results: list[VoiceCommandExecutionResult] = []
        dispatch_results: list[VoiceHostDispatchResult] = []
        projection_drain = self.drain_projection_outbox()
        if not projection_drain.quiescent:
            return VoiceHostDriveResult(
                status=projection_drain.status,
                reason=projection_drain.reason or "projection_outbox_not_drained",
                command_results=(),
                dispatch_results=(),
                pending_command_ids=tuple(self.snapshot.pending_commands),
                pending_projection_ids=projection_drain.pending_projection_ids,
                projection_drain_result=projection_drain,
            )
        command_receipt_result = self.drain_command_receipts()
        dispatch_results.extend(command_receipt_result.dispatch_results)
        if not command_receipt_result.quiescent:
            return VoiceHostDriveResult(
                status=command_receipt_result.status,
                reason=command_receipt_result.reason or "command_receipt_not_drained",
                command_results=(),
                dispatch_results=tuple(dispatch_results),
                pending_command_ids=tuple(self.snapshot.pending_commands),
                pending_projection_ids=self._pending_projection_ids(dispatch_results),
                projection_drain_result=projection_drain,
                command_receipt_result=command_receipt_result,
            )
        if command_receipt_result.dispatch_results:
            return VoiceHostDriveResult(
                status="succeeded",
                reason="",
                command_results=(),
                dispatch_results=tuple(dispatch_results),
                pending_command_ids=tuple(self.snapshot.pending_commands),
                pending_projection_ids=self._pending_projection_ids(dispatch_results),
                projection_drain_result=projection_drain,
                command_receipt_result=command_receipt_result,
            )
        command_ids = tuple(self.snapshot.pending_commands)
        command_receipt_issue: VoiceHostPortResult | None = None

        for command_id in command_ids:
            command = self.snapshot.pending_commands.get(command_id)
            if command is None:
                continue
            begun = self._begin_command(command)
            if not begun.ok:
                command_receipt_issue = begun
                break
            execution = self.execute_command(command)
            command_results.append(execution)
            if execution.status == "succeeded":
                stored = self._store_command_observations(command, execution.observations)
                if not stored.ok:
                    command_receipt_issue = stored
                    break
                command_receipt_result = self.drain_command_receipts()
                dispatch_results.extend(command_receipt_result.dispatch_results)
                if not command_receipt_result.quiescent:
                    break
                continue
            if execution.outcome_known:
                released = self._release_command(command.command_id)
                if not released.ok:
                    command_receipt_issue = released
                    break
            elif not execution.in_progress:
                break

        failed = any(result.status == "failed" for result in command_results) or any(
            result.status not in {
                TransitionStatus.ACCEPTED.value, TransitionStatus.DUPLICATE.value, TransitionStatus.DEFERRED.value,
            }
            and not (
                result.status == "failed"
                and result.journal_result is not None
                and result.journal_result.retryable
            )
            for result in dispatch_results
        )
        deferred = any(result.status == "deferred" for result in command_results) or any(
            result.status == TransitionStatus.DEFERRED.value for result in dispatch_results
        )
        pending_projection_ids = self._pending_projection_ids(dispatch_results)
        projection_failed = any(result.reason == "projection_emit_failed" for result in dispatch_results)
        projection_deferred = any(
            result.projection_drain_result is not None and result.projection_drain_result.status == "deferred"
            for result in dispatch_results
        )
        projection_hard_failed = any(
            result.projection_drain_result is not None and result.projection_drain_result.status == "failed"
            for result in dispatch_results
        )
        receipt_failed = (
            command_receipt_issue is not None and not command_receipt_issue.retryable
        ) or command_receipt_result.status == "failed"
        receipt_deferred = (
            command_receipt_issue is not None and command_receipt_issue.retryable
        ) or command_receipt_result.status == "deferred"
        status = (
            "failed"
            if failed or projection_hard_failed or receipt_failed
            else "deferred"
            if deferred or projection_deferred or pending_projection_ids or receipt_deferred
            else "succeeded"
        )
        if command_receipt_issue is not None:
            reason = command_receipt_issue.reason or "command_receipt_persist_failed"
        elif not command_receipt_result.quiescent:
            reason = command_receipt_result.reason or "command_receipt_not_drained"
        elif failed or projection_hard_failed:
            reason = "voice_command_drive_failed"
        elif deferred or projection_deferred or pending_projection_ids or receipt_deferred:
            reason = "voice_command_drive_deferred"
        elif projection_failed:
            reason = "projection_emit_failed"
        else:
            reason = ""
        return VoiceHostDriveResult(
            status=status,
            reason=reason,
            command_results=tuple(command_results),
            dispatch_results=tuple(dispatch_results),
            pending_command_ids=tuple(self.snapshot.pending_commands),
            pending_projection_ids=pending_projection_ids,
            projection_drain_result=projection_drain,
            command_receipt_result=command_receipt_result,
        )

    @staticmethod
    def _pending_projection_ids(
        dispatch_results: list[VoiceHostDispatchResult],
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                projection_id
                for result in dispatch_results
                if result.projection_drain_result is not None
                for projection_id in result.projection_drain_result.pending_projection_ids
            )
        )
