from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from voicecore import (
    TransitionResult,
    TransitionStatus,
    VoiceCommand,
    VoiceEvent,
    VoiceRuntimeSnapshot,
    initial_snapshot,
    projection_to_host_record,
    reduce_event,
    snapshot_to_dict,
    validate_snapshot,
    voice_command_to_dict,
    voice_event_to_dict,
)


@dataclass(frozen=True)
class VoiceHostPortResult:
    status: str
    reason: str = ""
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "duplicate"}

    @classmethod
    def succeeded(cls) -> VoiceHostPortResult:
        return cls(status="succeeded")

    @classmethod
    def failed(cls, reason: str, *, retryable: bool = False) -> VoiceHostPortResult:
        return cls(status="failed", reason=reason, retryable=retryable)


@dataclass(frozen=True)
class VoiceCommandExecutionResult:
    status: str
    reason: str = ""
    retryable: bool = False
    observations: tuple[VoiceEvent, ...] = ()

    @classmethod
    def succeeded(cls, *observations: VoiceEvent) -> VoiceCommandExecutionResult:
        return cls(status="succeeded", observations=tuple(observations))

    @classmethod
    def deferred(cls, reason: str) -> VoiceCommandExecutionResult:
        return cls(status="deferred", reason=reason, retryable=True)

    @classmethod
    def failed(cls, reason: str, *, retryable: bool = False) -> VoiceCommandExecutionResult:
        return cls(status="failed", reason=reason, retryable=retryable)


class VoiceRuntimeJournal(Protocol):
    def append(self, event_record: Mapping[str, Any]) -> VoiceHostPortResult: ...


class VoiceProjectionPort(Protocol):
    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult: ...


class VoiceCommandExecutor(Protocol):
    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult: ...


