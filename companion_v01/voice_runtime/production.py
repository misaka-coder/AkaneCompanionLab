from __future__ import annotations

import hashlib
import re
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from capcore_adapter_speech import PCMStreamNormalizer
from voicecore import VoiceEvent, initial_snapshot

from ..background_tasks import BackgroundTaskRunner
from .asr_bridge import VoiceASRSessionBridge
from .asr_provider import build_voice_asr_provider
from .asr_realtime_turn import VoiceASRRealtimeTurnCoordinator
from .durable_ports import FileVoiceTextArtifactPort, SqliteVoiceRuntimeJournal
from .host import (
    AkaneVoiceRuntimeHost,
    VoiceHostPortResult,
)
from .realtime_transport import (
    VoiceRealtimeCoordinatorResolution,
    VoiceRealtimeOpenRequest,
)
from .thinking_agent import (
    AkaneThinkingAgentCommandExecutor,
    VoiceThinkingStartResult,
)


_SAFE_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class _SerializedVoiceRuntimeHost:
    """Serialize one conversation while model work runs off-thread."""

    def __init__(self, host: AkaneVoiceRuntimeHost) -> None:
        self._host = host
        self._guard = threading.RLock()

    @property
    def snapshot(self) -> Any:
        return self._host.snapshot

    def accept_event(self, event: VoiceEvent) -> Any:
        with self._guard:
            return self._host.accept_event(event)

    def drive_once(self) -> Any:
        with self._guard:
            return self._host.drive_once()

    def drain_projection_outbox(self) -> Any:
        with self._guard:
            return self._host.drain_projection_outbox()

    def drain_command_receipts(self) -> Any:
        with self._guard:
            return self._host.drain_command_receipts()


