"""Consume model streams, deliver real observations and settle generation work."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..host import AkaneVoiceRuntimeHost
from ..playback_context import render_unsettled_playback_context
from ..stream_bridge import VoiceResponseStreamBridge
from .context import VoiceThinkingStartResult, _GenerationFailure, _GenerationJob, _safe_failure_reason


class GenerationStream:
    """Streaming and terminal cleanup use the owning executor and serialized Host."""

    def _run_generation(
        self,
        host: AkaneVoiceRuntimeHost,
        job: _GenerationJob,
    ) -> None:
        try:
            final_seen = False
            cancelled = self._generation_cancel_requested(job.response_id)
            with self._guard:
                cancelled = cancelled or self._jobs.get(job.response_id) is not job
            failure_reason = ""
            iterator: Any | None = None
            try:
                if not cancelled:
                    playback_context = render_unsettled_playback_context(
                        host.snapshot,
                        self.text_artifacts,
                        exclude_response_id=job.response_id,
                    )
                    if self._generation_cancel_requested(job.response_id):
                        return
                    playback_kwargs = {"playback_context": playback_context} if playback_context else {}
                    if self.conversation_ref:
                        playback_kwargs["conversation_ref"] = self.conversation_ref
                    if job.commitment == "speculative":
                        candidate_stream = getattr(
                            self.engine,
                            "process_voice_candidate_stream",
                            None,
                        )
                        if not callable(candidate_stream):
                            raise RuntimeError("voice_candidate_runtime_unavailable")
                        iterator = candidate_stream(
                            profile_user_id=self.profile_user_id,
                            session_id=self.session_id,
                            character_pack_id=self.character_pack_id,
                            voice_turn_id=job.voice_turn_id,
                            message=job.message,
                            timestamp=job.timestamp,
                            **playback_kwargs,
                        )
                    else:
                        iterator = self.engine.process_voice_turn_stream(
                            profile_user_id=self.profile_user_id,
                            session_id=self.session_id,
                            character_pack_id=self.character_pack_id,
                            source_id=job.source_id,
                            memcore_turn_id=job.memcore_turn_id,
                            voice_turn_id=job.voice_turn_id,
                            message=job.message,
                            timestamp=job.timestamp,
                            **playback_kwargs,
                        )
                bridge = VoiceResponseStreamBridge(
                    host=host,
                    response_id=job.response_id,
                    text_artifacts=self.text_artifacts,
                    event_factory=job.event_factory,
                )
                for stream_event in iterator or ():
                    if self._generation_cancel_requested(job.response_id):
                        cancelled = True
                        break
                    if not isinstance(stream_event, Mapping):
                        continue
                    event_type = str(stream_event.get("type") or "")
                    if event_type == "speech_mode":
                        bridge.accept_stream_event(stream_event)
                        continue
                    if event_type == "final":
                        final_payload = stream_event.get("payload")
                        if isinstance(final_payload, Mapping) and bool(final_payload.get("_transient_final_failure")):
                            failure_reason = "voice_thinking_final_unusable"
                            break
                        # Stage before the terminal observation can wake transport.
                        # The channel exposes it only after VoiceCore accepts final.
                        if job.presentation_sink is not None and isinstance(final_payload, Mapping):
                            job.presentation_sink(job.response_id, final_payload)
                        if self._generation_cancel_requested(job.response_id):
                            cancelled = True
                            break
                        result = bridge.accept_stream_event(stream_event)
                        if not result.accepted and result.status != "duplicate":
                            if self._generation_cancel_requested(job.response_id):
                                cancelled = True
                                failure_reason = ""
                                break
                            failure_reason = result.reason or "voice_thinking_final_dispatch_failed"
                            break
                        final_seen = True
                        if job.commitment == "speculative":
                            validation_drive = host.drive_once()
                            if validation_drive.status == "failed":
                                failure_reason = validation_drive.reason or "voice_candidate_validation_dispatch_failed"
                                break
                        self._notify_delivery(job)
                        break
                    if event_type in {"speech_segment", "voice_segment"} and job.speech_delivery_enabled:
                        if event_type == "speech_segment" and stream_event.get("speech_protocol") == "speech_units_v1":
                            continue
                        result = bridge.accept_stream_event(stream_event)
                        if not result.accepted and result.status != "duplicate":
                            if self._generation_cancel_requested(job.response_id):
                                cancelled = True
                                failure_reason = ""
                                break
                            failure_reason = result.reason or "voice_speech_segment_dispatch_failed"
                            break
                        delivery_drive = host.drive_once()
                        if delivery_drive.status == "failed":
                            failure_reason = delivery_drive.reason or "voice_playback_offer_failed"
                            break
                        self._notify_delivery(job)
                if not cancelled and not final_seen and not failure_reason:
                    failure_reason = "voice_thinking_final_missing"
            except Exception:
                if self._generation_cancel_requested(job.response_id):
                    cancelled = True
                    failure_reason = ""
                else:
                    failure_reason = "voice_thinking_generation_failed"
            finally:
                try:
                    close_iterator = getattr(iterator, "close", None)
                except Exception:
                    close_iterator = None
                    if not cancelled and not final_seen and not failure_reason:
                        failure_reason = "voice_thinking_stream_close_failed"
                if callable(close_iterator):
                    try:
                        close_iterator()
                    except Exception:
                        if not cancelled and not final_seen and not failure_reason:
                            failure_reason = "voice_thinking_stream_close_failed"

            if self._generation_cancel_requested(job.response_id):
                cancelled = True
                failure_reason = ""
            if failure_reason:
                self._fail_response(
                    host,
                    job,
                    reason=failure_reason,
                    retryable=True,
                )
                self._notify_delivery(job)
        finally:
            self._release_generation_job(job)
            lease = getattr(host, "private_lease", None)
            if lease is not None:
                lease.settle_failed_inputs(host, self)

    def _fail_response(
        self,
        host: AkaneVoiceRuntimeHost,
        job: _GenerationJob,
        *,
        reason: str,
        retryable: bool,
    ) -> None:
        with self._guard:
            failure = self._generation_failures.setdefault(job.response_id, _GenerationFailure(
                host=host, job=job, reason=reason or "voice_thinking_generation_failed", retryable=retryable,
            ))
        self._settle_generation_failure(failure)

    def _retry_failed_generations(
        self, *, voice_turn_id: str = "", commitment: str = "",
    ) -> VoiceThinkingStartResult:
        with self._guard:
            pending = tuple(f for f in self._generation_failures.values()
                            if (not voice_turn_id or f.job.voice_turn_id == voice_turn_id)
                            and (not commitment or f.job.commitment == commitment))
        for failure in pending:
            settled, reason, retryable = self._settle_generation_failure(failure)
            if not settled:
                return VoiceThinkingStartResult(
                    status="failed", reason=reason, retryable=retryable, response_id=failure.job.response_id,
                    safe_public_summary="回复生成已停止，失败状态尚未确认，本轮没有重新生成。",
                )
            self._notify_delivery(failure.job)
        return VoiceThinkingStartResult(status="completed")

    def _settle_generation_failure(self, failure: _GenerationFailure) -> tuple[bool, str, bool]:
        response_id = failure.job.response_id
        with self._guard:
            if self._generation_failures.get(response_id) is not failure:
                return True, "", False
            if response_id in self._settling_failure_ids:
                return False, "voice_thinking_failure_settlement_in_progress", True
            self._settling_failure_ids.add(response_id)
        settled = False
        try:
            response = failure.host.snapshot.responses.get(response_id)
            if response is None or str(getattr(response.state, "value", "")) in {
                "completed", "cancelled", "failed", "discarded",
            }:
                settled = True
                return True, "", False
            if failure.event is None:
                try:
                    failure.event = failure.job.event_factory.make(
                        "voice.response.failed", voice_turn_id=failure.job.voice_turn_id, response_id=response_id,
                        turn_revision=failure.job.turn_revision, response_generation=failure.job.response_generation,
                        payload={
                            "stage": "thinking_agent", "reason_code": failure.reason,
                            "retryable": bool(failure.retryable),
                            "safe_public_summary": "语音内容已经收到，但这次回复没有生成完成。",
                            "affected_ids": [failure.job.voice_turn_id, response_id],
                        },
                    )
                except Exception:
                    return False, "voice_thinking_failure_event_invalid", False
            dispatched = failure.host.accept_event(failure.event)
            if dispatched.accepted or dispatched.status == "duplicate":
                settled = True
                return True, "", False
            journal = dispatched.journal_result
            return False, _safe_failure_reason(dispatched.reason), bool(journal is not None and journal.retryable)
        except Exception:
            return False, "voice_thinking_failure_dispatch_failed", True
        finally:
            with self._guard:
                self._settling_failure_ids.discard(response_id)
                if settled and self._generation_failures.get(response_id) is failure:
                    self._generation_failures.pop(response_id)
            if settled:
                self._release_generation_job(failure.job)
