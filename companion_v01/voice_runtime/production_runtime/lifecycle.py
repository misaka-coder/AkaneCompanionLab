"""Service shutdown and owning playback-channel release."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from ..playback_delivery import VoicePlaybackDeliveryResult
from .events import _safe_reason


@dataclass
class _RuntimeResources:
    """Keep unfinished cleanup owned, including partially constructed runtimes."""

    channels: list[Any] = field(default_factory=list)
    executors: list[Any] = field(default_factory=list)
    leases: list[Any] = field(default_factory=list)
    pending_background: bool = False
    _guard: Any = field(default_factory=threading.RLock, repr=False)

    def close_channels(self) -> list[str]:
        failures = []
        with self._guard:
            pending = []
            for channel in self.channels:
                try:
                    result = channel.close(reason="voice_runtime_stopped")
                    if not isinstance(result, VoicePlaybackDeliveryResult):
                        reason = "voice_playback_channel_close_result_invalid"
                    elif not result.ok:
                        reason = _safe_reason(result.reason, fallback="voice_playback_channel_close_failed")
                    else:
                        continue
                except Exception:
                    reason = "voice_playback_channel_close_failed"
                pending.append(channel)
                failures.append(reason)
            self.channels = pending
        return failures

    def close_executors(self) -> list[str]:
        return self._close_objects("executors", "voice_runtime_executor_close_failed")

    def close_leases(self) -> list[str]:
        return self._close_objects("leases", "voice_private_lease_close_failed")

    def _close_objects(self, field_name: str, reason: str) -> list[str]:
        failures = []
        with self._guard:
            pending = []
            for obj in getattr(self, field_name):
                try:
                    obj.close()
                except Exception:
                    pending.append(obj)
                    failures.append(reason)
            setattr(self, field_name, pending)
        return failures


class RuntimeLifecycle:
    """Lifecycle methods share the owning service state and lock."""

    def close(self) -> dict[str, Any]:
        # A nested or concurrent close must not report completion while the
        # owning close is still submitting receipts or joining workers.
        if not self._shutdown_guard.acquire(blocking=False):
            return {"status": "degraded", "reason": "voice_runtime_shutdown_in_progress",
                    "failures": ["voice_runtime_shutdown_in_progress"], "retryable": True}
        try:
            with self._guard:
                if self._shutdown_status is not None and self._shutdown_status["status"] == "stopped":
                    return {"status": "stopped", "reason": "already_stopped"}
                if not self._closed:
                    self._closed = True
                    self._shutdown_host_count = len(self._hosts)
                    self._shutdown_resources = _RuntimeResources(
                        channels=list(self._delivery_channels.values()),
                        executors=[*self._thinking_executors.values(), *self._semantic_executors.values(),
                                   *self._candidate_executors.values(), *self._tts_executors.values()],
                        leases=[lease for host in self._hosts.values()
                                if (lease := getattr(host, "private_lease", None)) is not None],
                    )
                    self._hosts.clear()
                    self._thinking_executors.clear()
                    self._semantic_executors.clear()
                    self._candidate_executors.clear()
                    self._playback_executors.clear()
                    self._tts_executors.clear()
                    self._text_artifacts.clear()
                    self._audio_artifacts.clear()
                    self._delivery_channels.clear()
                resources = [self._shutdown_resources, *self._initializing_resources.values()]
            failures = []
            for owned in resources:
                failures.extend(owned.close_channels())
                failures.extend(owned.close_executors())
            try:
                background_stopped = self._background_tasks.close(timeout=10.0)
            except Exception:
                background_stopped = False
            if not background_stopped:
                failures.append("voice_runtime_shutdown_incomplete")
            with self._guard:
                opening_calls = self._opening_calls
            if opening_calls:
                failures.append("voice_runtime_call_open_pending")
            elif not self._drain_unclaimed_sessions():
                failures.append("asr_provider_cancel_pending")
            if not failures:
                for owned in resources:
                    failures.extend(owned.close_leases())
            status = {
                "status": "degraded" if failures else "stopped",
                "reason": "voice_runtime_shutdown_incomplete" if failures else "",
                "host_count": self._shutdown_host_count,
                "failures": list(dict.fromkeys(failures)),
                "retryable": bool(failures),
            }
            with self._guard:
                self._shutdown_status = status
                if not failures:
                    self._initializing_resources.clear()
            return dict(status)
        finally:
            self._shutdown_guard.release()

    def _cleanup_failed_initialization(self, conversation_id: str, owned: _RuntimeResources) -> bool:
        failures = owned.close_executors()
        if owned.pending_background:
            try:
                owned.pending_background = not self._background_tasks.wait_idle(
                    lane=f"voice-thinking-{conversation_id[-16:]}", timeout=0.1,
                )
            except Exception:
                owned.pending_background = True
        if not failures and not owned.pending_background:
            failures.extend(owned.close_leases())
        if failures or owned.pending_background:
            return False
        with self._guard:
            if self._initializing_resources.get(conversation_id) is owned:
                self._initializing_resources.pop(conversation_id)
        return True

    def _drain_unclaimed_sessions(self) -> bool:
        with self._guard:
            pending = list(self._unclaimed_asr_sessions.values())
        for record in pending:
            if record.future is not None and record.future.done():
                try:
                    cancelled = record.future.result()
                except Exception:
                    cancelled = False
                if cancelled:
                    with self._guard:
                        if self._unclaimed_asr_sessions.get(id(record.session)) is record:
                            self._unclaimed_asr_sessions.pop(id(record.session))
                    continue
                record.future = None
            if record.future is None and record.loop.is_running() and not record.loop.is_closed():
                cancellation = record.cancel()
                try:
                    record.future = asyncio.run_coroutine_threadsafe(cancellation, record.loop)
                except RuntimeError:
                    cancellation.close()
        with self._guard:
            return not self._unclaimed_asr_sessions

    def wait_idle(self, *, timeout: float = 10.0) -> bool:
        return self._background_tasks.wait_idle(timeout=timeout)

    def _release_delivery_channel(
        self,
        canonical_conversation_id: str,
        voice_turn_id: str,
    ) -> None:
        with self._guard:
            channel = self._delivery_channels.get((canonical_conversation_id, voice_turn_id))
        if channel is not None:
            self._release_owned_delivery_channel(canonical_conversation_id, voice_turn_id, channel)

    def _release_owned_delivery_channel(self, canonical_conversation_id: str, voice_turn_id: str, channel: Any) -> None:
        with self._guard:
            key = (canonical_conversation_id, voice_turn_id)
            if self._delivery_channels.get(key) is not channel:
                return
            self._delivery_channels.pop(key)
            executor = self._playback_executors.get(canonical_conversation_id)
        if executor is not None:
            executor.unregister_channel(voice_turn_id, channel)
