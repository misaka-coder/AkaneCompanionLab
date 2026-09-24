from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from voicecore import VoiceEvent

from ..background_tasks import BackgroundTaskRunner
from ..tts_service import TTSServiceError
from .durable_ports import (
    VoiceAudioArtifactReadResult,
    VoiceAudioArtifactResult,
    VoiceTextArtifactReadResult,
)
from .host import VoiceCommandExecutionResult, VoiceCommandExecutor


logger = logging.getLogger("akane.voice_tts")


@dataclass
class _TTSJob:
    command: dict[str, Any]
    snapshot: dict[str, Any]
    result: VoiceCommandExecutionResult | None = None


class VoiceReadableTextArtifactPort(Protocol):
    def read_text(self, artifact_ref: str) -> VoiceTextArtifactReadResult: ...


class VoiceAudioArtifactPort(Protocol):
    def put_audio(
        self,
        *,
        artifact_key: str,
        audio: bytes,
        media_type: str,
    ) -> VoiceAudioArtifactResult: ...

    def read_audio(self, artifact_ref: str) -> VoiceAudioArtifactReadResult: ...


class VoiceCommandRouterExecutor:
    """Route provider-neutral VoiceCore commands to one effect authority."""

    def __init__(self, routes: Mapping[str, VoiceCommandExecutor]) -> None:
        normalized: dict[str, VoiceCommandExecutor] = {}
        for command_kind, executor in routes.items():
            kind = str(command_kind or "").strip()
            if not kind or executor is None or kind in normalized:
                raise ValueError("voice_command_route_invalid")
            normalized[kind] = executor
        if not normalized:
            raise ValueError("voice_command_routes_required")
        self._routes = normalized

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        executor = self._resolve(command_record)
        if executor is None:
            return VoiceCommandExecutionResult.deferred(self._unsupported_reason(command_record))
        return executor.execute(command_record, snapshot_record)

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        executor = self._resolve(command_record)
        if executor is None:
            return VoiceCommandExecutionResult.deferred(self._unsupported_reason(command_record))
        recover = getattr(executor, "recover", None)
        if not callable(recover):
            return VoiceCommandExecutionResult(
                status="deferred",
                reason="voice_command_recovery_unavailable",
                retryable=True,
                outcome_known=False,
            )
        return recover(command_record, snapshot_record)

    def _resolve(
        self,
        command_record: Mapping[str, Any],
    ) -> VoiceCommandExecutor | None:
        if not isinstance(command_record, Mapping):
            return None
        return self._routes.get(str(command_record.get("command_kind") or ""))

    @staticmethod
    def _unsupported_reason(command_record: Mapping[str, Any]) -> str:
        kind = (
            str(command_record.get("command_kind") or "unknown") if isinstance(command_record, Mapping) else "unknown"
        )
        return f"voice_command_not_connected:{kind}"


