import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from companion_v01.local_music_control import LocalMusicControlService
from companion_v01.tool_handlers.local_music import LocalMusicControlToolHandler


class LocalMusicControlTests(unittest.TestCase):
    def make(self, *, timeout=.15):
        received = threading.Event()
        frames = []
        async def deliver(frame, **kwargs):
            frames.append(frame)
            received.set()
            return {"ok": True, "status": "queued"}
        service = LocalMusicControlService(SimpleNamespace(deliver_agent_frame=deliver), "bot", timeout_seconds=timeout)
        context = SimpleNamespace(client_mode="desktop_pet", profile_user_id="master", session_id="desktop",
                                  character_pack_id="cecilia", invocation_id="one", cancel_requested=None)
        return service, context, received, frames

    def test_queue_ack_is_not_success_and_late_receipt_cannot_execute(self):
        service, context, received, frames = self.make(timeout=.02)
        result = service.execute({"action": "next"}, context)
        self.assertEqual(result["status"], "execution_unknown")
        command = frames[0]["_desktop_music_control"]
        self.assertFalse(service.acknowledge({"operation_id": command["operation_id"], "phase": "claim"})["ok"])

    def test_real_receipt_deduplicates_retries_and_refuses_unclaimed_results(self):
        service, context, received, frames = self.make(timeout=2)
        with ThreadPoolExecutor(2) as pool:
            job = pool.submit(service.execute, {"action": "next"}, context)
            self.assertTrue(received.wait(1))
            receipt = {"operation_id": frames[0]["_desktop_music_control"]["operation_id"]}
            self.assertFalse(service.acknowledge({**receipt, "ok": True})["ok"])
            self.assertTrue(service.acknowledge({**receipt, "phase": "claim"})["ok"])
            self.assertFalse(service.acknowledge({**receipt, "phase": "claim"})["ok"])
            self.assertFalse(job.done())
            self.assertTrue(service.acknowledge({**receipt, "ok": False, "reason": "no_next_track"})["ok"])
            result = job.result(1)
            self.assertEqual(result["reason"], "no_next_track")
        self.assertEqual(service.execute({"action": "next"}, context), result)
        self.assertEqual(len(frames), 1)

    def test_qq_and_revoked_request_cannot_control_local_queue(self):
        service, context, received, frames = self.make()
        context.client_mode = "qq_text"
        self.assertEqual(service.execute({"action": "play"}, context)["reason"], "desktop_client_required")
        self.assertFalse(frames)
        context.client_mode = "desktop_pet"
        context.cancel_requested = lambda: True
        with ThreadPoolExecutor(1) as pool:
            job = pool.submit(service.execute, {"action": "play"}, context)
            self.assertTrue(received.wait(1))
            receipt = {"operation_id": frames[0]["_desktop_music_control"]["operation_id"], "phase": "claim"}
            self.assertEqual(service.acknowledge(receipt)["reason"], "desktop_music_request_revoked")
            self.assertFalse(job.result(1)["ok"])

    def test_tool_has_desktop_scope_and_validates_actual_actions(self):
        handler = LocalMusicControlToolHandler(lambda: None)
        self.assertEqual(handler.tool_spec().visible_in, ("desktop",))
        self.assertIsNone(handler.normalize_call({"type": handler.tool_type, "action": "delete"}))
        self.assertIsNone(handler.normalize_call({"type": handler.tool_type, "action": "play", "path": "file"}))
        self.assertEqual(handler.normalize_call({"type": handler.tool_type, "action": "play", "source_id": "audio_001"})["source_id"], "audio_001")
