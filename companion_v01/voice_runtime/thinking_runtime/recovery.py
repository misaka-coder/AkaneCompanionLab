"""Restore pending generation context and fail unverifiable pre-restart streams."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..host import AkaneVoiceRuntimeHost
from .context import VoiceThinkingStartResult, _GenerationJob


class GenerationRecovery:
    """Recovery uses the same job registry and Host receipt path."""

    def restore_generating_jobs(
        self,
        snapshot_record: Mapping[str, Any],
    ) -> VoiceThinkingStartResult:
        with self._guard:
            if self._closed:
                return VoiceThinkingStartResult(status="failed", reason="voice_thinking_executor_closed", retryable=True)
        responses = snapshot_record.get("responses")
        if not isinstance(responses, Mapping):
            return VoiceThinkingStartResult(
                status="failed",
                reason="voice_thinking_snapshot_invalid",
            )
        restored = 0
        for raw_response in responses.values():
            if not isinstance(raw_response, Mapping):
                continue
            if str(raw_response.get("state") or "") != "generating":
                continue
            response_id = str(raw_response.get("response_id") or "").strip()
            voice_turn_id = str(raw_response.get("voice_turn_id") or "").strip()
            response_generation = raw_response.get("response_generation")
            turn_revision = raw_response.get("source_turn_revision")
            if (
                not response_id
                or not voice_turn_id
                or isinstance(response_generation, bool)
                or not isinstance(response_generation, int)
                or isinstance(turn_revision, bool)
                or not isinstance(turn_revision, int)
            ):
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_snapshot_response_invalid",
                )
            resolved = self._resolve_turn_context(
                snapshot_record=snapshot_record,
                voice_turn_id=voice_turn_id,
            )
            if not resolved.get("ok"):
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=str(resolved.get("reason") or "voice_thinking_turn_context_unavailable"),
                    retryable=str(resolved.get("status") or "")
                    in {
                        "failed",
                        "unavailable",
                    },
                )
            with self._guard:
                if self._closed:
                    return VoiceThinkingStartResult(status="failed", reason="voice_thinking_executor_closed", retryable=True)
                self._jobs.setdefault(
                    response_id,
                    _GenerationJob(
                        response_id=response_id,
                        voice_turn_id=voice_turn_id,
                        response_generation=response_generation,
                        turn_revision=turn_revision,
                        commitment=str(raw_response.get("commitment") or "committed"),
                        candidate_id=str(raw_response.get("candidate_id") or ""),
                        source_id=str(resolved["source_id"]),
                        memcore_turn_id=str(resolved["turn_id"]),
                        message=str(resolved["text"]),
                        timestamp=int(resolved.get("timestamp") or 0),
                        event_factory=self._event_factory_for_turn(voice_turn_id),
                        speech_delivery_enabled=False,
                    ),
                )
            restored += 1
        return VoiceThinkingStartResult(
            status="started" if restored else "duplicate",
            reason="" if restored else "voice_thinking_no_generation_to_restore",
        )

    def fail_restarted_generations(
        self,
        host: AkaneVoiceRuntimeHost,
        *,
        response_ids: tuple[str, ...],
    ) -> VoiceThinkingStartResult:
        failed: list[str] = []
        for response_id in response_ids:
            response = host.snapshot.responses.get(str(response_id or ""))
            if response is None or str(getattr(response.state, "value", "")) not in {
                "generating",
                "streaming",
            }:
                continue
            voice_turn_id = str(getattr(response, "voice_turn_id", "") or "")
            try:
                event = self._event_factory_for_turn(voice_turn_id).make(
                    "voice.response.failed",
                    voice_turn_id=voice_turn_id,
                    response_id=str(response.response_id),
                    turn_revision=int(response.source_turn_revision),
                    response_generation=int(response.response_generation),
                    payload={
                        "stage": "thinking_agent",
                        "reason_code": "voice_thinking_runtime_restarted",
                        "retryable": True,
                        "safe_public_summary": "语音回复生成期间服务已重启，本轮没有伪装成完成。",
                        "affected_ids": [voice_turn_id, str(response.response_id)],
                    },
                )
            except Exception:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_restart_failure_event_invalid",
                    retryable=False,
                )
            dispatched = host.accept_event(event)
            if not dispatched.accepted:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=dispatched.reason or "voice_thinking_restart_failure_dispatch_failed",
                    retryable=True,
                )
            failed.append(str(response.response_id))
        return VoiceThinkingStartResult(
            status="completed" if failed else "duplicate",
            reason="" if failed else "voice_thinking_no_stale_generation",
            response_id=failed[0] if failed else "",
        )
