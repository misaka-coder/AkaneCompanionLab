from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from voicecore import VoiceEvent, snapshot_to_dict

from ..background_tasks import BackgroundTaskRunner
from ..memcore_integration.manager import resolve_memcore_provider_profile
from .host import AkaneVoiceRuntimeHost, VoiceCommandExecutionResult
from .tts_executor import VoiceReadableTextArtifactPort


_SEMANTIC_PULSE_SYSTEM_PROMPT = """
你是实时语音交互控制器。你只判断用户在助手语音播放期间说出的当前稳定片段，
不直接回复用户，不调用工具，不执行外部动作，也不把未完成转写当成最终消息。

你会看到既有对话、助手完整生成文本、实际已送达语音单元、当前正在播放的完整
语义单元，以及用户目前的稳定转写。结合语境判断这是附和、补充、纠正、明确抢话，
还是仍需继续听。不要按“嗯、对、停”等关键词机械分类。

只返回一个 JSON 对象：
{
  "playback_action": "duck | resume | finish_current_unit_then_stop | stop_now",
  "input_action": "keep_listening | commit_when_final | treat_as_interaction | take_over",
  "response_action": "none",
  "reason_summary": "简短说明判断依据",
  "confidence_hint": 0.0
}

当前宿主尚未启用推测式候选回复，因此 response_action 必须为 none。
转写仍明显未完整时，通常保持 duck + keep_listening；确认只是附和且不需要接管时，
使用 resume + treat_as_interaction；明确纠正或开启新请求时可使用 stop_now + take_over；
希望让当前完整语义单元说完再接管时使用 finish_current_unit_then_stop + take_over。
证据不足时不要猜成接管。
""".strip()

_PLAYBACK_ACTIONS = frozenset(
    {
        "duck",
        "resume",
        "finish_current_unit_then_stop",
        "stop_now",
    }
)
_INPUT_ACTIONS = frozenset(
    {
        "keep_listening",
        "commit_when_final",
        "treat_as_interaction",
        "take_over",
    }
)


@dataclass(frozen=True)
class _SemanticPulseJob:
    command_id: str
    idempotency_key: str
    interruption_id: str
    voice_turn_id: str
    response_id: str
    speech_unit_id: str
    turn_revision: int
    response_generation: int
    voice_session_id: str


