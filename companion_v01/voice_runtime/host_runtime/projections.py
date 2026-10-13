"""Ordered projection delivery and acknowledgement on the owning Host."""
from __future__ import annotations

from .contracts import VoiceHostPortResult, VoiceProjectionDrainResult


class HostProjections:
    """Ordered projection delivery and acknowledgement on the owning Host."""

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
