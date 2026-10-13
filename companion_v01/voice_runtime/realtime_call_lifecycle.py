"""Call failure receipts, resource cleanup and aggregate observations."""

from __future__ import annotations

import asyncio
import time
from typing import Any


from .realtime_protocol import (
    VOICE_REALTIME_CALL_PROTOCOL_VERSION,
    VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS,
)


class RealtimeCallLifecycleMixin:
    """Call failure receipts, resource cleanup and aggregate observations."""

    async def _send_result_failure(
        self,
        result: Any,
        *,
        terminal: bool,
    ) -> None:
        await self._send_failed(
            str(getattr(result, "reason", "") or "voice_realtime_failed"),
            retryable=bool(getattr(result, "retryable", False)),
            safe_public_summary=str(getattr(result, "safe_public_summary", "") or ""),
            terminal=terminal,
        )

    async def _send_failed(
        self,
        reason: str,
        *,
        retryable: bool = False,
        safe_public_summary: str = "",
        terminal: bool,
    ) -> None:
        payload = {
            "type": "server.failed",
            "protocol_version": VOICE_REALTIME_CALL_PROTOCOL_VERSION,
            "status": "failed",
            "reason": str(reason or "voice_realtime_failed")[:128],
            "retryable": bool(retryable),
            "message": str(safe_public_summary or "实时语音链路暂时不可用。")[:160],
            "terminal": bool(terminal),
            **({"voice_turn_id": self.current_request.voice_turn_id} if self.current_request is not None else {}),
        }
        try:
            await self.websocket.send_json(payload)
        except Exception:
            pass
        if not terminal:
            return
        self._terminal_reason = str(reason or "voice_realtime_failed")[:128]
        self.terminal = True
        self._observe_once(ok=False, reason=self._terminal_reason)
        try:
            await self.websocket.close(
                code=1011,
                reason="voice_realtime_call_failed",
            )
        except Exception:
            pass

    async def _cleanup(self) -> None:
        if self.receive_task is not None and not self.receive_task.done():
            self.receive_task.cancel()
            await asyncio.gather(self.receive_task, return_exceptions=True)
        if self.finalize_task is not None and not self.finalize_task.done():
            self.finalize_task.cancel()
            await asyncio.gather(self.finalize_task, return_exceptions=True)
        for task in tuple(self.delivery_tasks):
            if not task.done():
                task.cancel()
        if self.delivery_tasks:
            await asyncio.gather(
                *tuple(self.delivery_tasks),
                return_exceptions=True,
            )
        self.delivery_tasks.clear()
        if self.retired_delivery_tasks:
            await asyncio.gather(
                *tuple(self.retired_delivery_tasks),
                return_exceptions=True,
            )
            self.retired_delivery_tasks.clear()
        reason = self._terminal_reason or ("voice_realtime_call_closed" if self.call_closed else "client_disconnected")
        for voice_turn_id in tuple(self.delivery_channels):
            self._close_delivery_channel(voice_turn_id, reason=reason)
        if self.call is None or self.call_closed:
            return
        if self.coordinator is not None:
            try:
                await self.coordinator.cancel(reason=reason)
            except Exception:
                pass
        try:
            await self.call.cancel()
        except Exception:
            pass

    def _close_delivery_channel(
        self,
        voice_turn_id: str,
        *,
        reason: str,
    ) -> None:
        channel = self.delivery_channels.get(voice_turn_id)
        if channel is not None:
            try:
                channel.close(reason=reason)
            except Exception:
                pass
        self._remove_delivery_channel(voice_turn_id)

    def _remove_delivery_channel(self, voice_turn_id: str) -> None:
        self.delivery_channels.pop(voice_turn_id, None)
        release_response = getattr(self.call, "release_response", None)
        if callable(release_response):
            try:
                release_response(voice_turn_id)
            except Exception:
                pass
        self.delivery_owners = {key: owner for key, owner in self.delivery_owners.items() if owner != voice_turn_id}
        self.control_owners = {key: owner for key, owner in self.control_owners.items() if owner != voice_turn_id}
        for task, owner in tuple(self.delivery_tasks.items()):
            if owner != voice_turn_id:
                continue
            self.delivery_tasks.pop(task, None)
            if not task.done():
                task.cancel()
                self.retired_delivery_tasks.append(task)

    def _observe_once(self, *, ok: bool, reason: str = "") -> None:
        if self._observed:
            return
        self._observed = True
        duration_ms = (time.perf_counter() - self.started_at) * 1000
        try:
            self.runtime_metrics.observe_request(
                "asr_realtime_call",
                duration_ms=duration_ms,
                ok=bool(ok),
            )
        except Exception:
            pass
        try:
            self.log_event(
                "asr_realtime_call_complete",
                ok=bool(ok),
                **({"reason": str(reason or "voice_realtime_failed")[:128]} if not ok else {}),
                duration_ms=round(duration_ms, 1),
                completed_turns=self.completed_turns,
                input_frames=self.input_frames,
                input_bytes=self.input_bytes,
                provider=self.provider_id,
            )
        except Exception:
            pass

    def _log_transport_exception(self, exc: Exception) -> None:
        try:
            self.log_event(
                "asr_realtime_call_transport_exception",
                error_type=type(exc).__name__[:96],
                call_opened=self.call is not None,
                active_turn=self.current_request is not None,
                provider=self.provider_id,
                completed_turns=self.completed_turns,
                input_frames=self.input_frames,
                input_bytes=self.input_bytes,
            )
        except Exception:
            pass

    def _input_inactivity_wait_seconds(self) -> float | None:
        if self.coordinator is None or self.finalize_task is not None or self.turn_final_sent:
            return None
        elapsed = time.perf_counter() - self.last_client_activity_at
        return max(
            0.05,
            VOICE_REALTIME_INPUT_INACTIVITY_TIMEOUT_SECONDS - elapsed,
        )