class AkaneVoiceSemanticPulseCommandExecutor:
    """Resolve a VoiceCore semantic pulse with one read-only model request."""

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
        text_artifacts: VoiceReadableTextArtifactPort,
        background_tasks: BackgroundTaskRunner,
        runtime_metrics: Any = None,
    ) -> None:
        self.engine = engine
        self.memcore_manager = memcore_manager
        self.profile_user_id = str(profile_user_id or "")
        self.session_id = str(session_id or "")
        self.character_pack_id = str(character_pack_id or "")
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self.text_artifacts = text_artifacts
        self.background_tasks = background_tasks
        self.runtime_metrics = runtime_metrics
        self._host: AkaneVoiceRuntimeHost | None = None
        self._jobs: dict[str, _SemanticPulseJob] = {}
        self._guard = threading.RLock()
        self._closed = False
        self._live_commands_enabled = False

    def bind_host(self, host: AkaneVoiceRuntimeHost) -> None:
        if host is None:
            raise ValueError("voice_semantic_host_required")
        with self._guard:
            if self._host is not None and self._host is not host:
                raise RuntimeError("voice_semantic_host_conflict")
            self._host = host

    def enable_live_commands(self) -> None:
        """Accept new semantic pulses after durable startup recovery finishes."""

        with self._guard:
            if self._closed:
                raise RuntimeError("voice_semantic_executor_closed")
            if self._host is None:
                raise RuntimeError("voice_semantic_host_unavailable")
            self._live_commands_enabled = True

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return self._execute_or_recover(command_record, snapshot_record)

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        command_id = str(command_record.get("command_id") or "").strip()
        pending_commands = snapshot_record.get("pending_commands")
        if command_id and isinstance(pending_commands, Mapping) and command_id not in pending_commands:
            # The asynchronous observation already advanced VoiceCore. The
            # durable command receipt only needs releasing after a crash (or
            # after the worker won the race with the next host drive).
            return VoiceCommandExecutionResult.not_started("voice_semantic_observation_already_applied")
        return self._execute_or_recover(command_record, snapshot_record)

    def close(self) -> None:
        with self._guard:
            self._closed = True

    def _execute_or_recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        resolved = self._resolve_job(command_record, snapshot_record)
        if isinstance(resolved, str):
            return VoiceCommandExecutionResult.failed(resolved)
        job = resolved
        with self._guard:
            host = self._host
            if self._closed:
                return VoiceCommandExecutionResult.succeeded(self._failure_event(job, "voice_semantic_executor_closed"))
            if host is None:
                return VoiceCommandExecutionResult.failed("voice_semantic_host_unavailable")
            if not self._live_commands_enabled:
                # A semantic pulse describes one live overlap between a user
                # utterance and an owning playback channel. After restart that
                # channel no longer exists, so replaying the model decision
                # could only create an unactionable resume/stop command.
                return VoiceCommandExecutionResult.succeeded(self._skipped_event(job, "semantic_runtime_restarted"))
            existing = self._jobs.get(job.command_id)
            if existing is not None:
                if existing != job:
                    return VoiceCommandExecutionResult.failed("voice_semantic_job_conflict")
                return VoiceCommandExecutionResult.deferred("voice_semantic_pulse_running")
            current_reason = self._current_reason(host, job)
            if current_reason:
                return VoiceCommandExecutionResult.succeeded(self._skipped_event(job, current_reason))
            self._jobs[job.command_id] = job
        try:
            self.background_tasks.submit(
                lane=f"voice-semantic-{self.conversation_id[-16:]}",
                name=f"voice_semantic_pulse:{job.command_id[-16:]}",
                fn=self._run_job,
                args=(job,),
            )
        except Exception:
            with self._guard:
                self._jobs.pop(job.command_id, None)
            return VoiceCommandExecutionResult.succeeded(self._failure_event(job, "voice_semantic_schedule_failed"))
        return VoiceCommandExecutionResult.deferred("voice_semantic_pulse_running")

    def _run_job(self, job: _SemanticPulseJob) -> None:
        started_at = time.perf_counter()
        ok = False
        try:
            with self._guard:
                host = self._host
                closed = self._closed
            if host is None or closed:
                event = self._failure_event(job, "voice_semantic_executor_closed")
            else:
                current_reason = self._current_reason(host, job)
                if current_reason:
                    event = self._skipped_event(job, current_reason)
                else:
                    event = self._resolve_model_event(host, job)
                    superseded = self._current_reason(host, job)
                    if superseded:
                        event = self._skipped_event(job, superseded)
            if host is not None:
                accepted = host.accept_event(event)
                if accepted.status not in {"accepted", "duplicate"}:
                    fallback = self._skipped_event(job, "semantic_result_stale")
                    accepted = host.accept_event(fallback)
                ok = accepted.status in {"accepted", "duplicate"}
                self._drive_available_commands(host)
        except Exception:
            with self._guard:
                host = self._host
            if host is not None:
                try:
                    accepted = host.accept_event(self._failure_event(job, "voice_semantic_controller_failed"))
                    ok = accepted.status in {"accepted", "duplicate"}
                    self._drive_available_commands(host)
                except Exception:
                    ok = False
        finally:
            self._observe_latency(started_at=started_at, ok=ok)
            with self._guard:
                self._jobs.pop(job.command_id, None)

    def _resolve_model_event(
        self,
        host: AkaneVoiceRuntimeHost,
        job: _SemanticPulseJob,
    ) -> VoiceEvent:
        snapshot_record = snapshot_to_dict(host.snapshot)
        playback_context = self._build_playback_context(snapshot_record, job)
        if not playback_context.get("ok"):
            return self._failure_event(
                job,
                str(playback_context.get("reason") or "voice_semantic_playback_context_unavailable"),
            )
        history = self._build_history_turns()
        if isinstance(history, str):
            return self._failure_event(job, history)
        llm = getattr(self.engine, "llm", None)
        if llm is None:
            return self._failure_event(job, "voice_semantic_model_unavailable")
        request = {
            "system_prompt": _SEMANTIC_PULSE_SYSTEM_PROMPT,
            "user_prompt": self._render_user_prompt(playback_context),
            "fallback": {},
            "temperature": 0.1,
            "prompt_cache_key": self._prompt_cache_key(),
            "history_turns": history,
            "native_tools": None,
            "native_tool_choice": "",
            "prompt_audit_sections": [
                {"name": "system.voice_semantic_pulse", "text": _SEMANTIC_PULSE_SYSTEM_PROMPT},
                {"name": "user.voice_semantic_pulse", "text": self._render_user_prompt(playback_context)},
            ],
        }
        call_result = getattr(llm, "call_chat_json_result", None)
        if callable(call_result):
            result = call_result(**request)
            if bool(getattr(result, "fallback_used", False)) or str(getattr(result, "error", "") or ""):
                return self._failure_event(job, "voice_semantic_model_failed")
            parsed = getattr(result, "parsed", None)
        else:
            call = getattr(llm, "call_chat_json", None)
            if not callable(call):
                return self._failure_event(job, "voice_semantic_model_unavailable")
            parsed = call(**request)
        directive = self._normalize_directive(parsed)
        if isinstance(directive, str):
            return self._failure_event(job, directive)
        return self._directive_event(job, directive)

    def _build_history_turns(self) -> list[dict[str, Any]] | str:
        llm = getattr(self.engine, "llm", None)
        protocol_getter = getattr(llm, "chat_provider_protocol", None)
        try:
            protocol = str(protocol_getter() or "") if callable(protocol_getter) else "openai"
        except Exception:
            protocol = ""
        provider_profile = resolve_memcore_provider_profile(protocol)
        if not provider_profile:
            return "voice_semantic_provider_profile_unavailable"
        try:
            projection = self.memcore_manager.build_context_projection(
                provider_profile=provider_profile,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
            )
        except Exception:
            return "voice_semantic_history_read_failed"
        if not isinstance(projection, Mapping) or not bool(projection.get("ok")):
            return "voice_semantic_history_unavailable"
        return [dict(payload) for payload in list(projection.get("payloads") or []) if isinstance(payload, Mapping)]

    def _build_playback_context(
        self,
        snapshot_record: Mapping[str, Any],
        job: _SemanticPulseJob,
    ) -> dict[str, Any]:
        responses = snapshot_record.get("responses")
        units = snapshot_record.get("speech_units")
        turns = snapshot_record.get("input_turns")
        response = responses.get(job.response_id) if isinstance(responses, Mapping) else None
        unit = units.get(job.speech_unit_id) if isinstance(units, Mapping) else None
        turn = turns.get(job.voice_turn_id) if isinstance(turns, Mapping) else None
        if not isinstance(response, Mapping) or not isinstance(unit, Mapping) or not isinstance(turn, Mapping):
            return {"ok": False, "reason": "voice_semantic_snapshot_context_invalid"}
        revisions = turn.get("revisions")
        revision = None
        if isinstance(revisions, Mapping):
            revision = revisions.get(str(job.turn_revision), revisions.get(job.turn_revision))
        if not isinstance(revision, Mapping):
            return {"ok": False, "reason": "voice_semantic_transcript_unavailable"}
        rendered_units: list[dict[str, Any]] = []
        for unit_id in list(response.get("unit_ids") or []):
            raw_unit = units.get(str(unit_id)) if isinstance(units, Mapping) else None
            if not isinstance(raw_unit, Mapping):
                return {"ok": False, "reason": "voice_semantic_speech_unit_unavailable"}
            state = str(raw_unit.get("state") or "")
            if state not in {"delivered", "playing", "ducked"}:
                continue
            text_result = self.text_artifacts.read_text(str(raw_unit.get("text_artifact_ref") or ""))
            if not bool(getattr(text_result, "ok", False)):
                return {"ok": False, "reason": "voice_semantic_text_artifact_unavailable"}
            rendered_units.append(
                {
                    "ordinal": int(raw_unit.get("ordinal") or 0),
                    "state": state,
                    "text": str(getattr(text_result, "text", "") or ""),
                    "played_ms": int(raw_unit.get("played_ms") or 0),
                    "current": str(unit_id) == job.speech_unit_id,
                }
            )
        if not any(item["current"] for item in rendered_units):
            return {"ok": False, "reason": "voice_semantic_current_unit_unavailable"}
        return {
            "ok": True,
            "assistant": {
                "full_text": str(response.get("full_text") or ""),
                "units": rendered_units,
            },
            "user_input": {
                "quality": str(revision.get("quality") or ""),
                "stable_text": str(revision.get("stable_text") or ""),
                "unstable_tail": str(revision.get("unstable_tail") or ""),
            },
        }

    @staticmethod
    def _render_user_prompt(playback_context: Mapping[str, Any]) -> str:
        return "[host.voice.semantic_pulse]\n以下是宿主提供的当前实时事实，不是助手已经完成的新回复：\n" + json.dumps(
            playback_context, ensure_ascii=False, separators=(",", ":")
        )

    @staticmethod
    def _normalize_directive(value: Any) -> dict[str, Any] | str:
        if not isinstance(value, Mapping):
            return "voice_semantic_directive_invalid"
        nested = value.get("interaction_directive")
        payload = nested if isinstance(nested, Mapping) else value
        playback_action = str(payload.get("playback_action") or "").strip()
        input_action = str(payload.get("input_action") or "").strip()
        response_action = str(payload.get("response_action") or "").strip()
        if playback_action not in _PLAYBACK_ACTIONS:
            return "voice_semantic_playback_action_invalid"
        if input_action not in _INPUT_ACTIONS:
            return "voice_semantic_input_action_invalid"
        if response_action != "none":
            return "voice_semantic_response_action_unsupported"
        reason_summary = " ".join(str(payload.get("reason_summary") or "").split())[:240]
        confidence = payload.get("confidence_hint")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                return "voice_semantic_confidence_invalid"
            confidence = float(confidence)
            if not 0 <= confidence <= 1:
                return "voice_semantic_confidence_invalid"
        return {
            "playback_action": playback_action,
            "input_action": input_action,
            "response_action": response_action,
            "reason_summary": reason_summary,
            "confidence_hint": confidence,
        }

    def _resolve_job(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> _SemanticPulseJob | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_semantic_command_contract_invalid"
        if str(command_record.get("command_kind") or "") != "request_semantic_pulse":
            return "voice_semantic_command_invalid"
        command_id = str(command_record.get("command_id") or "").strip()
        idempotency_key = str(command_record.get("idempotency_key") or "").strip()
        payload = command_record.get("payload")
        if not command_id or not idempotency_key or not isinstance(payload, Mapping):
            return "voice_semantic_command_invalid"
        interruption_id = str(payload.get("interruption_id") or "").strip()
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        response_id = str(payload.get("response_id") or "").strip()
        turn_revision = payload.get("turn_revision")
        attempts = snapshot_record.get("interruption_attempts")
        attempt = attempts.get(interruption_id) if isinstance(attempts, Mapping) else None
        responses = snapshot_record.get("responses")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        turns = snapshot_record.get("input_turns")
        turn = turns.get(voice_turn_id) if isinstance(turns, Mapping) else None
        if (
            not interruption_id
            or not voice_turn_id
            or not response_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
            or not isinstance(attempt, Mapping)
            or not isinstance(response, Mapping)
            or not isinstance(turn, Mapping)
        ):
            return "voice_semantic_command_invalid"
        response_generation = response.get("response_generation")
        speech_unit_id = str(attempt.get("speech_unit_id") or "").strip()
        if (
            str(attempt.get("voice_turn_id") or "") != voice_turn_id
            or str(attempt.get("response_id") or "") != response_id
            or not speech_unit_id
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
        ):
            return "voice_semantic_snapshot_context_invalid"
        return _SemanticPulseJob(
            command_id=command_id,
            idempotency_key=idempotency_key,
            interruption_id=interruption_id,
            voice_turn_id=voice_turn_id,
            response_id=response_id,
            speech_unit_id=speech_unit_id,
            turn_revision=turn_revision,
            response_generation=response_generation,
            voice_session_id=str(turn.get("voice_session_id") or ""),
        )

    @staticmethod
    def _current_reason(host: AkaneVoiceRuntimeHost, job: _SemanticPulseJob) -> str:
        snapshot = host.snapshot
        attempt = snapshot.interruption_attempts.get(job.interruption_id)
        turn = snapshot.input_turns.get(job.voice_turn_id)
        unit = snapshot.speech_units.get(job.speech_unit_id)
        if attempt is None or turn is None or unit is None:
            return "semantic_context_missing"
        if str(getattr(attempt.state, "value", "") or "") != "suspected":
            return "interruption_already_classified"
        if attempt.semantic_pulse_revision != job.turn_revision or turn.latest_revision != job.turn_revision:
            return "semantic_pulse_superseded"
        if str(getattr(unit.state, "value", "") or "") != "ducked":
            return "playback_no_longer_ducked"
        return ""

    def _directive_event(
        self,
        job: _SemanticPulseJob,
        directive: Mapping[str, Any],
    ) -> VoiceEvent:
        canonical = json.dumps(dict(directive), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        directive_digest = hashlib.sha256(f"{job.command_id}:{canonical}".encode("utf-8")).hexdigest()
        payload = {
            "command_id": job.command_id,
            "interruption_id": job.interruption_id,
            "directive_id": f"voice_directive_{directive_digest[:32]}",
            **dict(directive),
        }
        if payload.get("confidence_hint") is None:
            payload.pop("confidence_hint", None)
        return self._event(
            job,
            event_kind="voice.directive.received",
            outcome=f"directive:{directive_digest}",
            payload=payload,
        )

    def _failure_event(self, job: _SemanticPulseJob, reason: str) -> VoiceEvent:
        safe_reason = _safe_reason(reason, fallback="voice_semantic_controller_failed")
        return self._event(
            job,
            event_kind="voice.semantic_pulse.failed",
            outcome=f"failed:{safe_reason}",
            payload={
                "command_id": job.command_id,
                "interruption_id": job.interruption_id,
                "reason_code": safe_reason,
                "retryable": safe_reason
                in {
                    "voice_semantic_model_failed",
                    "voice_semantic_history_unavailable",
                    "voice_semantic_controller_failed",
                },
            },
        )

    def _skipped_event(self, job: _SemanticPulseJob, reason: str) -> VoiceEvent:
        safe_reason = _safe_reason(reason, fallback="semantic_pulse_skipped")
        return self._event(
            job,
            event_kind="voice.semantic_pulse.skipped",
            outcome=f"skipped:{safe_reason}",
            payload={
                "command_id": job.command_id,
                "interruption_id": job.interruption_id,
                "reason_code": safe_reason,
            },
        )

    def _event(
        self,
        job: _SemanticPulseJob,
        *,
        event_kind: str,
        outcome: str,
        payload: Mapping[str, Any],
    ) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        event_digest = hashlib.sha256(f"{job.command_id}:{outcome}".encode("utf-8")).hexdigest()
        return VoiceEvent(
            event_id=f"voice_evt_{event_digest[:32]}",
            event_kind=event_kind,
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=job.voice_session_id,
            conversation_generation=self.conversation_generation,
            producer="akane.voice_semantic_pulse",
            voice_turn_id=job.voice_turn_id,
            response_id=job.response_id,
            speech_unit_id=job.speech_unit_id,
            turn_revision=job.turn_revision,
            response_generation=job.response_generation,
            causation_id=job.command_id,
            payload=dict(payload),
        )

    def _prompt_cache_key(self) -> str:
        material = "\x00".join(
            (
                "voice_semantic_pulse_v1",
                self.profile_user_id,
                self.session_id,
                self.character_pack_id,
            )
        )
        return f"voice-semantic-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

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

    def _observe_latency(self, *, started_at: float, ok: bool) -> None:
        observer = getattr(self.runtime_metrics, "observe_request", None)
        if not callable(observer):
            return
        try:
            observer(
                "voice_semantic_pulse",
                duration_ms=(time.perf_counter() - started_at) * 1000,
                ok=bool(ok),
            )
        except Exception:
            pass


def _safe_reason(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    if (
        not normalized
        or len(normalized) > 96
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.:-" for character in normalized)
    ):
        return fallback
    return normalized
