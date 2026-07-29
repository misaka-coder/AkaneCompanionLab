from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from voicecore import VoiceEvent, snapshot_to_dict

from ..background_tasks import BackgroundTaskRunner
from ..memcore_integration.manager import resolve_memcore_provider_profile
from .host import (
    AkaneVoiceRuntimeHost,
    VoiceCommandExecutionResult,
    VoiceHostPortResult,
)

logger = logging.getLogger(__name__)


_CANDIDATE_VALIDATOR_PROMPT = """
你是实时语音候选回复校验器，只判断一份根据临时转写提前生成的候选回复，
能否安全地作为最终用户发言的正式回复。

只返回一个 JSON 对象：
{
  "compatible": true,
  "reason_code": "简短、稳定的英文原因码"
}

只有候选回复完整覆盖最终发言、没有违背后来新增的限定、纠正、对象或意图，
也没有把未执行的工具或外部动作说成已完成时，compatible 才能为 true。
最终发言增加了实质条件、改变了请求、否定了临时转写，或者证据不足时返回 false。
不要回复用户，不调用工具，不执行外部动作。
""".strip()


@dataclass(frozen=True)
class _CandidateValidationJob:
    command_id: str
    idempotency_key: str
    voice_turn_id: str
    voice_session_id: str
    response_id: str
    response_generation: int
    candidate_id: str
    source_turn_revision: int
    final_turn_revision: int
    source_text: str
    final_text: str
    candidate_text: str


