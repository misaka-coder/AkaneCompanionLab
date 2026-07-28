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
    voice_command_from_dict,
    voice_command_to_dict,
    voice_event_from_dict,
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
    outcome_known: bool = True

    @classmethod
    def succeeded(cls, *observations: VoiceEvent) -> VoiceCommandExecutionResult:
        return cls(status="succeeded", observations=tuple(observations))

    @classmethod
    def deferred(cls, reason: str) -> VoiceCommandExecutionResult:
        return cls(status="deferred", reason=reason, retryable=True)

    @classmethod
    def failed(cls, reason: str, *, retryable: bool = False) -> VoiceCommandExecutionResult:
        return cls(status="failed", reason=reason, retryable=retryable)

    @classmethod
    def unknown(cls, reason: str) -> VoiceCommandExecutionResult:
        return cls(
            status="failed",
            reason=reason,
            retryable=True,
            outcome_known=False,
        )

    @classmethod
    def not_started(cls, reason: str = "") -> VoiceCommandExecutionResult:
        return cls(status="not_started", reason=reason, retryable=True)


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


@dataclass(frozen=True)
class VoiceCommandReceiptRecord:
    phase: str
    command_record: Mapping[str, Any]
    observation_records: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class VoiceCommandReceiptLoadResult:
    status: str
    reason: str = ""
    retryable: bool = False
    records: tuple[VoiceCommandReceiptRecord, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    @classmethod
    def succeeded(
        cls,
        records: tuple[VoiceCommandReceiptRecord, ...] = (),
    ) -> VoiceCommandReceiptLoadResult:
        return cls(status="succeeded", records=records)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        retryable: bool = False,
    ) -> VoiceCommandReceiptLoadResult:
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

    def begin_command(self, command_record: Mapping[str, Any]) -> VoiceHostPortResult: ...

    def store_command_observations(
        self,
        command_id: str,
        observation_records: tuple[Mapping[str, Any], ...],
    ) -> VoiceHostPortResult: ...

    def release_command(self, command_id: str) -> VoiceHostPortResult: ...

    def load_unfinished_commands(self) -> VoiceCommandReceiptLoadResult: ...

    def mark_command_completed(self, command_id: str) -> VoiceHostPortResult: ...


class VoiceProjectionPort(Protocol):
    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult: ...


class VoiceCommandExecutor(Protocol):
    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult: ...

    def recover(
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
    command_receipt_result: VoiceCommandReceiptDrainResult | None = None

    @property
    def quiescent(self) -> bool:
        return (
            self.status == "succeeded"
            and not self.pending_command_ids
            and not self.pending_projection_ids
            and (self.command_receipt_result is None or self.command_receipt_result.quiescent)
        )


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


@dataclass(frozen=True)
class VoiceCommandReceiptDrainResult:
    status: str
    reason: str
    dispatch_results: tuple[VoiceHostDispatchResult, ...] = ()
    pending_receipt_command_ids: tuple[str, ...] = ()

    @property
    def quiescent(self) -> bool:
        return self.status == "succeeded" and not self.pending_receipt_command_ids


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
        allowed_statuses = {"succeeded", "deferred", "failed"}
        if allow_not_started:
            allowed_statuses.add("not_started")
        if result.status not in allowed_statuses:
            return VoiceCommandExecutionResult.unknown("invalid_command_executor_status")
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
        if any(not isinstance(observation, VoiceEvent) for observation in result.observations):
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
            else:
                break

        failed = any(result.status == "failed" for result in command_results) or any(
            result.status not in {TransitionStatus.ACCEPTED.value, TransitionStatus.DEFERRED.value}
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
        receipt_failed = command_receipt_issue is not None and not command_receipt_issue.retryable
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
                        retryable=dispatch.status == TransitionStatus.DEFERRED.value,
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
            result = self.journal.store_command_observations(
                command.command_id,
                observation_records,
            )
        except (TypeError, ValueError):
            return VoiceHostPortResult.failed("command_observation_not_serializable")
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
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_projection_acknowledgement_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_projection_acknowledgement_status")
        return result

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
        if not isinstance(result, VoiceHostPortResult):
            return VoiceHostPortResult.failed("invalid_projection_result")
        if result.status not in {"succeeded", "duplicate", "failed"}:
            return VoiceHostPortResult.failed("invalid_projection_status")
        return result
