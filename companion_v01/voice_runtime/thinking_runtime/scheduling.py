"""Start model work only after the Host applies generation_started."""
from __future__ import annotations

from ..host import AkaneVoiceRuntimeHost
from .context import VoiceThinkingStartResult


class GenerationScheduling:
    """Ready job admission and background submission share the owning executor."""

    def start_ready_generations(
        self,
        host: AkaneVoiceRuntimeHost,
        *,
        voice_turn_id: str = "",
        commitment: str = "",
    ) -> VoiceThinkingStartResult:
        target_turn_id = str(voice_turn_id or "").strip()
        target_commitment = str(commitment or "").strip()
        with self._guard:
            if self._closed:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_executor_closed",
                    retryable=True,
                    safe_public_summary="回复生成服务正在关闭。",
                )
        settled = self._retry_failed_generations(voice_turn_id=target_turn_id, commitment=target_commitment)
        if not settled.ok:
            return settled
        with self._guard:
            if self._closed:
                return VoiceThinkingStartResult(status="failed", reason="voice_thinking_executor_closed", retryable=True)
            ready = [
                job
                for job in self._jobs.values()
                if (not target_turn_id or job.voice_turn_id == target_turn_id)
                and (not target_commitment or job.commitment == target_commitment)
                and job.response_id not in self._running_response_ids
                and job.response_id not in self._generation_failures
            ]
        if not ready:
            existing = self._response_for_turn(host, target_turn_id)
            with self._guard:
                if self._closed:
                    return VoiceThinkingStartResult(status="failed", reason="voice_thinking_executor_closed", retryable=True)
                already_running = existing is not None and existing.response_id in self._running_response_ids
            if already_running:
                return VoiceThinkingStartResult(status="started", response_id=str(existing.response_id))
            if existing is not None and str(getattr(existing.state, "value", "")) in {
                "generated",
                "completed",
            }:
                return VoiceThinkingStartResult(
                    status="completed",
                    response_id=str(existing.response_id),
                )
            return VoiceThinkingStartResult(
                status="duplicate",
                reason="voice_thinking_generation_already_started",
                response_id=str(getattr(existing, "response_id", "") or ""),
            )

        started: list[str] = []
        already_started: list[str] = []
        for job in ready:
            response = host.snapshot.responses.get(job.response_id)
            if response is None or str(getattr(response.state, "value", "")) != "generating":
                continue
            try:
                with self._guard:
                    if self._closed:
                        return VoiceThinkingStartResult(status="failed", reason="voice_thinking_executor_closed", retryable=True)
                    if self._jobs.get(job.response_id) is not job:
                        continue
                    if job.response_id in self._running_response_ids:
                        already_started.append(job.response_id)
                        continue
                    self._running_response_ids.add(job.response_id)
                    self.background_tasks.submit(
                        lane=f"voice-thinking-{self.conversation_id[-16:]}",
                        name=f"voice_response:{job.response_id[-16:]}",
                        fn=self._run_generation,
                        args=(host, job),
                    )
            except Exception:
                try:
                    self._fail_response(host, job, reason="voice_thinking_schedule_failed", retryable=True)
                finally:
                    self._release_generation_job(job)
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_schedule_failed",
                    retryable=True,
                    safe_public_summary="回复生成任务暂时无法启动。",
                    response_id=job.response_id,
                )
            started.append(job.response_id)
        if not started:
            if already_started:
                return VoiceThinkingStartResult(status="duplicate", reason="voice_thinking_generation_already_started",
                                                response_id=already_started[0])
            return VoiceThinkingStartResult(
                status="failed",
                reason="voice_thinking_response_not_generating",
                retryable=False,
                safe_public_summary="语音回复状态不一致，本轮没有继续生成。",
            )
        return VoiceThinkingStartResult(
            status="started",
            response_id=started[0],
        )
