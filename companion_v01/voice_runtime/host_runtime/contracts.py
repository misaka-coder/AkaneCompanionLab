"""Typed Host outcomes and effect port contracts."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from voicecore import TransitionResult, TransitionStatus, VoiceEvent


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
    in_progress: bool = False

    @classmethod
    def succeeded(cls, *observations: VoiceEvent) -> VoiceCommandExecutionResult:
        return cls(status="succeeded", observations=tuple(observations))

    @classmethod
    def deferred(cls, reason: str) -> VoiceCommandExecutionResult:
        return cls(status="deferred", reason=reason, retryable=True)

    @classmethod
    def running(cls, reason: str) -> VoiceCommandExecutionResult:
        """An effect is still running locally; retain its durable executing receipt."""
        return cls(
            status="deferred",
            reason=reason,
            retryable=True,
            outcome_known=False,
            in_progress=True,
        )

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
