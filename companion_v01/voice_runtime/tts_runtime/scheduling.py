"""Bounded background synthesis and original Host receipt completion."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..host import VoiceCommandExecutionResult, VoiceCommandReceiptDrainResult
from .commands import _TTSJob, logger


class TTSScheduling:
    def _schedule(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, _payload, common = context
        command_id = str(command["command_id"])
        with self._guard:
            host = self._host
            refresh = any(job.result is not None for job in self._jobs.values())
        if refresh and host is not None:
            self._refresh_confirmations(host)
        with self._guard:
            self._prune_settled(snapshot_record.get("pending_commands", {}))
            existing = self._jobs.get(command_id)
            if existing is not None:
                if existing.command != command:
                    return VoiceCommandExecutionResult.failed("voice_tts_job_conflict")
                return existing.result or VoiceCommandExecutionResult.running("voice_tts_running")
            if self._closed or self._host is None:
                return self._preflight_failure(command, common, "voice_tts_executor_closed")
            if not self._live_commands:
                # There is no owning playback client during restart recovery.
                # Do not spend GPU/provider work on an orphaned unstarted unit.
                return self._preflight_failure(command, common, "voice_tts_runtime_restarted")
            # At most two scheduled/running synthesis jobs per conversation.
            # All conversations share one synthesis lane, including local GPU TTS.
            # Further work stays in VoiceCore's existing pending command queue.
            if len(self._jobs) >= 2:
                return VoiceCommandExecutionResult.deferred("voice_tts_capacity_wait")
            job = self._make_job(command, common)
            self._jobs[command_id] = job
            try:
                self.background_tasks.submit(
                    lane="voice-tts",
                    name="voice_tts_synthesis",
                    fn=self._run_job,
                    args=(job,),
                )
            except Exception:
                # Keep the exact failure observations until the receipt confirms them.
                job.result = self._failed_observation(
                    command=command, common=common, reason="voice_tts_schedule_failed", started=False,
                )
            return job.result or VoiceCommandExecutionResult.running("voice_tts_running")

    def _preflight_failure(
        self, command: dict[str, Any], common: Mapping[str, Any], reason: str,
    ) -> VoiceCommandExecutionResult:
        result = self._failed_observation(command=command, common=common, reason=reason, started=False)
        if self._host is not None:
            # A bound Host owns confirmation even when synthesis never started.
            self._jobs[str(command["command_id"])] = self._make_job(command, common, result)
        return result

    def _prune_settled(self, pending: Mapping[str, Any]) -> None:
        # Keep running jobs counted even after cancellation so rapid new turns
        # cannot grow an unbounded queue of obsolete provider calls.
        for command_id, job in tuple(self._jobs.items()):
            if job.result is not None and job.receipt_settled and command_id not in pending:
                self._jobs.pop(command_id, None)

    def _refresh_confirmations(self, host: Any, confirmation: VoiceCommandReceiptDrainResult | None = None) -> bool:
        # Snapshot removal precedes the receipt completion/release write.
        # Only the actual journal can confirm the latter, including lost ACKs.
        try:
            result = confirmation if confirmation is not None else host.drain_command_receipts()
            if not isinstance(result, VoiceCommandReceiptDrainResult):
                return False
            unfinished = set(result.pending_receipt_command_ids)
            if not result.quiescent and not unfinished:
                return False
            pending = host.snapshot.pending_commands
        except Exception:
            return False
        with self._guard:
            for command_id, job in self._jobs.items():
                if job.result is not None and command_id not in pending and command_id not in unfinished:
                    job.receipt_settled = True
        return True

    def _run_job(self, job: _TTSJob) -> None:
        command_id = str(job.command["command_id"])
        with self._guard:
            if self._jobs.get(command_id) is not job or (job.started and job.result is None):
                return
            host = self._host
            closed = self._closed
            needs_synthesis = job.result is None
            if needs_synthesis:
                job.started = True
        if needs_synthesis:
            try:
                if host is None or command_id not in host.snapshot.pending_commands:
                    result = VoiceCommandExecutionResult.not_started("invocation_cancelled")
                elif closed:
                    context = self._command_context(job.command, job.snapshot)
                    command, _payload, common = context
                    result = self._failed_observation(
                        command=command, common=common, reason="voice_tts_executor_closed", started=False,
                    )
                else:
                    result = self._synthesize(job.command, job.snapshot)
            except Exception:
                # Persisted executing receipt remains recoverable if an artifact
                # write had an unknown outcome; never blindly retry synthesis.
                result = VoiceCommandExecutionResult.unknown("voice_tts_execution_failed")
                logger.warning("voice_tts_execution_outcome_unknown")
            if result is not None:
                with self._guard:
                    job.result = result
        if host is None:
            return
        # Apply through the same serialized Host/receipt path as synchronous
        # commands. A cancelled command's observations are never replayed.
        try:
            for _ in range(8):
                before = host.snapshot
                driven = host.drive_once()
                if driven.status == "failed":
                    logger.warning("voice_tts_completion_dispatch_failed")
                    break
                if host.snapshot is before:
                    break
        except Exception:
            logger.warning("voice_tts_completion_dispatch_failed")
        finally:
            self._refresh_confirmations(host)
            with self._guard:
                self._prune_settled(host.snapshot.pending_commands)
            self._notify_job_delivery(job)

    def _notify_job_delivery(self, job: _TTSJob) -> None:
        with self._guard:
            notifier = self._notify_delivery
        response_id = str(job.command.get("payload", {}).get("response_id") or "")
        response = job.snapshot.get("responses", {}).get(response_id, {})
        if notifier is not None:
            try:
                notifier(str(response.get("voice_turn_id") or ""))
            except Exception:
                logger.warning("voice_tts_delivery_notification_failed")
