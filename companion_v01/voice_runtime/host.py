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


@dataclass(frozen=True)
class VoiceProjectionOutboxLoadResult:
    status: str
    reason: str = ""
    retryable: bool = False
    records: tuple[Mapping[str, Any], ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    @classmethod
    def succeeded(
        cls,
        records: tuple[Mapping[str, Any], ...] = (),
    ) -> VoiceProjectionOutboxLoadResult:
        return cls(status="succeeded", records=records)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        retryable: bool = False,
    ) -> VoiceProjectionOutboxLoadResult:
        return cls(status="failed", reason=reason, retryable=retryable)


class VoiceRuntimeJournal(Protocol):
    """Durably commit accepted events and their deterministic projections.

    Projection delivery is at-least-once. Implementations must store the event
    and projection records atomically, while projection ports must deduplicate
    the stable ``projection_id`` before reporting success.
    """

    def append_transition(
        self,
        event_record: Mapping[str, Any],
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult: ...

    def load_pending_projections(self) -> VoiceProjectionOutboxLoadResult: ...

    def mark_projection_delivered(self, projection_id: str) -> VoiceHostPortResult: ...


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
    projection_drain_result: VoiceProjectionDrainResult | None = None

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
    pending_projection_ids: tuple[str, ...] = ()
    projection_drain_result: VoiceProjectionDrainResult | None = None

    @property
    def quiescent(self) -> bool:
        return self.status == "succeeded" and not self.pending_command_ids and not self.pending_projection_ids


@dataclass(frozen=True)
class VoiceProjectionDrainResult:
    status: str
    reason: str
    projection_results: tuple[VoiceHostPortResult, ...] = ()
    acknowledgement_results: tuple[VoiceHostPortResult, ...] = ()
    pending_projection_ids: tuple[str, ...] = ()

    @property
    def quiescent(self) -> bool:
        return self.status == "succeeded" and not self.pending_projection_ids


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
            projection_records = tuple(projection_to_host_record(projection) for projection in transition.projections)
        except (TypeError, ValueError):
            return VoiceHostDispatchResult(
                status="failed",
                reason="transition_not_serializable",
                transition=transition,
                snapshot_advanced=False,
            )
        journal_result = self._append_transition(event_record, projection_records)
        if not journal_result.ok:
            return VoiceHostDispatchResult(
                status="failed",
                reason=journal_result.reason or "journal_append_failed",
                transition=transition,
                snapshot_advanced=False,
                journal_result=journal_result,
            )

        self.snapshot = transition.snapshot
        projection_drain = self.drain_projection_outbox()
        projection_failed = projection_drain.status != "succeeded"
        return VoiceHostDispatchResult(
            status=TransitionStatus.ACCEPTED.value,
            reason="projection_emit_failed" if projection_failed else transition.reason,
            transition=transition,
            snapshot_advanced=True,
            journal_result=journal_result,
            projection_results=projection_drain.projection_results,
            projection_drain_result=projection_drain,
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
        pending_projection_ids = tuple(
            dict.fromkeys(
                projection_id
                for result in dispatch_results
                if result.projection_drain_result is not None
                for projection_id in result.projection_drain_result.pending_projection_ids
            )
        )
        projection_failed = any(result.reason == "projection_emit_failed" for result in dispatch_results)
        projection_deferred = any(
            result.projection_drain_result is not None and result.projection_drain_result.status == "deferred"
            for result in dispatch_results
        )
        projection_hard_failed = any(
            result.projection_drain_result is not None and result.projection_drain_result.status == "failed"
            for result in dispatch_results
        )
        status = (
            "failed"
            if failed or projection_hard_failed
            else "deferred"
            if deferred or projection_deferred or pending_projection_ids
            else "succeeded"
        )
        if failed or projection_hard_failed:
            reason = "voice_command_drive_failed"
        elif deferred or projection_deferred or pending_projection_ids:
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
        )

    def drain_projection_outbox(self) -> VoiceProjectionDrainResult:
        loaded = self._load_pending_projections()
        if not loaded.ok:
            return VoiceProjectionDrainResult(
                status="deferred" if loaded.retryable else "failed",
                reason=loaded.reason or "projection_outbox_load_failed",
            )
        records = tuple(loaded.records)
        projection_results: list[VoiceHostPortResult] = []
        acknowledgement_results: list[VoiceHostPortResult] = []
        for index, projection_record in enumerate(records):
            projection_id = str(projection_record.get("projection_id") or "")
            if not projection_id:
                return VoiceProjectionDrainResult(
                    status="failed",
                    reason="projection_outbox_record_invalid",
                    projection_results=tuple(projection_results),
                    acknowledgement_results=tuple(acknowledgement_results),
                    pending_projection_ids=tuple(
                        str(record.get("projection_id") or "")
                        for record in records[index:]
                        if str(record.get("projection_id") or "")
                    ),
                )
            emitted = self._emit_projection(projection_record)
            projection_results.append(emitted)
            if not emitted.ok:
                return VoiceProjectionDrainResult(
                    status="deferred" if emitted.retryable else "failed",
                    reason=emitted.reason or "projection_emit_failed",
                    projection_results=tuple(projection_results),
                    acknowledgement_results=tuple(acknowledgement_results),
                    pending_projection_ids=tuple(str(record.get("projection_id") or "") for record in records[index:]),
                )
            acknowledged = self._mark_projection_delivered(projection_id)
            acknowledgement_results.append(acknowledged)
            if not acknowledged.ok:
                return VoiceProjectionDrainResult(
                    status="deferred" if acknowledged.retryable else "failed",
                    reason=acknowledged.reason or "projection_acknowledgement_failed",
                    projection_results=tuple(projection_results),
                    acknowledgement_results=tuple(acknowledgement_results),
                    pending_projection_ids=tuple(str(record.get("projection_id") or "") for record in records[index:]),
                )
        return VoiceProjectionDrainResult(
            status="succeeded",
            reason="",
            projection_results=tuple(projection_results),
            acknowledgement_results=tuple(acknowledgement_results),
        )

    def _append_transition(
        self,
        event_record: Mapping[str, Any],
        projection_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult:
        try:
            result = self.journal.append_transition(event_record, projection_records)
        except Exception:
            return VoiceHostPortResult.failed("journal_append_failed", retryable=True)
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_journal_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_journal_status")
        return result

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
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_projection_acknowledgement_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_projection_acknowledgement_status")
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
