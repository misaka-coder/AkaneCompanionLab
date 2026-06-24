from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.store import MemoryStore
from companion_v01.vision_observation_router import VisionObservationRouter
from companion_v01.vision_service import VisionTarget


class FakeGiftService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def apply_image_observation_metadata(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"ok": True}


class VisionObservationRouterTests(unittest.TestCase):
    def test_attachment_image_ready_updates_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            inbox = AttachmentInboxService(store=store)
            item = inbox.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="pic.png",
                timestamp=100,
            )
            router = VisionObservationRouter(store=store, gift_service=FakeGiftService())
            router.handle(
                self._target(
                    observation_type="attachment_image",
                    target_id=str(item["attachment_id"]),
                    owner_profile_user_id="user",
                    owner_session_id="session",
                ),
                {
                    "status": "ready",
                    "observation": {
                        "summary_title": "白猫窗边",
                        "summary": "一只白猫趴在窗边。",
                        "entities": ["白猫"],
                    },
                },
            )

            updated = store.get_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=str(item["attachment_id"]),
            )
            assert updated is not None
            self.assertEqual(updated["status"], "ready")
            self.assertEqual(updated["summary_title"], "白猫窗边")
            self.assertIn("白猫", updated["short_hint"])

    def test_attachment_image_error_marks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            inbox = AttachmentInboxService(store=store)
            item = inbox.create_pending(
                profile_user_id="user",
                session_id="session",
                source="qq",
                kind="image",
                origin_name="pic.png",
                timestamp=100,
            )
            router = VisionObservationRouter(store=store, gift_service=FakeGiftService())
            router.handle(
                self._target(
                    observation_type="attachment_image",
                    target_id=str(item["attachment_id"]),
                    owner_profile_user_id="user",
                    owner_session_id="session",
                ),
                {"status": "error", "error_message": "bad image"},
            )

            updated = store.get_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                attachment_id=str(item["attachment_id"]),
            )
            assert updated is not None
            self.assertEqual(updated["status"], "failed")
            self.assertIn("bad image", updated["error_message"])

    def test_gift_ready_delegates_to_gift_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            gift_service = FakeGiftService()
            router = VisionObservationRouter(store=store, gift_service=gift_service)
            router.handle(
                self._target(
                    observation_type="gift",
                    target_id="gift_001",
                    owner_profile_user_id="user",
                    owner_session_id="session",
                ),
                {
                    "status": "ready",
                    "observation": {"summary": "一张温暖的照片。"},
                },
            )

            self.assertEqual(len(gift_service.calls), 1)
            self.assertEqual(gift_service.calls[0]["profile_user_id"], "user")
            self.assertEqual(gift_service.calls[0]["asset_id"], "gift_001")

    def _target(
        self,
        *,
        observation_type: str,
        target_id: str,
        owner_profile_user_id: str,
        owner_session_id: str,
    ) -> VisionTarget:
        return VisionTarget(
            observation_type=observation_type,
            target_id=target_id,
            source_path=Path("dummy.png"),
            public_path="",
            resource_fingerprint=f"{observation_type}:{target_id}",
            prompt_version="test",
            title="测试图片",
            hint_text="",
            owner_profile_user_id=owner_profile_user_id,
            owner_session_id=owner_session_id,
        )


if __name__ == "__main__":
    unittest.main()
