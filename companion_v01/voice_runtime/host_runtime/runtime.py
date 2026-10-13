"""The owning conversation snapshot and persist-before-advance event boundary."""
from __future__ import annotations

from typing import Any

from voicecore import TransitionStatus, VoiceEvent, VoiceRuntimeSnapshot, initial_snapshot, projection_to_host_record, reduce_event, validate_snapshot, voice_event_to_dict

from .commands import HostCommands
from .contracts import VoiceCommandExecutor, VoiceHostDispatchResult, VoiceProjectionPort, VoiceRuntimeJournal
from .ports import HostPorts
from .projections import HostProjections
from .receipts import HostReceipts


class AkaneVoiceRuntimeHost(HostCommands, HostReceipts, HostProjections, HostPorts):
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