@dataclass(frozen=True)
class VoiceHostDispatchResult:
    status: str
    reason: str
    transition: TransitionResult
    snapshot_advanced: bool
    journal_result: VoiceHostPortResult | None = None
    projection_results: tuple[VoiceHostPortResult, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == TransitionStatus.ACCEPTED.value and self.snapshot_advanced


@dataclass(frozen=True)
class VoiceHostDriveResult:
    status: str
    reason: str
    command_results: tuple[VoiceCommandExecutionResult, ...]
    dispatch_results: tuple[VoiceHostDispatchResult, ...]
    pending_command_ids: tuple[str, ...]

    @property
    def quiescent(self) -> bool:
        return not self.pending_command_ids


class AkaneVoiceRuntimeHost:
    """Thin effect boundary around VoiceCore; callers serialize one conversation's events."""

    def __init__(
        self,
        *,
        conversation_id: str,
        conversation_generation: int,
        journal: VoiceRuntimeJournal,
        projection_port: VoiceProjectionPort,
        command_executor: VoiceCommandExecutor,
        policy_snapshot: dict[str, Any] | None = None,
        capability_snapshot: dict[str, Any] | None = None,
        restored_snapshot: VoiceRuntimeSnapshot | None = None,
    ) -> None:
        self.journal = journal
        self.projection_port = projection_port
        self.command_executor = command_executor
        if restored_snapshot is None:
            self.snapshot = initial_snapshot(
                conversation_id,
                conversation_generation=conversation_generation,
                policy_snapshot=policy_snapshot,
                capability_snapshot=capability_snapshot,
            )
        else:
            if not isinstance(restored_snapshot, VoiceRuntimeSnapshot):
                raise ValueError("restored_voice_snapshot_invalid")
            if (
                restored_snapshot.conversation_id != conversation_id
                or restored_snapshot.conversation_generation != conversation_generation
            ):
                raise ValueError("restored_voice_snapshot_identity_mismatch")
            if policy_snapshot is not None and restored_snapshot.policy_snapshot != policy_snapshot:
                raise ValueError("restored_voice_snapshot_policy_mismatch")
            if capability_snapshot is not None and restored_snapshot.capability_snapshot != capability_snapshot:
                raise ValueError("restored_voice_snapshot_capability_mismatch")
            if validate_snapshot(restored_snapshot):
                raise ValueError("restored_voice_snapshot_invalid")
            self.snapshot = restored_snapshot.clone()

    def accept_event(self, event: VoiceEvent) -> VoiceHostDispatchResult:
        transition = reduce_event(self.snapshot, event)
        if transition.status is not TransitionStatus.ACCEPTED:
            return VoiceHostDispatchResult(
                status=transition.status.value,
                reason=transition.reason,
                transition=transition,
                snapshot_advanced=False,
            )

        try:
            event_record = voice_event_to_dict(event)
        except (TypeError, ValueError):
            return VoiceHostDispatchResult(
                status="failed",
                reason="event_not_serializable",
                transition=transition,
                snapshot_advanced=False,
            )
        journal_result = self._append_journal(event_record)
        if not journal_result.ok:
            return VoiceHostDispatchResult(
                status="failed",
                reason=journal_result.reason or "journal_append_failed",
                transition=transition,
                snapshot_advanced=False,
                journal_result=journal_result,
            )

        self.snapshot = transition.snapshot
        projection_results = tuple(
            self._emit_projection(projection_to_host_record(projection)) for projection in transition.projections
        )
        projection_failed = any(not result.ok for result in projection_results)
        return VoiceHostDispatchResult(
            status=TransitionStatus.ACCEPTED.value,
            reason="projection_emit_failed" if projection_failed else transition.reason,
            transition=transition,
            snapshot_advanced=True,
            journal_result=journal_result,
            projection_results=projection_results,
        )

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
            return VoiceCommandExecutionResult.failed("command_executor_failed", retryable=True)
        if not isinstance(result, VoiceCommandExecutionResult):
            return VoiceCommandExecutionResult.failed("invalid_command_executor_result")
        if result.status not in {"succeeded", "deferred", "failed"}:
            return VoiceCommandExecutionResult.failed("invalid_command_executor_status")
        if result.status != "succeeded":
            return result
        if not result.observations:
            return VoiceCommandExecutionResult.failed("command_observation_missing", retryable=True)
        if any(not isinstance(observation, VoiceEvent) for observation in result.observations):
            return VoiceCommandExecutionResult.failed("invalid_command_observation")
        if not any(observation.payload.get("command_id") == command.command_id for observation in result.observations):
            return VoiceCommandExecutionResult.failed("command_correlation_missing")
        return result

    def drive_once(self) -> VoiceHostDriveResult:
        command_results: list[VoiceCommandExecutionResult] = []
        dispatch_results: list[VoiceHostDispatchResult] = []
        command_ids = tuple(self.snapshot.pending_commands)

        for command_id in command_ids:
            command = self.snapshot.pending_commands.get(command_id)
            if command is None:
                continue
            execution = self.execute_command(command)
            command_results.append(execution)
            if execution.status != "succeeded":
                continue
            for observation in execution.observations:
                dispatch = self.accept_event(observation)
                dispatch_results.append(dispatch)
                if not dispatch.accepted:
                    break

        failed = any(result.status == "failed" for result in command_results) or any(
            result.status not in {TransitionStatus.ACCEPTED.value, TransitionStatus.DEFERRED.value}
            for result in dispatch_results
        )
        deferred = any(result.status == "deferred" for result in command_results) or any(
            result.status == TransitionStatus.DEFERRED.value for result in dispatch_results
        )
        projection_failed = any(result.reason == "projection_emit_failed" for result in dispatch_results)
        status = "failed" if failed else "deferred" if deferred else "succeeded"
        if failed:
            reason = "voice_command_drive_failed"
        elif deferred:
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
        )

    def _append_journal(self, event_record: Mapping[str, Any]) -> VoiceHostPortResult:
        try:
            result = self.journal.append(event_record)
        except Exception:
            return VoiceHostPortResult.failed("journal_append_failed", retryable=True)
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_journal_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_journal_status")
        return result

    def _emit_projection(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        try:
            result = self.projection_port.emit(projection_record)
        except Exception:
            return VoiceHostPortResult.failed("projection_emit_failed", retryable=True)
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_projection_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_projection_status")
        return result
