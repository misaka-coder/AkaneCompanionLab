"""One thinking executor, job registry, cancellation fence and shared lock."""
from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from typing import Any, Callable

from ...background_tasks import BackgroundTaskRunner
from ..host import AkaneVoiceRuntimeHost
from ..stream_bridge import VoiceStreamEventFactory, VoiceTextArtifactPort
from .commands import ThinkingCommands
from .context import GenerationContext, _GenerationFailure, _GenerationJob
from .recovery import GenerationRecovery
from .scheduling import GenerationScheduling
from .stream import GenerationStream


class AkaneThinkingAgentCommandExecutor(ThinkingCommands, GenerationScheduling, GenerationRecovery, GenerationStream, GenerationContext):
    """Start Akane's existing Thinking Agent after VoiceCore commits input.

    Command observations remain synchronous and durable. The potentially slow
    model stream starts only after the Host has persisted and applied the
    ``generation_started`` observation."""

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
        conversation_ref: str = "",
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
        self.conversation_ref = conversation_ref
        self._event_factories: dict[str, VoiceStreamEventFactory] = {}
        self._speech_delivery_by_turn: dict[str, bool] = {}
        self._delivery_notifiers: dict[str, Callable[[], None]] = {}
        self._presentation_sinks: dict[str, Callable[[str, Mapping[str, Any]], None]] = {}
        self._jobs: dict[str, _GenerationJob] = {}
        self._running_response_ids: set[str] = set()
        self._cancel_requested_response_ids: set[str] = set()
        self._generation_failures: dict[str, _GenerationFailure] = {}
        self._settling_failure_ids: set[str] = set()
        self._guard = threading.RLock()
        self._closed = False

    def register_turn(
        self,
        *,
        voice_turn_id: str,
        event_factory: VoiceStreamEventFactory,
        speech_delivery_enabled: bool = False,
        delivery_notifier: Callable[[], None] | None = None,
        presentation_sink: Callable[[str, Mapping[str, Any]], None] | None = None,
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
            else:
                self._delivery_notifiers.pop(normalized_turn_id, None)
            if presentation_sink is not None:
                self._presentation_sinks[normalized_turn_id] = presentation_sink
            else:
                self._presentation_sinks.pop(normalized_turn_id, None)

    def has_running_turn(self, turn_id: str) -> bool:
        with self._guard:
            return any(job.voice_turn_id == turn_id and response in self._running_response_ids
                       for response, job in self._jobs.items())

    def close(self) -> None:
        with self._guard:
            self._closed = True
            self._event_factories.clear()
            self._speech_delivery_by_turn.clear()
            self._delivery_notifiers.clear()
            self._presentation_sinks.clear()
            self._cancel_requested_response_ids.update(self._running_response_ids)
            for job in tuple(self._jobs.values()):
                if job.response_id not in self._running_response_ids:
                    self._release_generation_job(job)
        settled = self._retry_failed_generations()
        if not settled.ok:
            raise RuntimeError("voice_thinking_shutdown_settlement_pending")

    def _release_generation_job(self, job: _GenerationJob) -> None:
        with self._guard:
            if self._jobs.get(job.response_id) is not job:
                return
            self._running_response_ids.discard(job.response_id)
            if job.response_id in self._generation_failures:
                return
            self._cancel_requested_response_ids.discard(job.response_id)
            self._jobs.pop(job.response_id)
            if (
                job.commitment != "speculative"
                and not any(other.voice_turn_id == job.voice_turn_id for other in self._jobs.values())
                and self._event_factories.get(job.voice_turn_id) is job.event_factory
                and self._speech_delivery_by_turn.get(job.voice_turn_id, False) == job.speech_delivery_enabled
                and self._delivery_notifiers.get(job.voice_turn_id) is job.delivery_notifier
                and self._presentation_sinks.get(job.voice_turn_id) is job.presentation_sink
            ):
                self._event_factories.pop(job.voice_turn_id, None)
                self._speech_delivery_by_turn.pop(job.voice_turn_id, None)
                self._delivery_notifiers.pop(job.voice_turn_id, None)
                self._presentation_sinks.pop(job.voice_turn_id, None)

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
        responses = [
            response
            for response in host.snapshot.responses.values()
            if str(getattr(response, "voice_turn_id", "") or "") == voice_turn_id
        ]
        return next(
            (
                response
                for response in reversed(responses)
                if str(getattr(getattr(response, "state", None), "value", "") or "")
                not in {"failed", "cancelled", "discarded"}
            ),
            responses[-1] if responses else None,
        )