class AkaneVoiceCandidateValidationCommandExecutor:
    """Validate a completed speculative reply before VoiceCore can play it."""

    def __init__(
        self,
        *,
        engine: Any,
        memcore_manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        conversation_id: str,
        conversation_generation: int,
        background_tasks: BackgroundTaskRunner,
        generation_starter: Callable[[AkaneVoiceRuntimeHost, str], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.memcore_manager = memcore_manager
        self.profile_user_id = str(profile_user_id or "")
        self.session_id = str(session_id or "")
        self.character_pack_id = str(character_pack_id or "")
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self.background_tasks = background_tasks
        self.generation_starter = generation_starter
        self._host: AkaneVoiceRuntimeHost | None = None
        self._jobs: dict[str, _CandidateValidationJob] = {}
        self._guard = threading.RLock()
        self._closed = False
        self._live_commands_enabled = False

    def bind_host(self, host: AkaneVoiceRuntimeHost) -> None:
        if host is None:
            raise ValueError("voice_candidate_host_required")
        with self._guard:
            if self._host is not None and self._host is not host:
                raise RuntimeError("voice_candidate_host_conflict")
            self._host = host

    def enable_live_commands(self) -> None:
        with self._guard:
            if self._closed:
                raise RuntimeError("voice_candidate_executor_closed")
            if self._host is None:
                raise RuntimeError("voice_candidate_host_unavailable")
            self._live_commands_enabled = True

    def discard_restarted_candidates(
        self,
        host: AkaneVoiceRuntimeHost,
    ) -> VoiceHostPortResult:
        for response in tuple(host.snapshot.responses.values()):
            if str(getattr(getattr(response, "commitment", None), "value", "") or "") != "speculative" or str(
                getattr(getattr(response, "state", None), "value", "") or ""
            ) in {"completed", "cancelled", "failed", "discarded"}:
                continue
            voice_turn_id = str(getattr(response, "voice_turn_id", "") or "")
            candidate_id = str(getattr(response, "candidate_id", "") or "")
            source_revision = int(getattr(response, "source_turn_revision", 0) or 0)
            turn = host.snapshot.input_turns.get(voice_turn_id)
            final_revision = int(getattr(turn, "final_revision", 0) or 0)
            validated_revision = final_revision or source_revision
            if not voice_turn_id or not candidate_id or source_revision < 1 or validated_revision < 1:
                return VoiceHostPortResult.failed(
                    "voice_candidate_restart_snapshot_invalid",
                )
            command_id = f"voice-candidate-restart:{response.response_id}"
            event = self._validation_event(
                _CandidateValidationJob(
                    command_id=command_id,
                    idempotency_key=command_id,
                    voice_turn_id=voice_turn_id,
                    voice_session_id="voice-session-recovery",
                    response_id=str(response.response_id),
                    response_generation=int(response.response_generation),
                    candidate_id=candidate_id,
                    source_turn_revision=source_revision,
                    final_turn_revision=validated_revision,
                    source_text="",
                    final_text="",
                    candidate_text=str(getattr(response, "full_text", "") or ""),
                ),
                compatible=False,
                reason_code="voice_candidate_runtime_restarted",
                start_fallback=False,
            )
            accepted = host.accept_event(event)
            if accepted.status not in {"accepted", "duplicate"}:
                return VoiceHostPortResult.failed(
                    accepted.reason or "voice_candidate_restart_discard_failed",
                    retryable=accepted.status == "deferred",
                )
        return VoiceHostPortResult.succeeded()

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        job = self._resolve_job(command_record, snapshot_record)
        if isinstance(job, str):
            return VoiceCommandExecutionResult.failed(job)
        with self._guard:
            if self._closed or self._host is None:
                return VoiceCommandExecutionResult.failed(
                    "voice_candidate_host_unavailable",
                    retryable=True,
                )
            if not self._live_commands_enabled:
                return VoiceCommandExecutionResult.failed(
                    "voice_candidate_live_runtime_unavailable",
                    retryable=True,
                )
            existing = self._jobs.get(job.command_id)
            if existing is not None:
                if existing != job:
                    return VoiceCommandExecutionResult.failed("voice_candidate_job_conflict")
                return VoiceCommandExecutionResult.deferred("voice_candidate_validation_running")
            self._jobs[job.command_id] = job
        try:
            self.background_tasks.submit(
                lane=f"voice-candidate-{self.conversation_id[-16:]}",
                name=f"voice_candidate_validation:{job.command_id[-16:]}",
                fn=self._run_job,
                args=(job,),
            )
        except Exception:
            with self._guard:
                self._jobs.pop(job.command_id, None)
            return VoiceCommandExecutionResult.succeeded(
                self._validation_event(
                    job,
                    compatible=False,
                    reason_code="voice_candidate_schedule_failed",
                )
            )
        return VoiceCommandExecutionResult.deferred("voice_candidate_validation_running")

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        job = self._resolve_job(command_record, snapshot_record)
        if isinstance(job, str):
            return VoiceCommandExecutionResult.failed(job)
        # The playback channel that owned this live candidate does not survive
        # a process restart. Discard it truthfully instead of generating an old
        # answer into a new call.
        return VoiceCommandExecutionResult.succeeded(
            self._validation_event(
                job,
                compatible=False,
                reason_code="voice_candidate_runtime_restarted",
                start_fallback=False,
            )
        )

    def close(self) -> None:
        with self._guard:
            self._closed = True
            self._live_commands_enabled = False
            self._host = None
            self._jobs.clear()

    def _run_job(self, job: _CandidateValidationJob) -> None:
        try:
            compatible, reason_code = self._validate_with_model(job)
        except Exception:
            compatible = False
            reason_code = "voice_candidate_validator_failed"
        with self._guard:
            host = self._host
            closed = self._closed
        if host is not None and not closed:
            try:
                accepted = host.accept_event(
                    self._validation_event(
                        job,
                        compatible=compatible,
                        reason_code=reason_code,
                    )
                )
                if accepted.status in {"accepted", "duplicate"}:
                    self._drive_available_commands(host)
                    self._start_generation_if_ready(host, job.voice_turn_id)
                else:
                    logger.warning(
                        "voice candidate validation dispatch failed status=%s reason=%s",
                        accepted.status,
                        accepted.reason,
                    )
            except Exception as exc:
                logger.warning(
                    "voice candidate validation dispatch failed error_type=%s",
                    type(exc).__name__,
                )
        with self._guard:
            self._jobs.pop(job.command_id, None)

    def _validate_with_model(
        self,
        job: _CandidateValidationJob,
    ) -> tuple[bool, str]:
        llm = getattr(self.engine, "llm", None)
        if llm is None:
            return False, "voice_candidate_model_unavailable"
        history = self._build_history_turns()
        if isinstance(history, str):
            return False, history
        user_prompt = "[host.voice.candidate_validation]\n" + json.dumps(
            {
                "provisional_user_text": job.source_text,
                "final_user_text": job.final_text,
                "candidate_reply": job.candidate_text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request = {
            "system_prompt": _CANDIDATE_VALIDATOR_PROMPT,
            "user_prompt": user_prompt,
            "fallback": {},
            "temperature": 0.0,
            "prompt_cache_key": self._prompt_cache_key(),
            "history_turns": history,
            "native_tools": None,
            "native_tool_choice": "",
            "prompt_audit_sections": [
                {
                    "name": "system.voice_candidate_validator",
                    "text": _CANDIDATE_VALIDATOR_PROMPT,
                },
                {
                    "name": "user.voice_candidate_validator",
                    "text": user_prompt,
                },
            ],
        }
        call_result = getattr(llm, "call_chat_json_result", None)
        if callable(call_result):
            result = call_result(**request)
            if bool(getattr(result, "fallback_used", False)) or str(getattr(result, "error", "") or ""):
                return False, "voice_candidate_model_failed"
            parsed = getattr(result, "parsed", None)
        else:
            call = getattr(llm, "call_chat_json", None)
            if not callable(call):
                return False, "voice_candidate_model_unavailable"
            parsed = call(**request)
        if not isinstance(parsed, Mapping) or not isinstance(parsed.get("compatible"), bool):
            return False, "voice_candidate_result_invalid"
        compatible = bool(parsed["compatible"])
        reason_code = _safe_reason(
            parsed.get("reason_code"),
            fallback=("voice_candidate_compatible" if compatible else "voice_candidate_incompatible"),
        )
        return compatible, reason_code

    def _build_history_turns(self) -> list[dict[str, Any]] | str:
        llm = getattr(self.engine, "llm", None)
        protocol_getter = getattr(llm, "chat_provider_protocol", None)
        try:
            protocol = str(protocol_getter() or "") if callable(protocol_getter) else "openai"
        except Exception:
            protocol = ""
        provider_profile = resolve_memcore_provider_profile(protocol)
        if not provider_profile:
            return "voice_candidate_provider_profile_unavailable"
        try:
            projection = self.memcore_manager.build_context_projection(
                provider_profile=provider_profile,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
            )
        except Exception:
            return "voice_candidate_history_read_failed"
        if not isinstance(projection, Mapping) or not bool(projection.get("ok")):
            return "voice_candidate_history_unavailable"
        return [dict(payload) for payload in list(projection.get("payloads") or []) if isinstance(payload, Mapping)]

    def _resolve_job(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> _CandidateValidationJob | str:
        if (
            not isinstance(command_record, Mapping)
            or not isinstance(snapshot_record, Mapping)
            or str(command_record.get("command_kind") or "") != "validate_response_candidate"
        ):
            return "voice_candidate_command_invalid"
        command_id = str(command_record.get("command_id") or "").strip()
        idempotency_key = str(command_record.get("idempotency_key") or "").strip()
        payload = command_record.get("payload")
        if not command_id or not idempotency_key or not isinstance(payload, Mapping):
            return "voice_candidate_command_invalid"
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        response_id = str(payload.get("response_id") or "").strip()
        candidate_id = str(payload.get("candidate_id") or "").strip()
        response_generation = payload.get("response_generation")
        source_revision = payload.get("source_turn_revision")
        final_revision = payload.get("final_turn_revision")
        responses = snapshot_record.get("responses")
        turns = snapshot_record.get("input_turns")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        turn = turns.get(voice_turn_id) if isinstance(turns, Mapping) else None
        if (
            not voice_turn_id
            or not response_id
            or not candidate_id
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
            or isinstance(source_revision, bool)
            or not isinstance(source_revision, int)
            or isinstance(final_revision, bool)
            or not isinstance(final_revision, int)
            or not isinstance(response, Mapping)
            or not isinstance(turn, Mapping)
            or str(response.get("candidate_id") or "") != candidate_id
            or str(response.get("commitment") or "") != "speculative"
            or str(response.get("state") or "") != "generated"
            or int(response.get("response_generation") or 0) != response_generation
            or int(response.get("source_turn_revision") or 0) != source_revision
            or int(turn.get("final_revision") or 0) != final_revision
        ):
            return "voice_candidate_snapshot_invalid"
        revisions = turn.get("revisions")
        source = None
        final = None
        if isinstance(revisions, Mapping):
            source = revisions.get(str(source_revision), revisions.get(source_revision))
            final = revisions.get(str(final_revision), revisions.get(final_revision))
        candidate_text = str(response.get("full_text") or "").strip()
        if not isinstance(source, Mapping) or not isinstance(final, Mapping) or not candidate_text:
            return "voice_candidate_evidence_unavailable"
        return _CandidateValidationJob(
            command_id=command_id,
            idempotency_key=idempotency_key,
            voice_turn_id=voice_turn_id,
            voice_session_id=str(turn.get("voice_session_id") or ""),
            response_id=response_id,
            response_generation=response_generation,
            candidate_id=candidate_id,
            source_turn_revision=source_revision,
            final_turn_revision=final_revision,
            source_text=_revision_text(source),
            final_text=_revision_text(final),
            candidate_text=candidate_text,
        )

    def _validation_event(
        self,
        job: _CandidateValidationJob,
        *,
        compatible: bool,
        reason_code: str,
        start_fallback: bool = True,
    ) -> VoiceEvent:
        safe_reason = _safe_reason(
            reason_code,
            fallback=("voice_candidate_compatible" if compatible else "voice_candidate_incompatible"),
        )
        digest = hashlib.sha256(
            f"{job.command_id}:{compatible}:{safe_reason}:{start_fallback}".encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=f"voice_evt_{digest[:32]}",
            event_kind="voice.candidate.validation_result",
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=job.voice_session_id,
            conversation_generation=self.conversation_generation,
            producer="akane.voice_candidate_validator",
            voice_turn_id=job.voice_turn_id,
            response_id=job.response_id,
            turn_revision=job.final_turn_revision,
            response_generation=job.response_generation,
            causation_id=job.command_id,
            payload={
                "command_id": job.command_id,
                "candidate_id": job.candidate_id,
                "source_turn_revision": job.source_turn_revision,
                "validated_against_turn_revision": job.final_turn_revision,
                "compatible": bool(compatible),
                "validator_id": "akane.voice_candidate_validator.v1",
                "reason_code": safe_reason,
                "start_fallback": bool(start_fallback),
            },
        )

    @staticmethod
    def _drive_available_commands(host: AkaneVoiceRuntimeHost) -> None:
        seen: set[tuple[str, ...]] = set()
        while True:
            pending = tuple(host.snapshot.pending_commands)
            if not pending or pending in seen:
                return
            seen.add(pending)
            result = host.drive_once()
            if result.status != "succeeded":
                return

    def _start_generation_if_ready(
        self,
        host: AkaneVoiceRuntimeHost,
        voice_turn_id: str,
    ) -> None:
        if not callable(self.generation_starter):
            return
        try:
            result = self.generation_starter(host, voice_turn_id)
        except Exception as exc:
            logger.warning(
                "voice candidate fallback start failed error_type=%s",
                type(exc).__name__,
            )
            return
        if str(getattr(result, "status", "") or "") == "failed":
            logger.warning(
                "voice candidate fallback start failed reason=%s",
                str(getattr(result, "reason", "") or "unknown"),
            )

    def _prompt_cache_key(self) -> str:
        material = "\x00".join(
            (
                "voice_candidate_validator_v1",
                self.profile_user_id,
                self.session_id,
                self.character_pack_id,
            )
        )
        return "voice-candidate-validator-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _revision_text(revision: Mapping[str, Any]) -> str:
    return (f"{str(revision.get('stable_text') or '')}{str(revision.get('unstable_tail') or '')}").strip()


def _safe_reason(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    if (
        not normalized
        or len(normalized) > 96
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.:-" for character in normalized)
    ):
        return fallback
    return normalized
