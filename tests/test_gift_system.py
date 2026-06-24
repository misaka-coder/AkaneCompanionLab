from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.gift_system import GiftSystemService
from companion_v01.store import MemoryStore


class FakeLLM:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)

    def call_aux_json(self, **kwargs):  # noqa: ANN003
        return dict(self.payload)


class GiftSystemServiceTests(unittest.TestCase):
    def test_pending_prompt_context_limits_window_and_mentions_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            for index, name in enumerate(["alpha", "bravo", "charlie", "delta", "echo"], start=1):
                service.ingest_upload(
                    profile_user_id="user_a",
                    session_id="session_a",
                    filename=f"{name}.flac",
                    content_type="audio/flac",
                    content=b"stub-audio",
                    now_ts=100 + index,
                )

            context = service.build_pending_prompt_context(
                profile_user_id="user_a",
                session_id="session_a",
                limit=3,
            )

            self.assertIn("音乐: echo", context)
            self.assertIn("音乐: delta", context)
            self.assertIn("音乐: charlie", context)
            self.assertIn("还有 2 件较早的未处理礼物", context)
            self.assertNotIn("音乐: alpha", context)

    def test_internalized_audio_projects_into_runtime_bgm_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            service.apply_action(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                action="internalize",
                timestamp=120,
            )

            projection = service.build_runtime_projection(profile_user_id="user_a")

            self.assertEqual(len(projection["extra_bgm_tracks"]), 1)
            self.assertEqual(projection["extra_bgm_tracks"][0]["id"], asset["resource_id"])
            self.assertTrue(projection["extra_bgm_tracks"][0]["path"].startswith("/user-assets/"))
            refreshed = store.get_gift_asset(profile_user_id="user_a", asset_id=str(asset["asset_id"]))
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed["container_type"], "music_box")
            self.assertEqual(refreshed["container_key"], "main")
            self.assertEqual(refreshed["container_name"], "曲库")

    def test_upload_sets_focus_and_internalize_clears_focus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            session_after_upload = store.get_session("user_a", "session_a")
            service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="internalize",
                timestamp=120,
            )
            session_after_internalize = store.get_session("user_a", "session_a")

            self.assertIsNotNone(session_after_upload)
            self.assertEqual(session_after_upload["current_gift_focus_asset_id"], asset["asset_id"])
            self.assertIsNotNone(session_after_internalize)
            self.assertEqual(session_after_internalize["current_gift_focus_asset_id"], asset["asset_id"])

    def test_internalized_image_projects_into_runtime_scene_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="窗边黄昏.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            updated = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="internalize",
                timestamp=120,
            )
            projection = service.build_runtime_projection(profile_user_id="user_a")

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["payload"]["projection_role"], "scene")
            self.assertEqual(updated["container_type"], "album")
            self.assertEqual(updated["container_key"], "memories")
            self.assertEqual(updated["container_name"], "回忆")
            self.assertEqual(len(projection["extra_scene_groups"]), 1)
            major = projection["extra_scene_groups"][0]
            self.assertEqual(major["id"], "gift_gallery")
            self.assertEqual(major["minors"][0]["backgrounds"][0]["name"], "窗边黄昏")

    def test_kept_image_stays_in_album_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="星空.jpg",
                content_type="image/jpeg",
                content=b"stub-image",
                now_ts=100,
            )
            updated = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="keep",
                timestamp=110,
            )
            projection = service.build_runtime_projection(profile_user_id="user_a")

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["payload"]["projection_role"], "photo")
            self.assertEqual(updated["container_type"], "album")
            self.assertEqual(updated["container_key"], "memories")
            self.assertEqual(updated["container_name"], "回忆")
            self.assertEqual(projection["extra_scene_groups"], [])

    def test_image_observation_metadata_uses_fallback_naming(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="IMG_0001.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            updated = service.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                observation={
                    "observation": {
                        "summary": "黄昏窗边的安静房间。",
                        "entities": ["窗边", "房间"],
                        "mood_tags": ["黄昏", "安静"],
                        "uncertainty": [],
                    }
                },
                timestamp=120,
            )

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["display_name"], "IMG_0001")
            self.assertEqual(updated["payload"]["display_name_source"], "filename")
            self.assertEqual(updated["payload"]["seed_name"], "黄昏窗边的安静房间")
            self.assertEqual(updated["payload"]["seed_collection_key"], "room")
            self.assertEqual(updated["payload"]["seed_collection_name"], "房间")
            self.assertEqual(updated["payload"]["collection_key"], "memories")
            self.assertEqual(updated["payload"]["collection_name"], "回忆")
            self.assertEqual(updated["payload"]["vision_entities"], ["窗边", "房间"])
            self.assertEqual(updated["container_type"], "album")
            self.assertEqual(updated["container_key"], "memories")
            self.assertEqual(updated["container_name"], "回忆")

            kept = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="keep",
                timestamp=130,
            )
            self.assertIsNotNone(kept)
            assert kept is not None
            self.assertEqual(kept["display_name"], "黄昏窗边的安静房间")
            self.assertEqual(kept["payload"]["display_name_source"], "akane_confirmed")
            self.assertEqual(kept["payload"]["collection_key"], "room")
            self.assertEqual(kept["payload"]["collection_name"], "房间")
            self.assertEqual(kept["payload"]["collection_source"], "akane_confirmed")
            self.assertEqual(kept["container_key"], "room")
            self.assertEqual(kept["container_name"], "房间")

    def test_image_observation_metadata_can_use_llm_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(
                root / "gifts",
                store=store,
                llm=FakeLLM(
                    {
                        "display_name": "下雨前的天色",
                        "collection_key": "rain",
                        "collection_name": "雨天",
                    }
                ),
            )

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="IMG_0002.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            updated = service.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                observation={
                    "observation": {
                        "summary": "窗外的天空有下雨前的压低云层。",
                        "entities": ["天空", "云层"],
                        "mood_tags": ["雨前", "安静"],
                        "uncertainty": [],
                    }
                },
                timestamp=120,
            )

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["display_name"], "IMG_0002")
            self.assertEqual(updated["payload"]["seed_name"], "下雨前的天色")
            self.assertEqual(updated["payload"]["seed_collection_key"], "rain")
            self.assertEqual(updated["payload"]["seed_collection_name"], "雨天")

            internalized = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="internalize",
                timestamp=130,
            )
            self.assertIsNotNone(internalized)
            assert internalized is not None
            self.assertEqual(internalized["display_name"], "下雨前的天色")
            self.assertEqual(internalized["payload"]["collection_key"], "rain")
            self.assertEqual(internalized["payload"]["collection_name"], "雨天")

    def test_image_observation_metadata_prefers_existing_collection_when_llm_matches_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            existing_asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="window.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            service.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(existing_asset["asset_id"]),
                observation={
                    "observation": {
                        "summary": "黄昏窗边的安静房间。",
                        "entities": ["窗边", "房间"],
                        "mood_tags": ["黄昏", "安静"],
                        "uncertainty": [],
                    }
                },
                timestamp=110,
            )
            service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(existing_asset["asset_id"]),
                action="keep",
                timestamp=120,
            )

            service.llm = FakeLLM(
                {
                    "display_name": "靠窗的灯",
                    "collection_key": "new_room_bucket",
                    "collection_name": "房间",
                }
            )

            candidate_asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="lamp.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=130,
            )
            updated = service.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(candidate_asset["asset_id"]),
                observation={
                    "observation": {
                        "summary": "房间角落里的一盏暖灯。",
                        "entities": ["房间", "暖灯"],
                        "mood_tags": ["安静", "温暖"],
                        "uncertainty": [],
                    }
                },
                timestamp=140,
            )

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["payload"]["seed_collection_key"], "room")
            self.assertEqual(updated["payload"]["seed_collection_name"], "房间")

    def test_transient_image_reply_and_discard_removes_asset_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(
                root / "gifts",
                store=store,
                llm=FakeLLM({"assistant_line": "我看到了哦，这样的日常被主人分享给我，我会很开心。"}),
            )

            asset = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="daily.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            stored = store.get_gift_asset(profile_user_id="user_a", asset_id=str(asset["asset_id"]))
            self.assertIsNotNone(stored)
            assert stored is not None
            stored_path = root / "gifts" / str(stored["storage_relpath"])
            self.assertTrue(stored_path.exists())

            reply = service.build_transient_image_reply(
                asset=asset,
                observation={
                    "observation": {
                        "summary": "窗边有一只打盹的小猫。",
                        "entities": ["窗边", "小猫"],
                        "mood_tags": ["安静", "午后"],
                        "uncertainty": [],
                    }
                },
            )
            removed = service.discard_asset(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                timestamp=120,
            )

            self.assertIn("开心", reply)
            self.assertIsNotNone(removed)
            self.assertIsNone(store.get_gift_asset(profile_user_id="user_a", asset_id=str(asset["asset_id"])))
            self.assertFalse(stored_path.exists())

    def test_remove_and_purge_can_cleanup_owned_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)

            audio = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="alpha.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(audio["asset_id"]),
                action="internalize",
                timestamp=110,
            )
            removed = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(audio["asset_id"]),
                action="remove",
                timestamp=120,
            )

            self.assertIsNotNone(removed)
            assert removed is not None
            self.assertEqual(removed["status"], "rejected")

            image = service.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="daily.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=130,
            )
            stored = store.get_gift_asset(profile_user_id="user_a", asset_id=str(image["asset_id"]))
            self.assertIsNotNone(stored)
            assert stored is not None
            image_path = root / "gifts" / str(stored["storage_relpath"])
            self.assertTrue(image_path.exists())

            purged = service.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(image["asset_id"]),
                action="purge",
                timestamp=140,
            )

            self.assertIsNotNone(purged)
            self.assertIsNone(store.get_gift_asset(profile_user_id="user_a", asset_id=str(image["asset_id"])))
            self.assertFalse(image_path.exists())


if __name__ == "__main__":
    unittest.main()