class AkaneVoiceTTSCommandExecutor:
    """Execute VoiceCore ``start_tts`` against a real TTS client.

    Synthesized bytes become one immutable, path-free artifact before
    ``voice.tts.ready`` is emitted. This executor never emits playback facts;
    a client acknowledgement remains the only delivery authority.
    """

    def __init__(
        self,
        *,
        tts_client: Any,
        text_artifacts: VoiceReadableTextArtifactPort,
        audio_artifacts: VoiceAudioArtifactPort,
        conversation_id: str,
        conversation_generation: int,
        background_tasks: BackgroundTaskRunner | None = None,
    ) -> None:
        self.tts_client = tts_client
        self.text_artifacts = text_artifacts
        self.audio_artifacts = audio_artifacts
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self.provider_id = str(getattr(tts_client, "provider_id", "") or "").strip()
        self.background_tasks = background_tasks
        self._host: Any = None
        self._notify_delivery: Callable[[str], None] | None = None
        self._guard = threading.RLock()
        self._jobs: dict[str, _TTSJob] = {}
        self._closed = False
        self._live_commands = False

    def bind_host(self, host: Any, *, notify_delivery: Callable[[str], None]) -> None:
        with self._guard:
            self._host = host
            self._notify_delivery = notify_delivery

    def close(self) -> None:
        with self._guard:
            self._closed = True

    def enable_live_commands(self) -> None:
        with self._guard:
            self._live_commands = True

    def execute(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        if self.background_tasks is not None:
            return self._schedule(command_record, snapshot_record)
        return self._synthesize(command_record, snapshot_record)

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
            self._prune_settled(snapshot_record.get("pending_commands", {}))
            existing = self._jobs.get(command_id)
            if existing is not None:
                if existing.command != command:
                    return VoiceCommandExecutionResult.failed("voice_tts_job_conflict")
                return existing.result or VoiceCommandExecutionResult.running("voice_tts_running")
            if self._closed or self._host is None:
                return self._failed_observation(
                    command=command,
                    common=common,
                    reason="voice_tts_executor_closed",
                    started=False,
                )
            if not self._live_commands:
                # There is no owning playback client during restart recovery.
                # Do not spend GPU/provider work on an orphaned unstarted unit.
                return self._failed_observation(
                    command=command,
                    common=common,
                    reason="voice_tts_runtime_restarted",
                    started=False,
                )
            # At most two scheduled/running synthesis jobs per conversation.
            # All conversations share one synthesis lane, including local GPU TTS.
            # Further work stays in VoiceCore's existing pending command queue.
            if len(self._jobs) >= 2:
                return VoiceCommandExecutionResult.deferred("voice_tts_capacity_wait")
            # Keep only this unit's lineage, not a copy of the entire conversation.
            job = _TTSJob(
                command=command,
                snapshot={
                    "responses": {
                        str(common["response_id"]): {
                            "voice_turn_id": common["voice_turn_id"],
                            "source_turn_revision": common["turn_revision"],
                            "response_generation": common["response_generation"],
                        },
                    },
                    "input_turns": {
                        str(common["voice_turn_id"]): {"voice_session_id": common["voice_session_id"]},
                    },
                },
            )
            self._jobs[command_id] = job
        try:
            self.background_tasks.submit(
                lane="voice-tts",
                name="voice_tts_synthesis",
                fn=self._run_job,
                args=(job,),
            )
        except Exception:
            with self._guard:
                self._jobs.pop(command_id, None)
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_schedule_failed",
                started=False,
            )
        return VoiceCommandExecutionResult.running("voice_tts_running")

    def _prune_settled(self, pending: Mapping[str, Any]) -> None:
        # Keep running jobs counted even after cancellation so rapid new turns
        # cannot grow an unbounded queue of obsolete provider calls.
        for command_id, job in tuple(self._jobs.items()):
            if job.result is not None and command_id not in pending:
                self._jobs.pop(command_id, None)

    def _run_job(self, job: _TTSJob) -> None:
        command_id = str(job.command["command_id"])
        with self._guard:
            host = self._host
            closed = self._closed
        if host is None or command_id not in host.snapshot.pending_commands:
            with self._guard:
                self._jobs.pop(command_id, None)
        elif closed:
            context = self._command_context(job.command, job.snapshot)
            command, _payload, common = context
            with self._guard:
                job.result = self._failed_observation(
                    command=command,
                    common=common,
                    reason="voice_tts_executor_closed",
                    started=False,
                )
        else:
            try:
                result = self._synthesize(job.command, job.snapshot)
            except Exception:
                # Persisted executing receipt remains recoverable if an artifact
                # write had an unknown outcome; never blindly retry synthesis.
                result = VoiceCommandExecutionResult.unknown("voice_tts_execution_failed")
                logger.warning("voice_tts_execution_outcome_unknown")
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
            with self._guard:
                self._prune_settled(host.snapshot.pending_commands)
                notifier = self._notify_delivery
            response_id = str(job.command.get("payload", {}).get("response_id") or "")
            response = job.snapshot.get("responses", {}).get(response_id, {})
            if notifier is not None:
                try:
                    notifier(str(response.get("voice_turn_id") or ""))
                except Exception:
                    logger.warning("voice_tts_delivery_notification_failed")

    def _synthesize(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, payload, common = context
        text_result = self.text_artifacts.read_text(str(payload.get("text_artifact_ref") or ""))
        if not text_result.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_text_artifact_unavailable",
                started=False,
            )
        if self.tts_client is None or not callable(getattr(self.tts_client, "synthesize", None)):
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_provider_unavailable",
                started=False,
            )

        if self._command_cancelled(str(command["command_id"])):
            return self._failed_observation(command=command, common=common,
                reason="invocation_cancelled", started=False)
        try:
            contextual = getattr(self.tts_client, "synthesize_command", None)
            if callable(contextual):
                synthesized = _resolve_awaitable(contextual(text_result.text,
                    invocation_id="voice-tts-" + hashlib.sha256(self._artifact_key(command).encode()).hexdigest(),
                    cancel_requested=lambda: self._command_cancelled(str(command["command_id"]))))
            else:
                # Generic effect port used by non-plugin embedders and tests.
                synthesized = _resolve_awaitable(self.tts_client.synthesize(text_result.text))
            audio, media_type = _coerce_synthesized_audio(synthesized)
        except TTSServiceError as exc:
            if not exc.outcome_known:
                return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
            return self._failed_observation(command=command, common=common, reason=exc.reason,
                started=bool(exc.origin), origin=exc.origin)
        except Exception:
            if callable(getattr(self.tts_client, "synthesize_command", None)):
                # An untyped service/transport exception is not a confirmed
                # provider failure. Keep the executing receipt for recovery.
                return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
            return self._failed_observation(
                command=command,
                common=common,
                reason="voice_tts_synthesis_failed",
                started=True,
            )

        artifact_key = self._artifact_key(command)
        origin = getattr(synthesized, "origin", None)
        stored = self.audio_artifacts.put_audio(
            artifact_key=artifact_key,
            audio=audio,
            media_type=media_type,
            **({"origin": origin} if origin else {}),
        )
        if not stored.ok:
            return self._failed_observation(
                command=command,
                common=common,
                reason=(
                    stored.reason
                    if stored.reason.startswith("voice_audio_artifact_")
                    else "voice_audio_artifact_write_failed"
                ),
                started=True,
            )
        return VoiceCommandExecutionResult.succeeded(
            self._event(
                command=command,
                common=common,
                stage="started",
                event_kind="voice.tts.started",
                payload=self._provider_payload(origin),
            ),
            self._event(
                command=command,
                common=common,
                stage="ready",
                event_kind="voice.tts.ready",
                payload={
                    "command_id": str(command["command_id"]),
                    "audio_artifact_ref": stored.artifact_ref,
                    **self._provider_payload(origin),
                },
            ),
        )

    def recover(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> VoiceCommandExecutionResult:
        command_id = str(command_record.get("command_id") or "") if isinstance(command_record, Mapping) else ""
        with self._guard:
            job = self._jobs.get(command_id)
            if job is not None:
                if job.command != command_record:
                    return VoiceCommandExecutionResult.failed("voice_tts_job_conflict")
                return job.result or VoiceCommandExecutionResult.running("voice_tts_running")
        context = self._command_context(command_record, snapshot_record)
        if isinstance(context, str):
            return VoiceCommandExecutionResult.failed(context)
        command, _payload, common = context
        artifact_ref = self._artifact_ref(command)
        recovered = self.audio_artifacts.read_audio(artifact_ref)
        if not recovered.ok:
            return VoiceCommandExecutionResult.unknown("voice_tts_outcome_unconfirmed")
        origin = getattr(recovered, "origin", {})
        return VoiceCommandExecutionResult.succeeded(
            self._event(
                command=command,
                common=common,
                stage="started",
                event_kind="voice.tts.started",
                payload=self._provider_payload(origin),
            ),
            self._event(
                command=command,
                common=common,
                stage="ready",
                event_kind="voice.tts.ready",
                payload={
                    "command_id": str(command["command_id"]),
                    "audio_artifact_ref": artifact_ref,
                    **self._provider_payload(origin),
                },
            ),
        )

    def _command_context(
        self,
        command_record: Mapping[str, Any],
        snapshot_record: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | str:
        if not isinstance(command_record, Mapping) or not isinstance(snapshot_record, Mapping):
            return "voice_tts_command_contract_invalid"
        command = dict(command_record)
        payload_value = command.get("payload")
        if (
            command.get("command_kind") != "start_tts"
            or not isinstance(payload_value, Mapping)
            or not str(command.get("command_id") or "")
            or not str(command.get("idempotency_key") or "")
        ):
            return "voice_tts_command_invalid"
        payload = dict(payload_value)
        response_id = str(payload.get("response_id") or "")
        speech_unit_id = str(payload.get("speech_unit_id") or "")
        text_artifact_ref = str(payload.get("text_artifact_ref") or "")
        response_generation = payload.get("response_generation")
        responses = snapshot_record.get("responses")
        response = responses.get(response_id) if isinstance(responses, Mapping) else None
        if (
            not response_id
            or not speech_unit_id
            or not text_artifact_ref
            or isinstance(response_generation, bool)
            or not isinstance(response_generation, int)
            or not isinstance(response, Mapping)
        ):
            return "voice_tts_command_invalid"
        voice_turn_id = str(response.get("voice_turn_id") or "")
        turn_revision = response.get("source_turn_revision")
        input_turns = snapshot_record.get("input_turns")
        input_turn = input_turns.get(voice_turn_id) if isinstance(input_turns, Mapping) else None
        voice_session_id = str(input_turn.get("voice_session_id") or "") if isinstance(input_turn, Mapping) else ""
        if (
            not voice_turn_id
            or not voice_session_id
            or isinstance(turn_revision, bool)
            or not isinstance(turn_revision, int)
        ):
            return "voice_tts_snapshot_context_invalid"
        return (
            command,
            payload,
            {
                "voice_turn_id": voice_turn_id,
                "voice_session_id": voice_session_id,
                "response_id": response_id,
                "speech_unit_id": speech_unit_id,
                "turn_revision": turn_revision,
                "response_generation": response_generation,
            },
        )

    def _failed_observation(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        reason: str,
        started: bool,
        origin: Mapping[str, str] | None = None,
    ) -> VoiceCommandExecutionResult:
        observations = []
        if started:
            observations.append(
                self._event(
                    command=command,
                    common=common,
                    stage="started",
                    event_kind="voice.tts.started",
                    payload=self._provider_payload(origin),
                )
            )
        observations.append(
            self._event(
                command=command,
                common=common,
                stage="failed",
                event_kind="voice.tts.failed",
                payload={
                    "command_id": str(command["command_id"]),
                    "reason_code": str(reason or "voice_tts_failed")[:128],
                    **self._provider_payload(origin),
                },
            )
        )
        return VoiceCommandExecutionResult.succeeded(*observations)

    def _command_cancelled(self, command_id: str) -> bool:
        with self._guard:
            return self._closed or (self._host is not None and command_id not in self._host.snapshot.pending_commands)

    def _provider_payload(self, origin: Mapping[str, str] | None = None) -> dict[str, str]:
        if origin is not None:
            return ({"provider_id": origin["plugin_id"], "provider_generation": origin.get("generation_id", "")}
                    if origin.get("plugin_id") else {})
        return {"provider_id": self.provider_id} if self.provider_id else {}

    def _event(
        self,
        *,
        command: Mapping[str, Any],
        common: Mapping[str, Any],
        stage: str,
        event_kind: str,
        payload: Mapping[str, Any],
    ) -> VoiceEvent:
        now = datetime.now(timezone.utc).isoformat()
        return VoiceEvent(
            event_id=self._event_id(command, stage),
            event_kind=event_kind,
            occurred_at=now,
            recorded_at=now,
            conversation_id=self.conversation_id,
            voice_session_id=str(common["voice_session_id"]),
            conversation_generation=self.conversation_generation,
            producer="akane.voice_tts",
            voice_turn_id=str(common["voice_turn_id"]),
            response_id=str(common["response_id"]),
            speech_unit_id=str(common["speech_unit_id"]),
            turn_revision=int(common["turn_revision"]),
            response_generation=int(common["response_generation"]),
            causation_id=str(command.get("command_id") or ""),
            payload=dict(payload),
        )

    @staticmethod
    def _artifact_key(command: Mapping[str, Any]) -> str:
        return f"voice-tts:{str(command.get('idempotency_key') or '')}"

    @classmethod
    def _artifact_ref(cls, command: Mapping[str, Any]) -> str:
        digest = hashlib.sha256(cls._artifact_key(command).encode("utf-8")).hexdigest()
        return f"voice-audio:{digest}"

    @staticmethod
    def _event_id(command: Mapping[str, Any], stage: str) -> str:
        material = f"{str(command.get('command_id') or '')}:{stage}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"voice_evt_{digest[:32]}"


def _coerce_synthesized_audio(value: Any) -> tuple[bytes, str]:
    if isinstance(value, bytes):
        audio = value
        media_type = "audio/mpeg"
    else:
        audio = getattr(value, "audio", None)
        media_type = getattr(value, "media_type", None)
    if not isinstance(audio, bytes) or not audio:
        raise ValueError("voice_tts_audio_invalid")
    normalized_media_type = str(media_type or "audio/mpeg").strip().lower()
    return audio, normalized_media_type


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(value))
        except BaseException as exc:  # preserved and reraised on the caller
            failure.append(exc)

    worker = threading.Thread(
        target=run,
        name=f"voice-tts-await-{uuid.uuid4().hex[:8]}",
        daemon=True,
    )
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    if not result:
        raise RuntimeError("voice_tts_awaitable_no_result")
    return result[0]
