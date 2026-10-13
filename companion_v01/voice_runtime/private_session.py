"""Host binding for voice memory and the shared private conversation lease."""
from __future__ import annotations

import asyncio
import threading
import time

from .asr_realtime_turn import VoiceASRRealtimeTurnCoordinator


class PrivateVoiceMemory:
    """Capture trusted ingress once; background voice workers keep that scope."""

    def __init__(self, manager, policy, payload, lease=None):
        self.manager, self.policy, self.payload, self.lease = manager, policy, dict(payload), lease

    def record_voice_projection(self, record, **kwargs):
        record = dict(record)
        if record.get("kind") == "message.user.voice":
            record["payload"] = {**record["payload"], "channel": "desktop"}
        with self.policy.scope(self.payload):
            result = self.manager.record_voice_projection(record, **kwargs)
        kind = record.get("kind")
        if (result.get("ok") and result.get("status") not in {"duplicate", "already_completed"}
                and self.policy.store is not None and kind in {"message.user.voice", "message.assistant.voice"}):
            self.policy.store.record_owner_private_voice_activity(
                character_pack_id=self.payload["character_pack_id"],
                role="user" if kind == "message.user.voice" else "assistant",
                timestamp=int(kwargs.get("timestamp") or time.time()))
        if result.get("ok") and self.lease and kind in {"message.assistant.voice", "event.voice.interaction"}:
            self.lease.projection_settled(str(record.get("payload", {}).get("voice_turn_id") or ""))
        if result.get("ok") and self.lease and kind == "event.voice.failure":
            for affected in record.get("payload", {}).get("affected_ids", ()):
                self.lease.projection_settled(str(affected), failure=True)
        return result

    def resolve_voice_projection_turn(self, **kwargs):
        with self.policy.scope(self.payload):
            return self.manager.resolve_voice_projection_turn(**kwargs)

    def build_context_projection(self, **kwargs):
        with self.policy.scope(self.payload):
            return self.manager.build_context_projection(**kwargs)


class PrivateVoiceLease:
    """Hold only a live voice exchange, including acknowledged playback.

    Overlapping inputs in one call reuse its lease so barge-in never waits on
    its own playback. Idle calls leave QQ and typed desktop input unblocked.
    """

    def __init__(self, coordinator, character):
        self.coordinator, self.character = coordinator, character
        self._turns = set()
        self._projected = set()
        self._failure_projected = set()
        self._guard = threading.RLock()
        self._lock = asyncio.Lock()
        self._hold = None
        self._loop = None

    async def acquire(self, turn_id):
        async with self._lock:
            self._loop = asyncio.get_running_loop()
            if self._hold is None:
                hold = self.coordinator.hold("master", "master", actor_id="master", channel="desktop_voice",
                                             turn_kind="voice", memory_domain=self.character)
                await hold.__aenter__()
                self._hold = hold
            with self._guard:
                self._turns.add(turn_id)

    def settled(self, turn_id):
        with self._guard:
            if turn_id not in self._turns:
                return
            self._turns.discard(turn_id)
            self._projected.discard(turn_id)
            self._failure_projected.discard(turn_id)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(self._release_idle()))

    async def _release_idle(self):
        async with self._lock:
            with self._guard:
                if self._turns or self._hold is None:
                    return
                hold, self._hold = self._hold, None
            await hold.__aexit__(None, None, None)

    def settle_failed_inputs(self, host, executor):
        with self._guard:
            turns = tuple(self._turns)
        for turn_id in turns:
            turn = host.snapshot.input_turns.get(turn_id)
            failed = False
            if turn_id in self._failure_projected:
                responses = [r for r in host.snapshot.responses.values()
                             if r.voice_turn_id == turn_id and r.commitment.value == "committed"]
                failed = bool(responses) and all(
                    r.state.value in {"failed", "cancelled", "discarded", "completed"} for r in responses)
            if failed or turn_id in self._projected or (turn is not None and turn.state.value in {"failed", "cancelled", "discarded"}):
                if not executor.has_running_turn(turn_id):
                    self.settled(turn_id)

    def projection_settled(self, turn_id, *, failure=False):
        with self._guard:
            if turn_id in self._turns:
                (self._failure_projected if failure else self._projected).add(turn_id)

    def close(self):
        """Only called after voice workers have stopped and channels closed."""
        with self._guard:
            turns = tuple(self._turns)
        for turn_id in turns:
            self.settled(turn_id)


class PrivateVoiceCoordinator(VoiceASRRealtimeTurnCoordinator):
    def __init__(self, *, private_lease, private_admission=None, **kwargs):
        super().__init__(**kwargs)
        self.private_lease = private_lease
        self.private_admission = private_admission

    async def open(self):
        if self.private_admission:
            self.private_admission(self.bridge.voice_turn_id)
        await self.private_lease.acquire(self.bridge.voice_turn_id)
        try:
            result = await super().open()
        except BaseException:
            self.private_lease.settled(self.bridge.voice_turn_id)
            raise
        if not result.ok:
            self.private_lease.settled(self.bridge.voice_turn_id)
        return result

    async def cancel(self, **kwargs):
        result = await super().cancel(**kwargs)
        self.bridge.host.drive_once()
        return result

    async def settle_finalize(self):
        result = await super().settle_finalize()
        self.bridge.host.drive_once()
        return result

    async def feed_audio(self, audio):
        result = await super().feed_audio(audio)
        if not result.ok:
            self.bridge.host.drive_once()
        return result
