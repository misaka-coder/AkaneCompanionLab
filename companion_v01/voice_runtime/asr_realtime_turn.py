from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from capcore_adapter_speech import (
    ASRRevisionQuality,
    ASRSessionMode,
    ASRSessionUpdate,
    ASRTranscriptRevision,
    NormalizedASRSession,
)

from .asr_bridge import VoiceASRBridgeResult, VoiceASRSessionBridge


@dataclass(frozen=True)
class VoiceASREarlyCandidate:
    """Stable ASR evidence that may seed non-playable speculative work."""

    candidate_id: str
    voice_turn_id: str
    source_turn_revision: int
    stable_text: str
    language_hint: str | None = None
    provider_receipt_id: str | None = None


@dataclass(frozen=True)
class VoiceASRRealtimeTurnResult:
    status: str
    reason: str = ""
    early_candidate: VoiceASREarlyCandidate | None = None
    provider_update: ASRSessionUpdate | None = None
    bridge_result: VoiceASRBridgeResult | None = None
    final_pending: bool = False
    retryable: bool = False
    provider_status: str = ""
    safe_public_summary: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "accepted", "started", "duplicate"}


class VoiceASRRealtimeTurnCoordinator:
    """Coordinate one provider session without replacing VoiceCore authority.

    A stable checkpoint can be exposed immediately for speculative model work.
    Provider finalization remains a separate awaitable operation, and only its
    normalized final revision is handed to ``VoiceASRSessionBridge`` for the
    authoritative turn commit.
    """

    def __init__(
        self,
        *,
        adapter: Any,
        bridge: VoiceASRSessionBridge,
        filename: str = "akane_voice_input.pcm",
        content_type: str = "audio/pcm",
        language: str = "",
    ) -> None:
        self.adapter = adapter
        self.bridge = bridge
        self.filename = str(filename or "akane_voice_input.pcm")
        self.content_type = str(content_type or "audio/pcm")
        self.language = str(language or "")
        self._open_attempted = False
        self._session: NormalizedASRSession | None = None
        self._finalize_task: asyncio.Task[ASRSessionUpdate] | None = None
        self._settled_result: VoiceASRRealtimeTurnResult | None = None
        self._latest_candidate: VoiceASREarlyCandidate | None = None

    @property
    def latest_candidate(self) -> VoiceASREarlyCandidate | None:
        return self._latest_candidate

    @property
    def final_pending(self) -> bool:
        return self._finalize_task is not None and not self._finalize_task.done()

    async def open(self) -> VoiceASRRealtimeTurnResult:
        if self._open_attempted:
            return VoiceASRRealtimeTurnResult(
                status="duplicate",
                reason="voice_asr_turn_open_already_attempted",
                early_candidate=self._latest_candidate,
                final_pending=self.final_pending,
            )
        self._open_attempted = True

        opened_turn = self.bridge.open_turn()
        if not opened_turn.accepted:
            return VoiceASRRealtimeTurnResult(
                status="failed",
                reason=opened_turn.reason or "voice_asr_turn_open_failed",
                bridge_result=opened_turn,
            )

        try:
            opened_session = await self.adapter.open_session(
                filename=self.filename,
                content_type=self.content_type,
                language=self.language,
            )
        except Exception:
            return self._record_open_failure(
                mode=ASRSessionMode.STREAMING,
                reason="asr_provider_open_failed",
                retryable=True,
            )

        if not opened_session.ok or opened_session.session is None:
            return self._record_open_failure(
                mode=opened_session.mode,
                reason=opened_session.reason or "asr_provider_open_failed",
                retryable=opened_session.retryable,
                provider_status=opened_session.provider_status,
                safe_public_summary=opened_session.safe_public_summary,
            )

        self._session = opened_session.session
        return VoiceASRRealtimeTurnResult(
            status="succeeded",
            bridge_result=opened_turn,
        )

    async def feed_audio(self, audio: bytes) -> VoiceASRRealtimeTurnResult:
        if self._session is None:
            return VoiceASRRealtimeTurnResult(status="failed", reason="voice_asr_turn_not_open")
        if self._finalize_task is not None:
            return VoiceASRRealtimeTurnResult(
                status="failed",
                reason="voice_asr_finalize_already_started",
                early_candidate=self._latest_candidate,
                final_pending=self.final_pending,
            )
        try:
            update = await self._session.feed_audio(audio)
        except Exception:
            update = ASRSessionUpdate.failed(
                ASRSessionMode.STREAMING,
                "asr_provider_feed_audio_failed",
                retryable=True,
            )
        return self._accept_provider_update(update)

    def start_finalize(self) -> VoiceASRRealtimeTurnResult:
        if self._session is None:
            return VoiceASRRealtimeTurnResult(status="failed", reason="voice_asr_turn_not_open")
        if self._settled_result is not None:
            return VoiceASRRealtimeTurnResult(
                status="duplicate",
                reason="voice_asr_finalize_already_settled",
                early_candidate=self._latest_candidate,
                provider_update=self._settled_result.provider_update,
                bridge_result=self._settled_result.bridge_result,
            )
        if self._finalize_task is not None:
            return VoiceASRRealtimeTurnResult(
                status="duplicate",
                reason="voice_asr_finalize_already_started",
                early_candidate=self._latest_candidate,
                final_pending=self.final_pending,
            )
        self._finalize_task = asyncio.create_task(
            self._session.finalize(),
            name=f"voice-asr-finalize-{self.bridge.voice_turn_id}",
        )
        return VoiceASRRealtimeTurnResult(
            status="started",
            early_candidate=self._latest_candidate,
            final_pending=True,
        )

    async def settle_finalize(self) -> VoiceASRRealtimeTurnResult:
        if self._settled_result is not None:
            return VoiceASRRealtimeTurnResult(
                status="duplicate",
                reason="voice_asr_finalize_already_settled",
                early_candidate=self._latest_candidate,
                provider_update=self._settled_result.provider_update,
                bridge_result=self._settled_result.bridge_result,
            )
        if self._session is None:
            return VoiceASRRealtimeTurnResult(status="failed", reason="voice_asr_turn_not_open")
        if self._finalize_task is None:
            started = self.start_finalize()
            if not started.ok:
                return started
        assert self._finalize_task is not None
        try:
            update = await asyncio.shield(self._finalize_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            update = ASRSessionUpdate.failed(
                ASRSessionMode.STREAMING,
                "asr_provider_finalize_failed",
                retryable=True,
            )
        result = self._accept_provider_update(update)
        self._settled_result = result
        return result

    async def cancel(self, *, reason: str = "cancelled") -> VoiceASRRealtimeTurnResult:
        if self._settled_result is not None:
            return VoiceASRRealtimeTurnResult(
                status="failed",
                reason="voice_asr_turn_already_terminal",
                early_candidate=self._latest_candidate,
            )
        if self._session is None:
            return VoiceASRRealtimeTurnResult(status="failed", reason="voice_asr_turn_not_open")
        finalize_task = self._finalize_task
        if finalize_task is not None and not finalize_task.done():
            finalize_task.cancel()
            await asyncio.gather(finalize_task, return_exceptions=True)
        try:
            update = await self._session.cancel()
        except Exception:
            update = ASRSessionUpdate.failed(
                ASRSessionMode.STREAMING,
                "asr_provider_cancel_failed",
                retryable=True,
            )
        if update.status == "cancelled" and reason and update.reason != reason:
            update = ASRSessionUpdate.cancelled(update.mode, reason)
        result = self._accept_provider_update(update)
        self._settled_result = result
        return result

    def _record_open_failure(
        self,
        *,
        mode: ASRSessionMode,
        reason: str,
        retryable: bool,
        provider_status: str = "",
        safe_public_summary: str = "",
    ) -> VoiceASRRealtimeTurnResult:
        update = ASRSessionUpdate.failed(
            mode,
            reason,
            retryable=retryable,
            provider_status=provider_status,
            safe_public_summary=safe_public_summary,
        )
        result = self._accept_provider_update(update)
        return VoiceASRRealtimeTurnResult(
            status="failed",
            reason=reason,
            provider_update=update,
            bridge_result=result.bridge_result,
            retryable=retryable,
            provider_status=provider_status,
            safe_public_summary=safe_public_summary,
        )

    def _accept_provider_update(self, update: ASRSessionUpdate) -> VoiceASRRealtimeTurnResult:
        bridge_result = self.bridge.accept_update(update)
        candidate = self._candidate_from_update(update) if bridge_result.accepted else None
        if candidate is not None:
            self._latest_candidate = candidate

        if update.status == "failed":
            return VoiceASRRealtimeTurnResult(
                status="failed",
                reason=update.reason,
                early_candidate=self._latest_candidate,
                provider_update=update,
                bridge_result=bridge_result,
                final_pending=self.final_pending,
                retryable=update.retryable,
                provider_status=update.provider_status,
                safe_public_summary=update.safe_public_summary,
            )
        if bridge_result.status == "failed":
            return VoiceASRRealtimeTurnResult(
                status="failed",
                reason=bridge_result.reason,
                early_candidate=self._latest_candidate,
                provider_update=update,
                bridge_result=bridge_result,
                final_pending=self.final_pending,
            )
        return VoiceASRRealtimeTurnResult(
            status=update.status,
            reason=update.reason or bridge_result.reason,
            early_candidate=candidate,
            provider_update=update,
            bridge_result=bridge_result,
            final_pending=self.final_pending,
        )

    def _candidate_from_update(
        self,
        update: ASRSessionUpdate,
    ) -> VoiceASREarlyCandidate | None:
        checkpoint: ASRTranscriptRevision | None = None
        for revision in update.revisions:
            if (
                revision.quality is ASRRevisionQuality.STABLE_CHECKPOINT
                and revision.control_significant
                and revision.stable_text.strip()
            ):
                checkpoint = revision
        if checkpoint is None:
            return None
        receipt = str(checkpoint.provider_receipt_id or "")
        material = f"{self.bridge.voice_turn_id}:{checkpoint.revision}:{receipt}:{checkpoint.stable_text}"
        return VoiceASREarlyCandidate(
            candidate_id="voice_candidate_" + sha256(material.encode("utf-8")).hexdigest()[:32],
            voice_turn_id=self.bridge.voice_turn_id,
            source_turn_revision=checkpoint.revision,
            stable_text=checkpoint.stable_text.strip(),
            language_hint=checkpoint.language_hint,
            provider_receipt_id=checkpoint.provider_receipt_id,
        )
