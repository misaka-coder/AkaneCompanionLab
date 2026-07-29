from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from capcore_adapter_speech import (
    ASRRevisionQuality,
    ASRSessionUpdate,
    ASRTranscriptRevision,
)
from voicecore import VoiceEvent

from .host import AkaneVoiceRuntimeHost, VoiceHostDispatchResult
from .stream_bridge import VoiceStreamEventFactory


@dataclass(frozen=True)
class VoiceASRBridgeResult:
    status: str
    reason: str
    source_status: str
    dispatch_results: tuple[VoiceHostDispatchResult, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


class VoiceASRSessionBridge:
    """Map provider-neutral ASR revisions into the one VoiceCore input turn.

    The speech adapter owns provider revision normalization. VoiceCore remains
    authoritative for turn state, final/commit uniqueness, journal replay, and
    MemCore projections. This bridge deliberately does not invoke the model or
    persist audio frames.
    """

    _EVENT_KIND_BY_QUALITY = {
        ASRRevisionQuality.PARTIAL: "voice.asr.partial",
        ASRRevisionQuality.STABLE_CHECKPOINT: "voice.asr.checkpoint",
        ASRRevisionQuality.FINAL: "voice.asr.finalized",
    }

    def __init__(
        self,
        *,
        host: AkaneVoiceRuntimeHost,
        event_factory: VoiceStreamEventFactory,
        voice_turn_id: str,
        audio_stream_id: str,
        disposition: str = "message",
    ) -> None:
        self.host = host
        self.event_factory = event_factory
        self.voice_turn_id = str(voice_turn_id or "")
        self.audio_stream_id = str(audio_stream_id or "")
        self.disposition = str(disposition or "")

    def open_turn(self) -> VoiceASRBridgeResult:
        if not self.voice_turn_id or not self.audio_stream_id:
            return self._failed("open", "voice_asr_identity_missing")
        if self.disposition not in {"message", "interaction"}:
            return self._failed("open", "voice_asr_disposition_invalid")

        existing = self.host.snapshot.input_turns.get(self.voice_turn_id)
        if existing is not None:
            if existing.audio_stream_id == self.audio_stream_id:
                return VoiceASRBridgeResult(
                    status="duplicate",
                    reason="voice_turn_already_opened",
                    source_status="open",
                )
            return self._failed("open", "voice_turn_identity_conflict")

        event = self._make_event(
            "voice.input.activity_started",
            provider_receipt_id=self._receipt("open"),
            voice_turn_id=self.voice_turn_id,
            audio_stream_id=self.audio_stream_id,
        )
        if event is None:
            return self._failed("open", "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        return self._from_dispatches("open", (dispatch,))

    def suspect_interruption(self, *, audio_clock_ms: int) -> VoiceASRBridgeResult:
        """Attach real acoustic activity to the currently playing speech unit.

        Opening an ASR session is not proof that the user spoke. The desktop
        client reports this boundary only after its acoustic detector confirms
        speech, and VoiceCore remains authoritative for ducking and the later
        semantic decision.
        """

        if self.voice_turn_id not in self.host.snapshot.input_turns:
            return self._failed("interruption", "voice_turn_not_open")
        if isinstance(audio_clock_ms, bool) or not isinstance(audio_clock_ms, int) or audio_clock_ms < 0:
            return self._failed("interruption", "voice_interruption_audio_clock_invalid")

        existing = next(
            (
                attempt
                for attempt in self.host.snapshot.interruption_attempts.values()
                if str(getattr(attempt, "voice_turn_id", "") or "") == self.voice_turn_id
            ),
            None,
        )
        if existing is not None:
            return VoiceASRBridgeResult(
                status="duplicate",
                reason="voice_interruption_already_suspected",
                source_status="interruption",
            )

        unit = next(
            (
                candidate
                for candidate in self.host.snapshot.speech_units.values()
                if str(getattr(getattr(candidate, "state", None), "value", "") or "") == "playing"
            ),
            None,
        )
        if unit is None:
            return VoiceASRBridgeResult(
                status="duplicate",
                reason="voice_playback_not_active",
                source_status="interruption",
            )
        response = self.host.snapshot.responses.get(unit.response_id)
        if response is None:
            return self._failed("interruption", "voice_interruption_response_missing")

        material = f"{self.voice_turn_id}:{unit.speech_unit_id}:interruption"
        interruption_id = "voice_interrupt_" + sha256(material.encode("utf-8")).hexdigest()[:32]
        event = self._make_event(
            "voice.interruption.suspected",
            provider_receipt_id=self._receipt(material),
            voice_turn_id=self.voice_turn_id,
            response_id=response.response_id,
            speech_unit_id=unit.speech_unit_id,
            audio_stream_id=self.audio_stream_id,
            audio_clock_ms=audio_clock_ms,
            turn_revision=response.source_turn_revision,
            response_generation=response.response_generation,
            payload={"interruption_id": interruption_id},
        )
        if event is None:
            return self._failed("interruption", "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        return self._from_dispatches("interruption", (dispatch,))

    @property
    def committed_disposition(self) -> str:
        turn = self.host.snapshot.input_turns.get(self.voice_turn_id)
        if turn is None or str(getattr(turn.state, "value", "") or "") != "committed":
            return ""
        return str(getattr(getattr(turn, "disposition", None), "value", "") or "")

    def accept_update(self, update: ASRSessionUpdate) -> VoiceASRBridgeResult:
        if not isinstance(update, ASRSessionUpdate):
            return self._failed("", "asr_session_update_invalid")
        if self.voice_turn_id not in self.host.snapshot.input_turns:
            return self._failed(update.status, "voice_turn_not_open")
        if update.status == "failed":
            return self._fail_turn(update)
        if update.status == "cancelled":
            return self._cancel_turn(update)
        if update.status not in {"accepted", "duplicate"}:
            return self._failed(update.status, "asr_session_update_status_invalid")

        dispatches: list[VoiceHostDispatchResult] = []
        for revision in update.revisions:
            revision_result = self._accept_revision(revision)
            dispatches.extend(revision_result.dispatch_results)
            if revision_result.status == "failed":
                return VoiceASRBridgeResult(
                    status="failed",
                    reason=revision_result.reason,
                    source_status=update.status,
                    dispatch_results=tuple(dispatches),
                )

        turn = self.host.snapshot.input_turns.get(self.voice_turn_id)
        final_revision = next(
            (revision for revision in reversed(update.revisions) if revision.quality is ASRRevisionQuality.FINAL),
            None,
        )
        if final_revision is not None and turn is not None:
            if turn.state.value == "finalized":
                commit = self._commit_final(final_revision)
                dispatches.extend(commit.dispatch_results)
                if commit.status == "failed":
                    return VoiceASRBridgeResult(
                        status="failed",
                        reason=commit.reason,
                        source_status=update.status,
                        dispatch_results=tuple(dispatches),
                    )
            elif turn.state.value != "committed":
                return VoiceASRBridgeResult(
                    status="failed",
                    reason="voice_final_not_ready_to_commit",
                    source_status=update.status,
                    dispatch_results=tuple(dispatches),
                )

        if not dispatches:
            return VoiceASRBridgeResult(
                status=update.status,
                reason="" if update.status == "accepted" else "asr_update_already_applied",
                source_status=update.status,
            )
        return self._from_dispatches(update.status, tuple(dispatches))

    def _accept_revision(self, revision: ASRTranscriptRevision) -> VoiceASRBridgeResult:
        if not isinstance(revision, ASRTranscriptRevision):
            return self._failed("revision", "asr_revision_invalid")
        event_kind = self._EVENT_KIND_BY_QUALITY.get(revision.quality)
        if event_kind is None:
            return self._failed("revision", "asr_revision_quality_invalid")
        payload: dict[str, Any] = {
            "stable_text": revision.stable_text,
            "unstable_tail": revision.unstable_tail,
            "control_significant": revision.control_significant,
        }
        if revision.language_hint is not None:
            payload["language_hint"] = revision.language_hint
        if revision.confidence_hint is not None:
            payload["confidence_hint"] = revision.confidence_hint
        if revision.supersedes_revision is not None:
            payload["supersedes_revision"] = revision.supersedes_revision

        event = self._make_event(
            event_kind,
            provider_receipt_id=self._revision_receipt(revision, event_kind),
            voice_turn_id=self.voice_turn_id,
            turn_revision=revision.revision,
            payload=payload,
        )
        if event is None:
            return self._failed("revision", "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        return self._from_dispatches("revision", (dispatch,))

    def _commit_final(self, revision: ASRTranscriptRevision) -> VoiceASRBridgeResult:
        event = self._make_event(
            "voice.turn.commit_requested",
            provider_receipt_id=self._revision_receipt(revision, "voice.turn.commit_requested"),
            voice_turn_id=self.voice_turn_id,
            turn_revision=revision.revision,
            payload={"disposition": self.disposition},
        )
        if event is None:
            return self._failed("final", "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        return self._from_dispatches("final", (dispatch,))

    def _fail_turn(self, update: ASRSessionUpdate) -> VoiceASRBridgeResult:
        turn = self.host.snapshot.input_turns[self.voice_turn_id]
        if turn.state.value == "failed":
            return VoiceASRBridgeResult(
                status="duplicate",
                reason="voice_turn_failure_already_recorded",
                source_status=update.status,
            )
        if turn.state.value in {"committed", "cancelled"}:
            return self._failed(update.status, "voice_turn_already_terminal")
        payload = {
            "stage": "asr",
            "reason_code": update.reason or "asr_provider_failed",
            "retryable": update.retryable,
            "provider_status": update.provider_status or None,
            "safe_public_summary": update.safe_public_summary,
            "affected_ids": [self.voice_turn_id],
        }
        event = self._make_event(
            "voice.turn.failed",
            provider_receipt_id=self._receipt(f"failed:{update.reason}"),
            voice_turn_id=self.voice_turn_id,
            payload=payload,
        )
        if event is None:
            return self._failed(update.status, "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        return self._from_dispatches(update.status, (dispatch,))

    def _cancel_turn(self, update: ASRSessionUpdate) -> VoiceASRBridgeResult:
        turn = self.host.snapshot.input_turns[self.voice_turn_id]
        if turn.state.value == "cancelled":
            return VoiceASRBridgeResult(
                status="duplicate",
                reason="voice_turn_cancel_already_recorded",
                source_status=update.status,
            )
        if turn.state.value in {"committed", "failed"}:
            return self._failed(update.status, "voice_turn_already_terminal")
        reason = update.reason or "cancelled"
        event = self._make_event(
            "voice.turn.cancel_requested",
            provider_receipt_id=self._receipt(f"cancelled:{reason}"),
            voice_turn_id=self.voice_turn_id,
            payload={"reason": reason},
        )
        if event is None:
            return self._failed(update.status, "voice_event_build_failed")
        dispatch = self.host.accept_event(event)
        dispatches = [dispatch]
        if dispatch.accepted and reason in {
            "speech_without_transcript",
            "unconfirmed_acoustic_activity",
            "utterance_discarded",
        }:
            attempt = next(
                (
                    candidate
                    for candidate in reversed(tuple(self.host.snapshot.interruption_attempts.values()))
                    if str(getattr(candidate, "voice_turn_id", "") or "") == self.voice_turn_id
                    and str(getattr(getattr(candidate, "state", None), "value", "") or "") == "suspected"
                ),
                None,
            )
            if attempt is not None:
                false_positive = self._make_event(
                    "voice.interruption.false_positive",
                    provider_receipt_id=self._receipt(f"false_positive:{attempt.interruption_id}:{reason}"),
                    voice_turn_id=self.voice_turn_id,
                    response_id=attempt.response_id,
                    speech_unit_id=attempt.speech_unit_id,
                    audio_stream_id=self.audio_stream_id,
                    payload={"interruption_id": attempt.interruption_id},
                )
                if false_positive is None:
                    return self._failed(
                        update.status,
                        "voice_event_build_failed",
                    )
                dispatches.append(self.host.accept_event(false_positive))
        return self._from_dispatches(update.status, tuple(dispatches))

    def _make_event(self, event_kind: str, **overrides: Any) -> VoiceEvent | None:
        try:
            event = self.event_factory.make(event_kind, **overrides)
        except Exception:
            return None
        return event if isinstance(event, VoiceEvent) else None

    def _revision_receipt(self, revision: ASRTranscriptRevision, event_kind: str) -> str:
        source_receipt = revision.provider_receipt_id or (
            f"{revision.revision}:{revision.quality.value}:{revision.stable_text}:{revision.unstable_tail}"
        )
        return self._receipt(f"{event_kind}:{source_receipt}")

    def _receipt(self, material: str) -> str:
        digest = sha256(f"{self.voice_turn_id}:{material}".encode("utf-8")).hexdigest()[:32]
        return f"voice_asr_{digest}"

    @staticmethod
    def _from_dispatches(
        source_status: str,
        dispatches: tuple[VoiceHostDispatchResult, ...],
    ) -> VoiceASRBridgeResult:
        failed = next(
            (result for result in dispatches if result.status not in {"accepted", "duplicate"}),
            None,
        )
        if failed is not None:
            return VoiceASRBridgeResult(
                status="failed",
                reason=failed.reason or "voice_asr_dispatch_failed",
                source_status=source_status,
                dispatch_results=dispatches,
            )
        status = "accepted" if any(result.status == "accepted" for result in dispatches) else "duplicate"
        return VoiceASRBridgeResult(
            status=status,
            reason="" if status == "accepted" else "voice_asr_update_already_applied",
            source_status=source_status,
            dispatch_results=dispatches,
        )

    @staticmethod
    def _failed(source_status: str, reason: str) -> VoiceASRBridgeResult:
        return VoiceASRBridgeResult(
            status="failed",
            reason=reason,
            source_status=source_status,
        )
