from __future__ import annotations

import hashlib
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from voicecore import VoiceEvent

from ..background_tasks import BackgroundTaskRunner
from .host import (
    AkaneVoiceRuntimeHost,
    VoiceCommandExecutionResult,
)
from .stream_bridge import (
    VoiceResponseStreamBridge,
    VoiceStreamEventFactory,
    VoiceTextArtifactPort,
)


@dataclass(frozen=True)
class VoiceThinkingStartResult:
    status: str
    reason: str = ""
    retryable: bool = False
    safe_public_summary: str = ""
    response_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"started", "duplicate", "completed"}


@dataclass(frozen=True)
class _GenerationJob:
    response_id: str
    voice_turn_id: str
    response_generation: int
    turn_revision: int
    source_id: str
    memcore_turn_id: str
    message: str
    timestamp: int
    event_factory: VoiceStreamEventFactory
    speech_delivery_enabled: bool = False
    delivery_notifier: Callable[[], None] | None = None


class AkaneThinkingAgentCommandExecutor:
    """Start Akane's existing Thinking Agent after VoiceCore commits input.

    Command observations remain synchronous and durable. The potentially slow
    model stream starts only after the Host has persisted and applied the
    ``generation_started`` observation.
    """

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
        text_artifacts: VoiceTextArtifactPort,
        background_tasks: BackgroundTaskRunner,
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
        self._event_factories: dict[str, VoiceStreamEventFactory] = {}
        self._speech_delivery_by_turn: dict[str, bool] = {}
        self._delivery_notifiers: dict[str, Callable[[], None]] = {}
        self._jobs: dict[str, _GenerationJob] = {}
        self._running_response_ids: set[str] = set()
        self._guard = threading.RLock()
        self._closed = False

    def register_turn(
        self,
        *,
        voice_turn_id: str,
        event_factory: VoiceStreamEventFactory,
        speech_delivery_enabled: bool = False,
        delivery_notifier: Callable[[], None] | None = None,
    ) -> None:
        normalized_turn_id = str(voice_turn_id or "").strip()
        if not normalized_turn_id or event_factory is None:
            raise ValueError("voice_thinking_turn_context_invalid")
        with self._guard:
            if self._closed:
                raise RuntimeError("voice_thinking_executor_closed")
            self._event_factories[normalized_turn_id] = event_factory
            self._speech_delivery_by_turn[normalized_turn_id] = bool(speech_delivery_enabled)
            if delivery_notifier is not None:
                self._delivery_notifiers[normalized_turn_id] = delivery_notifier

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return self._execute_or_recover(
            command_record=command_record,
            snapshot_record=snapshot_record,
        )

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        return self._execute_or_recover(
            command_record=command_record,
            snapshot_record=snapshot_record,
        )

    def start_ready_generations(
        self,
        host: AkaneVoiceRuntimeHost,
        *,
        voice_turn_id: str = "",
    ) -> VoiceThinkingStartResult:
        target_turn_id = str(voice_turn_id or "").strip()
        with self._guard:
            if self._closed:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_executor_closed",
                    retryable=True,
                    safe_public_summary="回复生成服务正在关闭。",
                )
            ready = [
                job
                for job in self._jobs.values()
                if (not target_turn_id or job.voice_turn_id == target_turn_id)
                and job.response_id not in self._running_response_ids
            ]
        if not ready:
            existing = self._response_for_turn(host, target_turn_id)
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
        for job in ready:
            response = host.snapshot.responses.get(job.response_id)
            if response is None or str(getattr(response.state, "value", "")) != "generating":
                continue
            with self._guard:
                if job.response_id in self._running_response_ids:
                    continue
                self._running_response_ids.add(job.response_id)
            try:
                self.background_tasks.submit(
                    lane=f"voice-thinking-{self.conversation_id[-16:]}",
                    name=f"voice_response:{job.response_id[-16:]}",
                    fn=self._run_generation,
                    args=(host, job),
                )
            except Exception:
                with self._guard:
                    self._running_response_ids.discard(job.response_id)
                self._fail_response(
                    host,
                    job,
                    reason="voice_thinking_schedule_failed",
                    retryable=True,
                )
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_thinking_schedule_failed",
                    retryable=True,
                    safe_public_summary="回复生成任务暂时无法启动。",
                    response_id=job.response_id,
                )
            started.append(job.response_id)
        if not started:
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

    def restore_generating_jobs(
        self,
        snapshot_record: Mapping[str, Any],
    ) -> VoiceThinkingStartResult:
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
                self._jobs.setdefault(
                    response_id,
                    _GenerationJob(
                        response_id=response_id,
                        voice_turn_id=voice_turn_id,
                        response_generation=response_generation,
                        turn_revision=turn_revision,
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

    def close(self) -> None:
        with self._guard:
            self._closed = True
            self._event_factories.clear()
            self._speech_delivery_by_turn.clear()
            self._delivery_notifiers.clear()

    def _execute_or_recover(
        self,
        *,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return VoiceCommandExecutionResult.failed("voice_command_contract_invalid")
        command = dict(command_record)
        payload = command.get("payload")
        if not isinstance(payload, Mapping):
            return VoiceCommandExecutionResult.failed("voice_command_payload_invalid")
        command_kind = str(command.get("command_kind") or "")
        if command_kind != "start_response_generation":
            return VoiceCommandExecutionResult.deferred(f"voice_command_not_connected:{command_kind or 'unknown'}")

        command_id = str(command.get("command_id") or "")
        voice_turn_id = str(payload.get("voice_turn_id") or "").strip()
        turn_revision = payload.get("turn_revision")
        if (
            not command_id
            or not voice_turn_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
            or turn_revision < 1
        ):
            return VoiceCommandExecutionResult.failed("voice_generation_command_invalid")
        event_factory = self._event_factory_for_turn(voice_turn_id)
        response_id = str(payload.get("response_id") or "").strip()
        if not response_id:
            response_id = self._response_id(command)
            return VoiceCommandExecutionResult.succeeded(
                event_factory.make(
                    "voice.response.created",
                    voice_turn_id=voice_turn_id,
                    response_id=response_id,
                    turn_revision=turn_revision,
                    response_generation=1,
                    payload={
                        "purpose": str(payload.get("purpose") or "content"),
                        "commitment": str(payload.get("commitment") or "committed"),
                        "command_id": command_id,
                    },
                )
            )

        response_generation = payload.get("response_generation")
        if isinstance(response_generation, bool) or not isinstance(response_generation, int) or response_generation < 1:
            return VoiceCommandExecutionResult.failed("voice_generation_command_invalid")
        resolved = self._resolve_turn_context(
            snapshot_record=snapshot_record,
            voice_turn_id=voice_turn_id,
        )
        if not resolved.get("ok"):
            return VoiceCommandExecutionResult.succeeded(
                event_factory.make(
                    "voice.response.failed",
                    voice_turn_id=voice_turn_id,
                    response_id=response_id,
                    turn_revision=turn_revision,
                    response_generation=response_generation,
                    payload={
                        "stage": "thinking_agent",
                        "reason_code": str(resolved.get("reason") or "voice_thinking_turn_context_unavailable"),
                        "retryable": str(resolved.get("status") or "")
                        in {
                            "failed",
                            "unavailable",
                        },
                        "safe_public_summary": "语音内容已经收到，但回复上下文暂时无法恢复。",
                        "affected_ids": [voice_turn_id, response_id],
                        "command_id": command_id,
                    },
                )
            )
        job = _GenerationJob(
            response_id=response_id,
            voice_turn_id=voice_turn_id,
            response_generation=response_generation,
            turn_revision=turn_revision,
            source_id=str(resolved["source_id"]),
            memcore_turn_id=str(resolved["turn_id"]),
            message=str(resolved["text"]),
            timestamp=int(resolved.get("timestamp") or 0),
            event_factory=event_factory,
            speech_delivery_enabled=self._speech_delivery_enabled(voice_turn_id),
            delivery_notifier=self._delivery_notifier(voice_turn_id),
        )
        with self._guard:
            existing = self._jobs.get(response_id)
            if existing is not None and existing != job:
                return VoiceCommandExecutionResult.failed("voice_generation_job_conflict")
            self._jobs[response_id] = job
        return VoiceCommandExecutionResult.succeeded(
            event_factory.make(
                "voice.response.generation_started",
                voice_turn_id=voice_turn_id,
                response_id=response_id,
                turn_revision=turn_revision,
                response_generation=response_generation,
                payload={"command_id": command_id},
            )
        )

    def _resolve_turn_context(
        self,
        *,
        snapshot_record: Mapping[str, Any],
        voice_turn_id: str,
    ) -> dict[str, Any]:
        input_turns = snapshot_record.get("input_turns")
        turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        projection_id = str(turn.get("committed_projection_id") or "").strip() if isinstance(turn, Mapping) else ""
        resolver = getattr(
            self.memcore_manager,
            "resolve_voice_projection_turn",
            None,
        )
        if not projection_id or not callable(resolver):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "voice_thinking_turn_context_unavailable",
            }
        try:
            result = resolver(
                projection_id=projection_id,
                voice_turn_id=voice_turn_id,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
            )
        except Exception:
            return {
                "ok": False,
                "status": "failed",
                "reason": "voice_thinking_turn_context_read_failed",
            }
        return (
            dict(result)
            if isinstance(result, Mapping)
            else {
                "ok": False,
                "status": "failed",
                "reason": "voice_thinking_turn_context_result_invalid",
            }
        )

    def _run_generation(
        self,
        host: AkaneVoiceRuntimeHost,
        job: _GenerationJob,
    ) -> None:
        final_seen = False
        failure_reason = ""
        iterator: Any | None = None
        try:
            iterator = self.engine.process_voice_turn_stream(
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
                source_id=job.source_id,
                memcore_turn_id=job.memcore_turn_id,
                voice_turn_id=job.voice_turn_id,
                message=job.message,
                timestamp=job.timestamp,
            )
            bridge = VoiceResponseStreamBridge(
                host=host,
                response_id=job.response_id,
                text_artifacts=self.text_artifacts,
                event_factory=job.event_factory,
            )
            for stream_event in iterator:
                if not isinstance(stream_event, Mapping):
                    continue
                event_type = str(stream_event.get("type") or "")
                if event_type == "final":
                    final_payload = stream_event.get("payload")
                    if isinstance(final_payload, Mapping) and bool(final_payload.get("_transient_final_failure")):
                        failure_reason = "voice_thinking_final_unusable"
                        break
                    result = bridge.accept_stream_event(stream_event)
                    if not result.accepted and result.status != "duplicate":
                        failure_reason = result.reason or "voice_thinking_final_dispatch_failed"
                        break
                    final_seen = True
                    self._notify_delivery(job)
                    break
                if event_type == "speech_segment" and job.speech_delivery_enabled:
                    result = bridge.accept_stream_event(stream_event)
                    if not result.accepted and result.status != "duplicate":
                        failure_reason = result.reason or "voice_speech_segment_dispatch_failed"
                        break
                    delivery_drive = host.drive_once()
                    if delivery_drive.status == "failed":
                        failure_reason = delivery_drive.reason or "voice_playback_offer_failed"
                        break
                    self._notify_delivery(job)
            if not final_seen and not failure_reason:
                failure_reason = "voice_thinking_final_missing"
        except Exception:
            failure_reason = "voice_thinking_generation_failed"
        finally:
            close_iterator = getattr(iterator, "close", None)
            if callable(close_iterator):
                try:
                    close_iterator()
                except Exception:
                    if not final_seen and not failure_reason:
                        failure_reason = "voice_thinking_stream_close_failed"

        if failure_reason:
            self._fail_response(
                host,
                job,
                reason=failure_reason,
                retryable=True,
            )
            self._notify_delivery(job)
        with self._guard:
            self._running_response_ids.discard(job.response_id)
            self._jobs.pop(job.response_id, None)
            self._event_factories.pop(job.voice_turn_id, None)
            self._speech_delivery_by_turn.pop(job.voice_turn_id, None)
            self._delivery_notifiers.pop(job.voice_turn_id, None)

    def _fail_response(
        self,
        host: AkaneVoiceRuntimeHost,
        job: _GenerationJob,
        *,
        reason: str,
        retryable: bool,
    ) -> None:
        response = host.snapshot.responses.get(job.response_id)
        if response is None or str(getattr(response.state, "value", "")) in {
            "completed",
            "cancelled",
            "failed",
            "discarded",
        }:
            return
        try:
            event = job.event_factory.make(
                "voice.response.failed",
                voice_turn_id=job.voice_turn_id,
                response_id=job.response_id,
                turn_revision=job.turn_revision,
                response_generation=job.response_generation,
                payload={
                    "stage": "thinking_agent",
                    "reason_code": str(reason or "voice_thinking_generation_failed"),
                    "retryable": bool(retryable),
                    "safe_public_summary": "语音内容已经收到，但这次回复没有生成完成。",
                    "affected_ids": [job.voice_turn_id, job.response_id],
                },
            )
        except Exception:
            return
        host.accept_event(event)

    def _event_factory_for_turn(
        self,
        voice_turn_id: str,
    ) -> VoiceStreamEventFactory:
        with self._guard:
            factory = self._event_factories.get(voice_turn_id)
        return factory or _RecoveryVoiceEventFactory(
            conversation_id=self.conversation_id,
            conversation_generation=self.conversation_generation,
            voice_turn_id=voice_turn_id,
        )

    def _speech_delivery_enabled(self, voice_turn_id: str) -> bool:
        with self._guard:
            return bool(self._speech_delivery_by_turn.get(voice_turn_id, False))

    def _delivery_notifier(
        self,
        voice_turn_id: str,
    ) -> Callable[[], None] | None:
        with self._guard:
            return self._delivery_notifiers.get(voice_turn_id)

    @staticmethod
    def _notify_delivery(job: _GenerationJob) -> None:
        if job.delivery_notifier is None:
            return
        try:
            job.delivery_notifier()
        except Exception:
            pass

    @staticmethod
    def _response_id(command_record: Mapping[str, Any]) -> str:
        material = str(command_record.get("idempotency_key") or command_record.get("command_id") or "")
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"voice_response_{digest[:32]}"

    @staticmethod
    def _response_for_turn(
        host: AkaneVoiceRuntimeHost,
        voice_turn_id: str,
    ) -> Any | None:
        if not voice_turn_id:
            return None
        for response in host.snapshot.responses.values():
            if str(getattr(response, "voice_turn_id", "") or "") == voice_turn_id:
                return response
        return None


class _RecoveryVoiceEventFactory:
    def __init__(
        self,
        *,
        conversation_id: str,
        conversation_generation: int,
        voice_turn_id: str,
    ) -> None:
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        digest = hashlib.sha256(f"{self.conversation_id}:{voice_turn_id}".encode("utf-8")).hexdigest()
        self.voice_session_id = f"voice_recovery_{digest[:32]}"

    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=str(overrides.pop("event_id", f"voice_evt_{uuid.uuid4().hex}")),
            event_kind=str(event_kind or ""),
            occurred_at=str(overrides.pop("occurred_at", now)),
            recorded_at=str(overrides.pop("recorded_at", now)),
            conversation_id=str(overrides.pop("conversation_id", self.conversation_id)),
            voice_session_id=str(overrides.pop("voice_session_id", self.voice_session_id)),
            conversation_generation=int(
                overrides.pop(
                    "conversation_generation",
                    self.conversation_generation,
                )
            ),
            producer=str(overrides.pop("producer", "akane.voice_thinking_recovery")),
            **overrides,
        )