class AkaneVoiceEventFactory:
    """Create production VoiceCore events without importing its test helpers."""

    def __init__(
        self,
        *,
        conversation_id: str,
        voice_session_id: str,
        conversation_generation: int,
        producer: str = "akane.voice_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.conversation_id = str(conversation_id or "")
        self.voice_session_id = str(voice_session_id or "")
        self.conversation_generation = int(conversation_generation)
        self.producer = str(producer or "akane.voice_runtime")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def make(self, event_kind: str, **overrides: Any) -> VoiceEvent:
        now = self.clock().astimezone(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=str(overrides.pop("event_id", f"voice_evt_{uuid.uuid4().hex}")),
            event_kind=str(event_kind or ""),
            occurred_at=str(overrides.pop("occurred_at", now)),
            recorded_at=str(overrides.pop("recorded_at", now)),
            conversation_id=str(overrides.pop("conversation_id", self.conversation_id)),
            voice_session_id=str(overrides.pop("voice_session_id", self.voice_session_id)),
            conversation_generation=int(overrides.pop("conversation_generation", self.conversation_generation)),
            producer=str(overrides.pop("producer", self.producer)),
            **overrides,
        )


class MemcoreVoiceProjectionPort:
    """Thin VoiceCore projection adapter over MemcoreManager's typed writer."""

    def __init__(
        self,
        *,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> None:
        self.manager = manager
        self.profile_user_id = str(profile_user_id or "")
        self.session_id = str(session_id or "")
        self.character_pack_id = str(character_pack_id or "")

    def emit(self, projection_record: Mapping[str, Any]) -> VoiceHostPortResult:
        if not isinstance(projection_record, Mapping):
            return VoiceHostPortResult.failed("voice_projection_record_invalid")
        normalized_record = dict(projection_record)
        target = str(normalized_record.get("target") or "").strip().lower()
        kind = str(normalized_record.get("kind") or "").strip().lower()
        if target in {"host", "runtime"} and kind.startswith("voice."):
            payload = normalized_record.get("payload")
            normalized_record = {
                **normalized_record,
                "target": "memcore",
                "kind": f"event.{kind}",
                "payload": {
                    **(dict(payload) if isinstance(payload, Mapping) else {}),
                    "projection_target": target,
                    "projection_kind": kind,
                },
            }
        try:
            result = self.manager.record_voice_projection(
                normalized_record,
                profile_user_id=self.profile_user_id,
                session_id=self.session_id,
                character_pack_id=self.character_pack_id,
                actor_stable_id=self.profile_user_id,
            )
        except Exception:
            return VoiceHostPortResult.failed(
                "voice_memcore_projection_failed",
                retryable=True,
            )
        if not isinstance(result, Mapping):
            return VoiceHostPortResult.failed("voice_memcore_projection_result_invalid")
        if bool(result.get("ok")):
            return VoiceHostPortResult.succeeded()
        status = _safe_reason(result.get("status"), fallback="failed")
        reason = _safe_reason(
            result.get("reason"),
            fallback=f"voice_memcore_projection_{status}",
        )
        return VoiceHostPortResult.failed(
            reason,
            retryable=status in {"failed", "unavailable"},
        )


class AkaneVoiceRuntimeService:
    """Own durable VoiceCore hosts for one BotRuntime instance."""

    def __init__(
        self,
        *,
        engine: Any,
        settings: Any,
        state_dir: Path,
        instance_id: str,
        bot_id: str,
        default_character_pack_id: str = "",
        provider_builder: Callable[[Any], Any] = build_voice_asr_provider,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.state_dir = Path(state_dir)
        self.instance_id = str(instance_id or "")
        self.bot_id = str(bot_id or "")
        self.default_character_pack_id = str(default_character_pack_id or "")
        self.provider_builder = provider_builder
        self._hosts: dict[str, _SerializedVoiceRuntimeHost] = {}
        self._thinking_executors: dict[
            str,
            AkaneThinkingAgentCommandExecutor,
        ] = {}
        self._background_tasks = BackgroundTaskRunner(default_workers=1)
        self._guard = threading.RLock()
        self._closed = False

    def create_coordinator(
        self,
        request: VoiceRealtimeOpenRequest,
    ) -> VoiceRealtimeCoordinatorResolution:
        if not isinstance(request, VoiceRealtimeOpenRequest):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_open_request_invalid",
                status="invalid_request",
            )
        with self._guard:
            if self._closed:
                return VoiceRealtimeCoordinatorResolution.failed(
                    "voice_runtime_service_closed",
                    status="unavailable",
                    retryable=True,
                )

        manager = self._memcore_manager()
        if manager is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_memcore_unavailable",
                status="unavailable",
                retryable=True,
                safe_public_summary="记忆时间线暂时不可用，实时语音没有开始。",
            )
        try:
            provider = self.provider_builder(self.settings)
        except Exception:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_asr_provider_build_failed",
                status="unavailable",
                retryable=True,
            )
        if not bool(getattr(provider, "ready", False)) or getattr(provider, "adapter", None) is None:
            reason = _safe_reason(
                getattr(provider, "reason", ""),
                fallback="voice_asr_provider_unavailable",
            )
            return VoiceRealtimeCoordinatorResolution.failed(
                reason,
                status=_safe_reason(getattr(provider, "status", ""), fallback="unavailable"),
                retryable=str(getattr(provider, "status", "")) == "unavailable",
                safe_public_summary=_provider_public_summary(reason),
            )

        character_pack_id = request.character_pack_id or self.default_character_pack_id
        canonical_conversation_id = self._canonical_conversation_id(
            request=request,
            character_pack_id=character_pack_id,
        )
        try:
            host_result = self._resolve_host(
                canonical_conversation_id=canonical_conversation_id,
                manager=manager,
                profile_user_id=request.profile_user_id,
                session_id=request.session_id,
                character_pack_id=character_pack_id,
            )
        except Exception:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_runtime_host_initialization_failed",
                status="unavailable",
                retryable=True,
                safe_public_summary="实时语音状态暂时无法初始化，本轮没有开始。",
            )
        if isinstance(host_result, VoiceRealtimeCoordinatorResolution):
            return host_result
        voice_session_id = f"voice_session_{uuid.uuid4().hex}"
        event_factory = AkaneVoiceEventFactory(
            conversation_id=canonical_conversation_id,
            voice_session_id=voice_session_id,
            conversation_generation=1,
        )
        with self._guard:
            thinking_executor = self._thinking_executors.get(canonical_conversation_id)
        if thinking_executor is None:
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_thinking_executor_unavailable",
                status="unavailable",
                retryable=True,
                safe_public_summary="实时语音回复服务暂时不可用，本轮没有开始。",
            )
        try:
            thinking_executor.register_turn(
                voice_turn_id=request.voice_turn_id,
                event_factory=event_factory,
            )
            normalizer = PCMStreamNormalizer(
                input_format=request.input_format,
                input_sample_rate=request.sample_rate,
                input_channels=request.channels,
            )
            bridge = VoiceASRSessionBridge(
                host=host_result,
                event_factory=event_factory,
                voice_turn_id=request.voice_turn_id,
                audio_stream_id=request.audio_stream_id,
                disposition=request.disposition,
            )
            coordinator = VoiceASRRealtimeTurnCoordinator(
                adapter=provider.adapter,
                bridge=bridge,
                language=request.language,
                pcm_normalizer=normalizer,
                response_starter=(
                    lambda: self._start_response_generation(
                        host=host_result,
                        executor=thinking_executor,
                        voice_turn_id=request.voice_turn_id,
                    )
                ),
            )
        except (RuntimeError, TypeError, ValueError):
            return VoiceRealtimeCoordinatorResolution.failed(
                "voice_realtime_coordinator_invalid",
                status="invalid_request",
            )
        return VoiceRealtimeCoordinatorResolution.succeeded(
            coordinator,
            provider_id=str(getattr(provider, "provider_id", "") or ""),
            voice_session_id=voice_session_id,
        )

    def close(self) -> dict[str, Any]:
        with self._guard:
            if self._closed:
                return {"status": "stopped", "reason": "already_stopped"}
            self._closed = True
            host_count = len(self._hosts)
            executors = list(self._thinking_executors.values())
            self._hosts.clear()
            self._thinking_executors.clear()
        for executor in executors:
            executor.close()
        background_stopped = self._background_tasks.close(timeout=10.0)
        return {
            "status": "stopped" if background_stopped else "degraded",
            "reason": "" if background_stopped else "voice_thinking_shutdown_incomplete",
            "host_count": host_count,
        }

    def wait_idle(self, *, timeout: float = 10.0) -> bool:
        return self._background_tasks.wait_idle(timeout=timeout)

    def _resolve_host(
        self,
        *,
        canonical_conversation_id: str,
        manager: Any,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> _SerializedVoiceRuntimeHost | VoiceRealtimeCoordinatorResolution:
        with self._guard:
            existing = self._hosts.get(canonical_conversation_id)
            if existing is not None:
                return existing
            journal = SqliteVoiceRuntimeJournal(
                state_dir=self.state_dir,
                conversation_id=canonical_conversation_id,
                conversation_generation=1,
            )
            base_snapshot = initial_snapshot(
                canonical_conversation_id,
                conversation_generation=1,
            )
            replayed = journal.replay(base_snapshot)
            if not replayed.ok or replayed.replay is None:
                return VoiceRealtimeCoordinatorResolution.failed(
                    replayed.reason or "voice_journal_replay_failed",
                    status="unavailable",
                    retryable=False,
                    safe_public_summary="实时语音状态无法恢复，本轮没有开始。",
                )
            projection_port = MemcoreVoiceProjectionPort(
                manager=manager,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            text_artifacts = FileVoiceTextArtifactPort(
                state_dir=self.state_dir,
                conversation_id=canonical_conversation_id,
                conversation_generation=1,
            )
            thinking_executor = AkaneThinkingAgentCommandExecutor(
                engine=self.engine,
                memcore_manager=manager,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                conversation_id=canonical_conversation_id,
                conversation_generation=1,
                text_artifacts=text_artifacts,
                background_tasks=self._background_tasks,
            )
            raw_host = AkaneVoiceRuntimeHost(
                conversation_id=canonical_conversation_id,
                conversation_generation=1,
                journal=journal,
                projection_port=projection_port,
                command_executor=thinking_executor,
                restored_snapshot=replayed.replay.snapshot,
            )
            host = _SerializedVoiceRuntimeHost(raw_host)
            pending = host.drain_projection_outbox()
            if not pending.quiescent:
                return VoiceRealtimeCoordinatorResolution.failed(
                    pending.reason or "voice_projection_recovery_failed",
                    status="unavailable",
                    retryable=pending.status == "deferred",
                    safe_public_summary="实时语音记忆投影尚未恢复，本轮没有开始。",
                )
            receipts = host.drain_command_receipts()
            if not receipts.quiescent:
                return VoiceRealtimeCoordinatorResolution.failed(
                    receipts.reason or "voice_command_recovery_failed",
                    status="unavailable",
                    retryable=receipts.status == "deferred",
                    safe_public_summary="实时语音回复状态尚未恢复，本轮没有开始。",
                )
            recovered_commands = self._recover_pending_response_commands(host)
            if not recovered_commands.ok:
                return VoiceRealtimeCoordinatorResolution.failed(
                    recovered_commands.reason or "voice_response_command_recovery_failed",
                    status="unavailable",
                    retryable=recovered_commands.retryable,
                    safe_public_summary=(
                        recovered_commands.safe_public_summary or "实时语音回复命令无法恢复，本轮没有开始。"
                    ),
                )
            restored = thinking_executor.restore_generating_jobs(self._snapshot_record(host))
            if restored.status == "failed":
                return VoiceRealtimeCoordinatorResolution.failed(
                    restored.reason or "voice_thinking_recovery_failed",
                    status="unavailable",
                    retryable=restored.retryable,
                    safe_public_summary="实时语音回复状态无法恢复，本轮没有开始。",
                )
            self._hosts[canonical_conversation_id] = host
            self._thinking_executors[canonical_conversation_id] = thinking_executor
            if restored.status == "started":
                started = thinking_executor.start_ready_generations(host)
                if not started.ok:
                    return VoiceRealtimeCoordinatorResolution.failed(
                        started.reason or "voice_thinking_recovery_start_failed",
                        status="unavailable",
                        retryable=started.retryable,
                        safe_public_summary=(
                            started.safe_public_summary or "实时语音回复恢复任务无法启动，本轮没有开始。"
                        ),
                    )
            return host

    @staticmethod
    def _snapshot_record(host: _SerializedVoiceRuntimeHost) -> dict[str, Any]:
        from voicecore import snapshot_to_dict

        return snapshot_to_dict(host.snapshot)

    @staticmethod
    def _recover_pending_response_commands(
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
                str(getattr(command, "command_kind", "") or "") != "start_response_generation" for command in pending
            ):
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_command_recovery_unsupported",
                    retryable=False,
                )
            driven = host.drive_once()
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_response_command_recovery_failed",
                    retryable=driven.status == "deferred",
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
            response = next(
                (
                    item
                    for item in host.snapshot.responses.values()
                    if str(getattr(item, "voice_turn_id", "") or "") == str(voice_turn_id or "")
                ),
                None,
            )
            response_state = str(getattr(getattr(response, "state", None), "value", "") or "")
            response_id = str(getattr(response, "response_id", "") or "")
            if response_state == "generating":
                return executor.start_ready_generations(
                    host,
                    voice_turn_id=voice_turn_id,
                )
            if response_state in {"failed", "cancelled", "discarded", "completed"}:
                return VoiceThinkingStartResult(
                    status="failed",
                    reason="voice_response_start_terminal",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复没有进入生成状态。",
                )
            pending_ids = tuple(host.snapshot.pending_commands)
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
            if driven.status != "succeeded":
                return VoiceThinkingStartResult(
                    status="failed",
                    reason=driven.reason or "voice_response_start_failed",
                    retryable=driven.status == "deferred",
                    response_id=response_id,
                    safe_public_summary="语音已经识别，但回复生成暂时无法启动。",
                )

    def _memcore_manager(self) -> Any | None:
        resolver = getattr(self.engine, "_memcore_manager_if_enabled", None)
        try:
            manager = resolver() if callable(resolver) else getattr(self.engine, "memcore_manager", None)
        except Exception:
            return None
        if (
            manager is None
            or not bool(getattr(manager, "enabled", False))
            or not bool(getattr(manager, "available", False))
            or not callable(getattr(manager, "record_voice_projection", None))
            or not callable(getattr(manager, "resolve_voice_projection_turn", None))
        ):
            return None
        return manager

    def _canonical_conversation_id(
        self,
        *,
        request: VoiceRealtimeOpenRequest,
        character_pack_id: str,
    ) -> str:
        material = "\0".join(
            (
                self.instance_id,
                self.bot_id,
                request.profile_user_id,
                request.session_id,
                character_pack_id,
                request.conversation_id,
            )
        )
        return "voice_conversation_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_reason(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if _SAFE_REASON.fullmatch(text) else str(fallback or "voice_runtime_failed")


def _provider_public_summary(reason: str) -> str:
    if reason == "fun_asr_realtime_disabled":
        return "实时语音识别尚未开启。"
    if reason in {"dashscope_api_key_missing", "dashscope_api_host_missing"}:
        return "实时语音识别配置不完整。"
    if reason == "fun_asr_realtime_config_invalid":
        return "实时语音识别配置无效。"
    return "实时语音识别服务暂时不可用。"
