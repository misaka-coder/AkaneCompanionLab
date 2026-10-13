"""Exercise async cancellation and playback notification failure boundaries."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from companion_v01.voice_runtime.realtime_transport import (
    VoiceRealtimeCallWebSocketSession,
    VoiceRealtimeOpenRequest,
    VoiceRealtimeWebSocketSession,
)


def open_request(voice_turn_id="turn-one"):
    return VoiceRealtimeOpenRequest(
        profile_user_id="alice",
        conversation_id="conversation",
        session_id="session",
        voice_turn_id=voice_turn_id,
        audio_stream_id="audio-" + voice_turn_id,
        disposition="message",
        language="",
        character_pack_id="character",
        input_format="s16le",
        sample_rate=16000,
        channels=1,
    )


class RecordingSocket:
    def __init__(self):
        self.frames = []
        self.closes = []
        self.receiving = asyncio.Event()

    async def accept(self):
        pass

    async def receive(self):
        await self.receiving.wait()
        return {"type": "websocket.disconnect"}

    async def send_json(self, frame):
        self.frames.append(frame)

    async def close(self, **kwargs):
        self.closes.append(kwargs)


class RealtimeTurnCancellationTests(unittest.IsolatedAsyncioTestCase):
    def session(self, cancellation):
        socket = RecordingSocket()
        session = VoiceRealtimeWebSocketSession(
            websocket=socket, coordinator_factory=None, runtime_metrics=Mock(), log_event=Mock()
        )
        session.open_request = open_request()
        session.final_sent = True
        session.coordinator = SimpleNamespace(cancel_response=cancellation)
        session.delivery_channel = SimpleNamespace(close=Mock())
        return session, socket

    async def test_async_cancel_finishes_before_a_success_receipt(self):
        cancelled = []

        async def cancel_response(*, reason):
            await asyncio.sleep(0)
            cancelled.append(reason)
            return SimpleNamespace(status="accepted")

        session, socket = self.session(cancel_response)
        await session._handle_cancel({"reason": "user-stopped"})

        self.assertEqual(cancelled, ["user-stopped"])
        self.assertEqual([frame["type"] for frame in socket.frames], ["server.cancelled"])
        session.delivery_channel.close.assert_called_once_with(reason="user-stopped")
        self.assertEqual(socket.closes[0]["code"], 1000)

    async def test_async_cancel_failure_is_reported_without_a_success_receipt(self):
        cancellation = AsyncMock(return_value=SimpleNamespace(status="failed", reason="response_still_running"))
        session, socket = self.session(cancellation)
        await session._handle_cancel({"reason": "user-stopped"})

        cancellation.assert_awaited_once_with(reason="user-stopped")
        self.assertEqual([frame["type"] for frame in socket.frames], ["server.failed"])
        self.assertEqual(socket.frames[0]["reason"], "response_still_running")
        session.delivery_channel.close.assert_not_called()

    async def test_cancel_exception_becomes_an_observable_failure(self):
        for cancellation in (
            Mock(side_effect=RuntimeError("cancel failed")),
            AsyncMock(side_effect=RuntimeError("cancel failed")),
        ):
            with self.subTest(asynchronous=isinstance(cancellation, AsyncMock)):
                session, socket = self.session(cancellation)
                await session._handle_cancel({"reason": "user-stopped"})

                self.assertEqual([frame["type"] for frame in socket.frames], ["server.failed"])
                self.assertEqual(socket.frames[0]["reason"], "voice_response_cancel_failed")
                session.delivery_channel.close.assert_not_called()

    async def test_failed_delivery_notification_terminates_instead_of_rearming_forever(self):
        session, socket = self.session(Mock())
        session.delivery_channel = SimpleNamespace(
            wait_activity=AsyncMock(side_effect=RuntimeError("delivery unavailable")),
            response_outcome=lambda: SimpleNamespace(response_terminal=False),
            take_outbound=lambda: None,
            close=Mock(),
        )
        session._arm_delivery_wait()
        await asyncio.wait_for(session.run(), timeout=0.3)

        self.assertTrue(session.terminal)
        self.assertEqual(socket.frames[0]["type"], "server.failed")
        self.assertEqual(socket.frames[0]["reason"], "voice_playback_wait_failed")
        session.delivery_channel.close.assert_called_once_with(reason="voice_playback_wait_failed")
        self.assertTrue(session.receive_task.done())


class RealtimeCallLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        socket = RecordingSocket()
        session = VoiceRealtimeCallWebSocketSession(
            websocket=socket, call_factory=None, runtime_metrics=Mock(), log_event=Mock()
        )
        session.call = SimpleNamespace(cancel=AsyncMock(), release_response=Mock())
        session.current_request = open_request("new-turn")
        session.coordinator = SimpleNamespace(cancel=AsyncMock())
        return session, socket

    def channel(self, waiter):
        return SimpleNamespace(
            wait_activity=waiter,
            response_outcome=lambda: SimpleNamespace(response_terminal=False),
            take_outbound=lambda: None,
            close=Mock(),
        )

    async def test_failed_or_cancelled_delivery_wait_fails_and_cleans_the_call(self):
        for error_type in (RuntimeError, asyncio.CancelledError):
            with self.subTest(error_type=error_type.__name__):
                session, socket = self.session()
                parked = asyncio.Event()
                attempts = 0

                async def wait_activity():
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise error_type()
                    await parked.wait()

                old_channel = self.channel(wait_activity)
                new_channel = self.channel(parked.wait)
                session.delivery_channels = {"old-turn": old_channel, "new-turn": new_channel}
                session._arm_delivery_wait("old-turn")
                session._arm_delivery_wait("new-turn")
                original_tasks = tuple(session.delivery_tasks)
                try:
                    await asyncio.wait_for(session.run(), timeout=0.3)
                finally:
                    await asyncio.gather(*original_tasks, return_exceptions=True)

                self.assertEqual(attempts, 1)
                self.assertEqual([frame["type"] for frame in socket.frames], ["server.failed"])
                self.assertEqual(socket.frames[0]["reason"], "voice_playback_wait_failed")
                old_channel.close.assert_called_once_with(reason="voice_playback_wait_failed")
                new_channel.close.assert_called_once_with(reason="voice_playback_wait_failed")
                session.coordinator.cancel.assert_awaited_once_with(reason="voice_playback_wait_failed")
                session.call.cancel.assert_awaited_once()
                self.assertFalse(session.delivery_channels)
                self.assertFalse(session.delivery_tasks)
                self.assertTrue(all(task.done() for task in original_tasks))

    async def test_parent_cancellation_propagates_after_cleanup(self):
        session, socket = self.session()
        started = asyncio.Event()
        parked = asyncio.Event()

        async def wait_activity():
            started.set()
            await parked.wait()

        channel = self.channel(wait_activity)
        session.delivery_channels["old-turn"] = channel
        session._arm_delivery_wait("old-turn")
        delivery_tasks = tuple(session.delivery_tasks)
        task = asyncio.create_task(session.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=0.3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.assertFalse(socket.frames)
        channel.close.assert_called_once_with(reason="client_disconnected")
        session.coordinator.cancel.assert_awaited_once_with(reason="client_disconnected")
        session.call.cancel.assert_awaited_once()
        self.assertFalse(session.delivery_tasks)
        self.assertFalse(session.retired_delivery_tasks)
        self.assertTrue(all(task.done() for task in delivery_tasks))
        self.assertTrue(session.receive_task.done())

    async def test_old_receipt_uses_its_delivery_owner_and_preserves_new_input(self):
        session, socket = self.session()
        request = session.current_request
        coordinator = session.coordinator
        session.pending_audio = (7, 100)
        outcome = SimpleNamespace(
            ok=True,
            response_terminal=True,
            response_state="completed",
            delivery_status="completed",
            full_text="old reply",
            status="accepted",
        )
        old_channel = SimpleNamespace(acknowledge=Mock(return_value=outcome))
        new_channel = self.channel(asyncio.Event().wait)
        session.delivery_channels = {"old-turn": old_channel, "new-turn": new_channel}
        session.delivery_owners = {"old-delivery": "old-turn", "new-delivery": "new-turn"}
        session.control_owners = {"old-control": "old-turn", "new-control": "new-turn"}

        await session._handle_playback_ack(
            "client.playback.completed", {"delivery_id": "old-delivery", "voice_turn_id": "new-turn"}
        )

        self.assertEqual(socket.frames[-1]["type"], "server.response.completed")
        self.assertEqual(socket.frames[-1]["voice_turn_id"], "old-turn")
        self.assertIs(session.current_request, request)
        self.assertIs(session.coordinator, coordinator)
        self.assertEqual(session.pending_audio, (7, 100))
        self.assertIs(session.delivery_channels["new-turn"], new_channel)
        self.assertEqual(session.delivery_owners, {"new-delivery": "new-turn"})
        self.assertEqual(session.control_owners, {"new-control": "new-turn"})
        self.assertFalse(session.terminal)
        session.call.release_response.assert_called_once_with("old-turn")
        coordinator.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
