"""The sole TTS executor state, lifecycle and synchronous entry."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any, Callable

from ...background_tasks import BackgroundTaskRunner
from ..host import VoiceCommandExecutionResult
from .commands import TTSCommandContext, VoiceAudioArtifactPort, VoiceReadableTextArtifactPort, _TTSJob
from .results import TTSResults
from .scheduling import TTSScheduling
from .synthesis import TTSSynthesis


class AkaneVoiceTTSCommandExecutor(TTSScheduling, TTSSynthesis, TTSResults, TTSCommandContext):
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
        self._closing = False

    def bind_host(self, host: Any, *, notify_delivery: Callable[[str], None]) -> None:
        with self._guard:
            self._host = host
            self._notify_delivery = notify_delivery

    def close(self) -> None:
        with self._guard:
            self._closed = True
            if self._closing:
                raise RuntimeError("voice_tts_shutdown_settlement_pending")
            self._closing = True
            host = self._host
            jobs = tuple(self._jobs.values())
        try:
            if not jobs:
                return
            with self._guard:
                for job in jobs:
                    if job.result is None and not job.started:
                        context = self._command_context(job.command, job.snapshot)
                        if isinstance(context, str):
                            raise RuntimeError("voice_tts_shutdown_settlement_pending")
                        command, _payload, common = context
                        job.result = self._failed_observation(
                            command=command, common=common, reason="voice_tts_executor_closed", started=False,
                        )
            if host is not None:
                # Recover only existing receipts; closing cannot start fresh effects.
                confirmation = host.drain_command_receipts()
                if not self._refresh_confirmations(host, confirmation):
                    raise RuntimeError("voice_tts_shutdown_settlement_pending")
                with self._guard:
                    self._prune_settled(host.snapshot.pending_commands)
                    finished = tuple(job for job in jobs if self._jobs.get(str(job.command["command_id"])) is not job)
                for job in finished:
                    self._notify_job_delivery(job)
            with self._guard:
                if self._jobs:
                    raise RuntimeError("voice_tts_shutdown_settlement_pending")
        except Exception:
            raise RuntimeError("voice_tts_shutdown_settlement_pending") from None
        finally:
            with self._guard:
                self._closing = False

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

    def _command_cancelled(self, command_id: str) -> bool:
        with self._guard:
            return self._closed or (self._host is not None and command_id not in self._host.snapshot.pending_commands)
