from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.artifact_system import ArtifactContainerService
from companion_v01.gift_system import GiftSystemService
from companion_v01.store import MemoryStore


class ArtifactContainerServiceTests(unittest.TestCase):
    def test_container_overview_only_counts_owned_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            pending_audio = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="alpha.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            kept_image = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="window.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=110,
            )
            gifts.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(kept_image["asset_id"]),
                action="keep",
                timestamp=120,
            )
            gifts.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(pending_audio["asset_id"]),
                action="internalize",
                timestamp=130,
            )

            containers = artifacts.list_containers(
                profile_user_id="user_a",
                preview_limit=3,
                include_empty=True,
            )

            by_type = {item["container_type"]: item for item in containers}
            self.assertEqual(by_type["music_box"]["total_count"], 1)
            self.assertEqual(by_type["album"]["total_count"], 1)
            self.assertEqual(by_type["note_box"]["total_count"], 0)
            self.assertEqual(by_type["keepsake_box"]["total_count"], 0)
            self.assertEqual(by_type["music_box"]["latest_items"][0]["display_name"], "alpha")
            self.assertEqual(by_type["album"]["latest_items"][0]["display_name"], "window")

    def test_list_container_items_can_filter_by_collection_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            asset = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="IMG_0001.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            gifts.apply_image_observation_metadata(
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
                timestamp=110,
            )
            gifts.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="keep",
                timestamp=120,
            )

            payload = artifacts.list_container_items(
                profile_user_id="user_a",
                container_type="album",
                container_key="room",
                limit=10,
            )

            self.assertEqual(payload["container_type"], "album")
            self.assertEqual(payload["container_key"], "room")
            self.assertEqual(payload["total_count"], 1)
            self.assertEqual(payload["items"][0]["container_name"], "房间")
            self.assertEqual(payload["collections"][0]["container_key"], "room")
            self.assertEqual(payload["collections"][0]["container_name"], "房间")

    def test_album_detail_includes_collection_overview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            first = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="night.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            second = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="room.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=110,
            )
            gifts.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(first["asset_id"]),
                observation={
                    "observation": {
                        "summary": "深夜窗边的街灯。",
                        "entities": ["街灯", "窗边"],
                        "mood_tags": ["夜晚", "安静"],
                        "uncertainty": [],
                    }
                },
                timestamp=120,
            )
            gifts.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(second["asset_id"]),
                observation={
                    "observation": {
                        "summary": "黄昏房间里的暖灯。",
                        "entities": ["房间", "暖灯"],
                        "mood_tags": ["黄昏", "温暖"],
                        "uncertainty": [],
                    }
                },
                timestamp=130,
            )
            gifts.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(first["asset_id"]),
                action="keep",
                timestamp=140,
            )
            gifts.apply_action(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(second["asset_id"]),
                action="keep",
                timestamp=150,
            )

            payload = artifacts.list_container_items(
                profile_user_id="user_a",
                container_type="album",
                limit=20,
            )

            by_key = {item["container_key"]: item for item in payload["collections"]}
            self.assertIn("night", by_key)
            self.assertIn("room", by_key)
            self.assertEqual(by_key["night"]["container_name"], "夜色")
            self.assertEqual(by_key["room"]["container_name"], "房间")
            self.assertEqual(by_key["night"]["total_count"], 1)
            self.assertEqual(by_key["room"]["total_count"], 1)

    def test_manage_artifact_claims_pending_image_as_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            asset = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="room.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            gifts.apply_image_observation_metadata(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                observation={
                    "observation": {
                        "summary": "黄昏房间里的暖灯。",
                        "entities": ["房间", "暖灯"],
                        "mood_tags": ["黄昏", "温暖"],
                        "uncertainty": [],
                    }
                },
                timestamp=110,
            )

            claimed = artifacts.manage_artifact(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="claim",
                display_name="暖灯房间",
                collection_key="room",
                collection_name="房间",
                asset_role="scene",
                timestamp=120,
                source_id="msg::claim_scene",
            )

            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed["display_name"], "暖灯房间")
            self.assertEqual(claimed["status"], "internalized")
            self.assertEqual(claimed["payload"]["collection_key"], "room")
            self.assertEqual(claimed["payload"]["collection_name"], "房间")
            self.assertEqual(claimed["payload"]["asset_role"], "scene")
            self.assertEqual(claimed["payload"]["projection_role"], "scene")
            self.assertEqual(claimed["artifact_flags"]["world_asset_state"], "claimed")
            self.assertEqual(claimed["container_key"], "room")
            self.assertEqual(claimed["source_ids"], ["msg::claim_scene"])

    def test_manage_artifact_claims_expression_as_runtime_character_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            asset = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="akane_no_ribbon.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )

            claimed = artifacts.manage_artifact(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="claim",
                display_name="quiet",
                collection_key="sailor_uniform",
                collection_name="水手服",
                asset_role="expression",
                timestamp=120,
                source_id="msg::claim_expression",
            )
            projection = gifts.build_runtime_projection(profile_user_id="user_a")

            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed["display_name"], "quiet")
            self.assertEqual(claimed["status"], "internalized")
            self.assertEqual(claimed["payload"]["projection_role"], "character")
            self.assertEqual(claimed["payload"]["character_outfit_id"], "sailor_uniform")
            self.assertEqual(claimed["payload"]["character_outfit_name"], "水手服")
            self.assertEqual(claimed["payload"]["character_emotion_id"], "quiet")
            self.assertEqual(claimed["payload"]["character_emotion_name"], "quiet")
            self.assertEqual(len(projection["extra_character_outfits"]), 1)
            outfit = projection["extra_character_outfits"][0]
            self.assertEqual(outfit["id"], "sailor_uniform")
            self.assertEqual(outfit["name"], "水手服")
            self.assertEqual(outfit["emotions"][0]["id"], "quiet")
            self.assertTrue(outfit["emotions"][0]["path"].startswith("/user-assets/"))

    def test_manage_artifact_can_rename_move_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gifts = GiftSystemService(root / "gifts", store=store)
            artifacts = ArtifactContainerService(store=store)

            asset = gifts.ingest_upload(
                profile_user_id="user_a",
                session_id="session_a",
                filename="shy.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )
            claimed = artifacts.manage_artifact(
                profile_user_id="user_a",
                session_id="session_a",
                asset_id=str(asset["asset_id"]),
                action="claim",
                display_name="害羞水手服",
                collection_key="daily_wardrobe",
                collection_name="常服衣柜",
                asset_role="outfit",
                timestamp=110,
            )
            self.assertIsNotNone(claimed)

            renamed = artifacts.manage_artifact(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                action="rename",
                display_name="shy 水手服",
                timestamp=120,
            )
            self.assertIsNotNone(renamed)
            assert renamed is not None
            self.assertEqual(renamed["display_name"], "shy 水手服")
            self.assertEqual(renamed["payload"]["display_name_source"], "akane_confirmed")

            moved = artifacts.manage_artifact(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                action="move",
                collection_key="spring_memory",
                collection_name="春季回忆",
                timestamp=130,
            )
            self.assertIsNotNone(moved)
            assert moved is not None
            self.assertEqual(moved["payload"]["collection_key"], "spring_memory")
            self.assertEqual(moved["container_key"], "spring_memory")

            deleted = artifacts.manage_artifact(
                profile_user_id="user_a",
                asset_id=str(asset["asset_id"]),
                action="delete",
                timestamp=140,
            )
            self.assertIsNotNone(deleted)
            assert deleted is not None
            self.assertEqual(deleted["status"], "rejected")
            self.assertEqual(deleted["artifact_flags"]["world_asset_state"], "deleted")


if __name__ == "__main__":
    unittest.main()
