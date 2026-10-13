"""Drive durable pending commands and start one response without silent stalls."""
from __future__ import annotations

from typing import Any

from ..thinking_agent import AkaneThinkingAgentCommandExecutor, VoiceThinkingStartResult
from .assembly import _SerializedVoiceRuntimeHost


class RuntimeRecovery:
    """Recovery methods share the owning service state and lock."""

    @staticmethod
    def _snapshot_record(host: _SerializedVoiceRuntimeHost) -> dict[str, Any]:
        from voicecore import snapshot_to_dict

        return snapshot_to_dict(host.snapshot)

    @staticmethod
    def _recover_pending_commands(
        host: _SerializedVoiceRuntimeHost,
    ) -> VoiceThinkingStartResult:
        seen_pending: set[tuple[str, ...]] = set()
        while host.snapshot.pending_commands:
            pending_ids = tuple(host.snapshot.pending_commands)
            if pending_ids in seen_pending:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_command_recovery_stalled",
                    retryable=True,
                )
            seen_pending.add(pending_ids)
            pending = [host.snapshot.pending_commands[command_id] for command_id in pending_ids]
            if any(
                str(getattr(command, "command_kind", "") or "")
                not in {
                    "start_response_generation",
                    "cancel_response_generation",
                    "start_tts",
                    "enqueue_playback",
                    "request_semantic_pulse",
                    "validate_response_candidate",
                }
                for command in pending
            ):
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_command_recovery_unsupported",
                    retryable=False,
                )
            driven = host.drive_once()
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_command_recovery_failed",
                    retryable=(
                        driven.status == "deferred" or any(result.retryable for result in driven.command_results)
                    ),
                )
        return VoiceThinkingStartResult(status="completed")

    @staticmethod
    def _start_response_generation(
        *,
        host: _SerializedVoiceRuntimeHost,
        executor: AkaneThinkingAgentCommandExecutor,
        voice_turn_id: str,
    ) -> VoiceThinkingStartResult:
        seen_states: set[tuple[tuple[str, ...], str, str]] = set()
        while True:
            responses = [
                item
                for item in host.snapshot.responses.values()
                if str(getattr(item, "voice_turn_id", "") or "") == str(voice_turn_id or "")
            ]
            response = next(
                (
                    item
                    for item in responses
                    if str(getattr(getattr(item, "state", None), "value", "") or "") == "generating"
                ),
                None,
            )
            if response is None:
                response = next(
                    (
                        item
                        for item in reversed(responses)
                        if str(
                            getattr(
                                getattr(item, "state", None),
                                "value",
                                "",
                            )
                            or ""
                        )
                        not in {"failed", "cancelled", "discarded"}
                    ),
                    responses[-1] if responses else None,
                )
            response_state = str(getattr(getattr(response, "state", None), "value", "") or "")
            response_id = str(getattr(response, "response_id", "") or "")
            if response_state == "generating":
                return executor.start_ready_generations(
                    host,
                    voice_turn_id=voice_turn_id,
                )
            pending_ids = tuple(host.snapshot.pending_commands)
            if response_state == "completed":
                return VoiceThinkingStartResult(
                    status="completed",
                    response_id=response_id,
                )
            if response_state in {"failed", "cancelled", "discarded"} and not pending_ids:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_start_terminal",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复没有进入生成状态。",
                )
            if (
                response_state in {"streaming", "generated"}
                and str(
                    getattr(
                        getattr(response, "commitment", None),
                        "value",
                        "",
                    )
                    or ""
                )
                in {"speculative", "committed"}
                and not pending_ids
            ):
                return VoiceThinkingStartResult(
                    status="started",
                    response_id=response_id,
                )
            state_key = (pending_ids, response_id, response_state)
            if not pending_ids or state_key in seen_states:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_start_stalled",
                    retryable=True,
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复生成暂时没有启动。",
                )
            seen_states.add(state_key)
            driven = host.drive_once()
            if driven.status == "deferred" and any(
                command.command_kind == "validate_response_candidate"
                for command in host.snapshot.pending_commands.values()
            ):
                return VoiceThinkingStartResult(
                    status="started",
                    response_id=response_id,
                )
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_response_start_failed",
                    retryable=driven.status == "deferred",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复生成暂时无法启动。",
                )
