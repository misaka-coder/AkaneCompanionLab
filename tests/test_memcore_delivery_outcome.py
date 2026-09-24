from types import SimpleNamespace
import unittest

from companion_v01.routes.qq import _process_qq_turn_streaming
from tests.test_qq_voice_delivery import FakeQQGateway


class MemcoreDeliveryOutcomeTests(unittest.TestCase):
    def deliver(self, frame, gateway=None, *, stream=True):
        class Engine:
            def process_turn_stream(self, _payload):
                if stream:
                    yield {"type": "final_ui", "payload": frame}

            def process_turn(self, _payload):
                return frame
        return _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway or FakeQQGateway(),
            context=SimpleNamespace(session_id="private", profile_user_id="owner", character_pack_id="", reply_mode="text"),
            turn_payload={"message": "test"}, config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True))

    def test_failure_notice_is_not_successful_model_delivery_in_either_mode(self):
        for stream in (True, False):
            result = self.deliver({"speech": "host fallback", "_transient_final_failure": True}, stream=stream)
            self.assertTrue(result["final_failure_notice_result"]["ok"])
            self.assertFalse(result["send_result"]["ok"])
            self.assertTrue(result["send_result"]["failure_notice_sent"])
            self.assertTrue(result["send_result"]["transport_ok"])
            self.assertEqual(result["send_result"]["reason"], "model_turn_incomplete")
            self.assertEqual(result["model_status"], "failed")

    def test_failed_notice_keeps_failure_visible(self):
        class FailedGateway(FakeQQGateway):
            def send_reply(self, _context, _message):
                return {"ok": False, "reason": "transport_unavailable"}
        result = self.deliver({"_transient_final_failure": True}, FailedGateway())
        self.assertFalse(result["send_result"]["ok"])
        self.assertFalse(result["send_result"]["failure_notice_sent"])
        self.assertFalse(result["send_result"]["transport_ok"])
        self.assertEqual(result["final_failure_notice_result"]["reason"], "transport_unavailable")

    def test_persistence_failure_preserves_real_reply_but_not_overall_success(self):
        gateway = FakeQQGateway()
        result = self.deliver({"speech": "Real model response", "_memcore_failure": {
            "status": "failed", "reason": "input_turn_completion_failed"}}, gateway)
        self.assertEqual(gateway.text_sends, [["Real model response"]])
        self.assertFalse(result["send_result"]["ok"])
        self.assertTrue(result["send_result"]["transport_ok"])
        self.assertTrue(result["send_result"]["partial_delivery"])
        self.assertEqual(result["send_result"]["reason"], "reply_persistence_failed")
        self.assertEqual(result["model_status"], "completed")

    def test_completed_reply_remains_successful(self):
        result = self.deliver({"speech": "Real response"})
        self.assertTrue(result["send_result"]["ok"])
        self.assertEqual(result["model_status"], "completed")
        self.assertNotIn("failure_notice_sent", result["send_result"])

    def test_file_notices_report_actual_transport_and_do_not_claim_user_was_notified(self):
        for file_status in ("failed", "partial", "sent"):
            for notice_ok in (False, True):
                with self.subTest(file_status=file_status, notice_ok=notice_ok):
                    class Gateway(FakeQQGateway):
                        def __init__(self):
                            super().__init__()
                            self.notes = []

                        def send_generated_files(self, context, tool_events):
                            return {"ok": file_status == "sent", "status": file_status, "count": 1,
                                    "results": [{"ok": file_status == "sent"}]}

                        def send_reply(self, context, message):
                            return {"ok": notice_ok, "reason": "" if notice_ok else "transport_unavailable"}

                        def add_delivery_note(self, session_id, note):
                            self.notes.append(note)

                    gateway = Gateway()
                    result = self.deliver({"speech": ""}, gateway)
                    key = "final_reply_fallback_result" if file_status == "sent" else "file_delivery_feedback_result"
                    prefix = {"sent": "generated_result_notice", "partial": "partial_notice", "failed": "failure_notice"}[file_status]
                    self.assertEqual(result[key]["status"], prefix + ("_sent" if notice_ok else "_failed"))
                    self.assertEqual(result[key]["ok"], notice_ok)
                    if not notice_ok:
                        self.assertEqual(result[key]["reason"], "transport_unavailable")
                        self.assertNotIn("用户已收到通知", "".join(gateway.notes))
                    if file_status == "failed":
                        self.assertIn("失败提示已发送" if notice_ok else "失败提示也未发送成功", "".join(gateway.notes))


if __name__ == "__main__":
    unittest.main()
