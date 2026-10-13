from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway


class QQEmotionDeliveryTests(unittest.TestCase):
    def context(self):
        return QQMessageContext(
            True, "private", target_id=41, user_id=41,
            session_id="qq_pri_41", profile_user_id="qq:41", character_pack_id="reimu",
        )

    def send(self, gateway, kind, emotion, *, interval=20):
        if kind == "image":
            return gateway.send_emotion_image(
                self.context(), {"emotion": emotion},
                image={"path": f"{emotion}.png"}, min_interval_seconds=interval,
            )
        return gateway.send_emotion_mface(
            self.context(), {"emotion": emotion}, qq_delivery_config={
                "enabled": True, "min_interval_seconds": interval,
                "map": {
                    emotion: {"emoji_package_id": 1, "emoji_id": emotion, "key": "fixture"},
                },
            },
        )

    def test_slower_older_delivery_does_not_replace_newer_dedupe_state(self):
        for kind in ("image", "mface"):
            with self.subTest(kind=kind):
                gateway = NapCatQQGateway()
                started = threading.Event()
                release = threading.Event()
                results = []
                errors = []

                def outbound(_context, **_kwargs):
                    if threading.current_thread().name == "older-emotion-delivery":
                        started.set()
                        if not release.wait(3):
                            raise TimeoutError("test_delivery_release_timeout")
                    return {"ok": True}

                def older():
                    try:
                        results.append(self.send(gateway, kind, "older"))
                    except BaseException as exc:
                        errors.append(exc)

                thread = threading.Thread(target=older, name="older-emotion-delivery")
                with patch.object(gateway, "send_image" if kind == "image" else "send_mface", side_effect=outbound) as transport:
                    try:
                        thread.start()
                        self.assertTrue(started.wait(2))
                        self.assertEqual(self.send(gateway, kind, "newer")["status"], "sent")
                    finally:
                        release.set()
                        thread.join(3)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(results[0]["status"], "sent")
                    repeated = self.send(gateway, kind, "newer")
                    self.assertEqual(repeated.get("reason"), "dedupe_interval")
                    self.assertEqual(transport.call_count, 2)

    def test_inflight_repeat_is_skipped_and_failed_delivery_can_retry(self):
        for kind in ("image", "mface"):
            with self.subTest(kind=kind):
                gateway = NapCatQQGateway()
                started = threading.Event()
                release = threading.Event()
                results = []
                errors = []

                def outbound(_context, **_kwargs):
                    if threading.current_thread().name == "failing-emotion-delivery":
                        started.set()
                        if not release.wait(3):
                            raise TimeoutError("test_delivery_release_timeout")
                        return {"ok": False, "reason": "transport_unavailable"}
                    return {"ok": True}

                def first():
                    try:
                        results.append(self.send(gateway, kind, "happy"))
                    except BaseException as exc:
                        errors.append(exc)

                thread = threading.Thread(target=first, name="failing-emotion-delivery")
                with patch.object(gateway, "send_image" if kind == "image" else "send_mface", side_effect=outbound) as transport:
                    try:
                        thread.start()
                        self.assertTrue(started.wait(2))
                        self.assertEqual(self.send(gateway, kind, "happy").get("reason"), "delivery_in_progress")
                    finally:
                        release.set()
                        thread.join(3)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(results[0]["status"], "send_failed")
                    self.assertEqual(self.send(gateway, kind, "happy")["status"], "sent")
                    self.assertEqual(transport.call_count, 2)

    def test_cooldown_starts_when_slow_delivery_succeeds(self):
        for kind in ("image", "mface"):
            with self.subTest(kind=kind):
                gateway = NapCatQQGateway()
                now = [1000.0]

                def outbound(_context, **_kwargs):
                    now[0] += 30
                    return {"ok": True}

                with (
                    patch("companion_v01.channels.qq.delivery.emotions.time.time", side_effect=lambda: now[0]),
                    patch.object(gateway, "send_image" if kind == "image" else "send_mface", side_effect=outbound) as transport,
                ):
                    self.assertEqual(self.send(gateway, kind, "happy")["status"], "sent")
                    self.assertEqual(self.send(gateway, kind, "happy").get("reason"), "dedupe_interval")
                    self.assertEqual(transport.call_count, 1)

    def test_exception_releases_inflight_claim_and_zero_interval_allows_repeats(self):
        for kind in ("image", "mface"):
            with self.subTest(kind=kind):
                gateway = NapCatQQGateway()
                with patch.object(gateway, "send_image" if kind == "image" else "send_mface", side_effect=RuntimeError("fixture")):
                    with self.assertRaises(RuntimeError):
                        self.send(gateway, kind, "happy")
                with patch.object(gateway, "send_image" if kind == "image" else "send_mface", return_value={"ok": True}) as transport:
                    self.assertEqual(self.send(gateway, kind, "happy")["status"], "sent")
                    self.assertEqual(self.send(gateway, kind, "happy", interval=0)["status"], "sent")
                    self.assertEqual(transport.call_count, 2)

    def test_image_result_preserves_resource_emotion_and_name_on_success_and_failure(self):
        context = self.context()
        for ok in (True, False):
            with self.subTest(ok=ok):
                gateway = NapCatQQGateway()
                image = {"path": "fallback.png", "emotion": "neutral"}
                with patch.object(gateway, "send_image", return_value={"ok": ok, "transport": "file_uri"}) as transport:
                    result = gateway.send_emotion_image(context, {"emotion": "happy"}, image=image)
                    self.assertEqual(result["status"], "sent" if ok else "send_failed")
                    self.assertEqual(result["emotion"], "happy")
                    self.assertEqual(result["image_emotion"], "neutral")
                    self.assertEqual(result["transport"], "file_uri")
                    transport.assert_called_once_with(context, image_path="fallback.png", name="neutral")
                    if ok:
                        skipped = gateway.send_emotion_image(context, {"emotion": "happy"}, image=image)
                        self.assertEqual(skipped["reason"], "dedupe_interval")
                        self.assertNotIn("image_emotion", skipped)
                        transport.assert_called_once()


if __name__ == "__main__":
    unittest.main()
