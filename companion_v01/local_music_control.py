"""Desktop queue control with a real UI execution receipt, not a queued ACK."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import uuid
from dataclasses import dataclass, field

from .turn_coordination import current_cancellation_check


@dataclass
class _Request:
    command: dict
    cancelled: object
    result: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)
    started: bool = False


class LocalMusicControlService:
    def __init__(self, satellite, bot_id, *, timeout_seconds=15):
        self.satellite, self.bot_id = satellite, bot_id
        self.timeout_seconds = timeout_seconds
        self._pending = {}
        self._completed = {}
        self._lock = threading.RLock()

    def available(self):
        snapshot = self.satellite.diagnostics()
        return bool(snapshot.get("desktopUiConnected") and snapshot.get("desktopUiBotId") == self.bot_id)

    def execute(self, call, context):
        if context.client_mode != "desktop_pet":
            return {"ok": False, "status": "unavailable", "reason": "desktop_client_required"}
        key = (context.profile_user_id, context.session_id, context.character_pack_id, context.invocation_id or uuid.uuid4().hex)
        with self._lock:
            if key in self._completed:
                return dict(self._completed[key])
            previous = next((p for p in self._pending.values() if p.command["request_key"] == key), None)
            if previous is None:
                operation_id = uuid.uuid4().hex
                command = {"operation_id": operation_id, "request_key": key,
                           "profile_user_id": context.profile_user_id, "session_id": context.session_id,
                           "character_pack_id": context.character_pack_id,
                           "action": call["action"], "source_id": call.get("source_id", ""),
                           "expires_at": time.time() + self.timeout_seconds}
                pending = _Request(command, context.cancel_requested or current_cancellation_check())
                self._pending[operation_id] = pending
            else:
                pending = previous
        if previous is not None:
            try:
                return pending.result.result(timeout=self.timeout_seconds)
            except concurrent.futures.TimeoutError:
                return {"ok": False, "status": "execution_unknown", "reason": "desktop_music_receipt_timeout"}
        async def deliver():
            command = {k: v for k, v in pending.command.items() if k != "request_key"}
            return await self.satellite.deliver_agent_frame({"_desktop_music_control": command}, bot_id=self.bot_id)
        try:
            delivery = asyncio.run(deliver())
            if not delivery.get("ok"):
                result = {"ok": False, "status": "execution_unknown" if "timeout" in str(delivery.get("reason")) else "unavailable",
                          "reason": delivery.get("reason") or "desktop_music_delivery_failed"}
            else:
                try:
                    result = pending.result.result(timeout=max(.01, pending.command["expires_at"] - time.time()))
                except concurrent.futures.TimeoutError:
                    result = {"ok": False, "status": "execution_unknown", "reason": "desktop_music_receipt_timeout"}
        except Exception:
            result = {"ok": False, "status": "execution_unknown", "reason": "desktop_music_transport_failed"}
        with self._lock:
            self._pending.pop(operation_id, None)
            if not pending.result.done():
                pending.result.set_result(result)
            self._completed[key] = dict(result)
            while len(self._completed) > 128:
                self._completed.pop(next(iter(self._completed)))
        return result

    def acknowledge(self, payload):
        with self._lock:
            pending = self._pending.get(str(payload.get("operation_id") or ""))
            if pending is None or pending.result.done():
                return {"ok": False, "reason": "desktop_music_request_expired"}
            if payload.get("phase") == "claim":
                if pending.started or time.time() >= pending.command["expires_at"] or (pending.cancelled and pending.cancelled()):
                    return {"ok": False, "reason": "desktop_music_request_revoked"}
                pending.started = True
                return {"ok": True}
            if not pending.started:
                return {"ok": False, "reason": "desktop_music_not_claimed"}
            ok = payload.get("ok") is True
            result = {"ok": ok, "status": "succeeded" if ok else "failed", "action": pending.command["action"]}
            if not ok:
                result["reason"] = str(payload.get("reason") or "desktop_music_execution_failed")[:100]
            if isinstance(payload.get("title"), str):
                result["title"] = payload["title"][:160]
            pending.result.set_result(result)
            return {"ok": True}
