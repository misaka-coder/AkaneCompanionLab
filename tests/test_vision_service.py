from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from companion_v01.resource_manifest import ResourceManifest
from companion_v01.store import MemoryStore
from companion_v01.vision_service import VisionObservationService


def write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class VisionObservationServiceTests(unittest.TestCase):
    def _build_assets_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        assets = root / "assets"
        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_bytes(assets / "characters" / "default" / "normal.png")
        return temp_dir, root

    def test_scene_observation_prefers_override_file(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        override_path = root / "assets" / "scenes" / "school" / "classroom" / "evening.vision.json"
        override_path.write_text(
            json.dumps(
                {
                    "summary": "黄昏教室里有暖色余光。",
                    "entities": ["教室", "窗边", "课桌"],
                    "mood_tags": ["黄昏", "安静"],
                    "uncertainty": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        manifest = ResourceManifest(root / "assets")
        manifest.refresh()
        store = MemoryStore(root / "db")
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            resource_manifest=manifest,
        )

        observation = service.ensure_scene_observation(
            visual_payload={
                "emotion": "normal",
                "character": {"outfit": "default"},
                "scene": {
                    "major": "school",
                    "minor": "classroom",
                    "background": "evening",
                    "bgm": "",
                },
            }
        )

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation["provider"], "override")
        self.assertEqual(observation["observation"]["summary"], "黄昏教室里有暖色余光。")

    def test_scene_prompt_context_uses_cached_observation_card(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        manifest = ResourceManifest(root / "assets")
        manifest.refresh()
        store = MemoryStore(root / "db")
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            resource_manifest=manifest,
            analyze_image_fn=lambda target: {
                "summary": f"{target.title}，窗边带着安静的晚光。",
                "entities": ["教室", "窗", "桌椅"],
                "mood_tags": ["黄昏", "安静"],
                "uncertainty": [],
            },
        )
        payload = {
            "emotion": "normal",
            "character": {"outfit": "default"},
            "scene": {
                "major": "school",
                "minor": "classroom",
                "background": "evening",
                "bgm": "",
            },
        }

        service.ensure_scene_observation(visual_payload=payload)
        prompt_context = service.build_scene_prompt_context(visual_payload=payload)

        self.assertIn("当前场景沉浸观察：", prompt_context)
        self.assertIn("mood_tags: 黄昏, 安静", prompt_context)
        self.assertIn("entities: 教室, 窗, 桌椅", prompt_context)

    def test_outfit_prompt_context_uses_cached_observation_card(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        manifest = ResourceManifest(root / "assets")
        manifest.refresh()
        store = MemoryStore(root / "db")
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            resource_manifest=manifest,
            analyze_image_fn=lambda target: {
                "summary": f"{target.title} 看起来偏轻松柔和。",
                "entities": ["针织外套", "浅色内搭", "长发"],
                "appearance_traits": ["橙金长发", "红色眼睛", "白上衣", "黑色蝴蝶结"],
                "mood_tags": ["居家", "温柔", "放松"],
                "uncertainty": [],
            },
        )
        payload = {
            "emotion": "normal",
            "character": {"outfit": "default"},
            "scene": {
                "major": "school",
                "minor": "classroom",
                "background": "evening",
                "bgm": "",
            },
        }

        observation = service.ensure_outfit_observation(visual_payload=payload)
        prompt_context = service.build_outfit_prompt_context(visual_payload=payload)

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation["observation"]["type"], "outfit_observation")
        self.assertIn("当前服装体感观察：", prompt_context)
        self.assertIn("summary: 默认 看起来偏轻松柔和。", prompt_context)
        self.assertIn("appearance_traits: 橙金长发, 红色眼睛, 白上衣, 黑色蝴蝶结", prompt_context)
        self.assertIn("mood_tags: 居家, 温柔, 放松", prompt_context)

    def test_gift_prompt_context_reads_from_user_asset_directory(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        user_assets = root / "user_assets"
        write_bytes(user_assets / "user_a" / "images" / "gift_1.png")
        store = MemoryStore(root / "db")
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            gift_assets_dir=user_assets,
            analyze_image_fn=lambda target: {
                "summary": "一张偏安静的夜空照片。",
                "entities": ["夜空", "星点"],
                "mood_tags": ["夜晚", "安静"],
                "uncertainty": [],
            },
        )
        asset = {
            "asset_id": "gift_1",
            "asset_type": "image",
            "storage_relpath": "user_a/images/gift_1.png",
            "asset_url": "/user-assets/user_a/images/gift_1.png",
            "display_name": "星空照片",
            "payload": {"user_caption": "那天晚上拍给她看的"},
        }

        service.ensure_gift_observation(asset=asset)
        prompt_context = service.build_gift_prompt_context(asset=asset)

        self.assertIn("当前礼物视觉观察：", prompt_context)
        self.assertIn("summary: 一张偏安静的夜空照片。", prompt_context)

    def test_gift_observation_ready_hook_receives_target_owner(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        user_assets = root / "user_assets"
        write_bytes(user_assets / "user_a" / "images" / "gift_2.png")
        store = MemoryStore(root / "db")
        captured: dict[str, str] = {}
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            gift_assets_dir=user_assets,
            analyze_image_fn=lambda target: {
                "summary": "窗边的晚霞照片。",
                "entities": ["窗边", "晚霞"],
                "mood_tags": ["黄昏", "安静"],
                "uncertainty": [],
            },
            on_observation_ready=lambda target, observation: captured.update(
                {
                    "asset_id": str(target.target_id),
                    "profile_user_id": str(target.owner_profile_user_id),
                    "summary": str((observation.get("observation") or {}).get("summary") or ""),
                }
            ),
        )
        asset = {
            "asset_id": "gift_2",
            "profile_user_id": "user_a",
            "asset_type": "image",
            "storage_relpath": "user_a/images/gift_2.png",
            "asset_url": "/user-assets/user_a/images/gift_2.png",
            "display_name": "晚霞",
            "payload": {},
        }

        service.ensure_gift_observation(asset=asset)

        self.assertEqual(captured["asset_id"], "gift_2")
        self.assertEqual(captured["profile_user_id"], "user_a")
        self.assertEqual(captured["summary"], "窗边的晚霞照片。")

    def test_schedule_scene_observation_retries_stale_pending_record(self) -> None:
        temp_dir, root = self._build_assets_root()
        self.addCleanup(temp_dir.cleanup)

        manifest = ResourceManifest(root / "assets")
        manifest.refresh()
        store = MemoryStore(root / "db")
        service = VisionObservationService(
            root / "vision_cache",
            store=store,
            resource_manifest=manifest,
            analyze_image_fn=lambda target: {
                "summary": "客厅里有柔和的暖光。",
                "entities": ["窗边", "沙发"],
                "mood_tags": ["温暖", "安静"],
                "uncertainty": [],
            },
        )
        payload = {
            "emotion": "normal",
            "character": {"outfit": "default"},
            "scene": {
                "major": "school",
                "minor": "classroom",
                "background": "evening",
                "bgm": "",
            },
        }
        target = service._resolve_scene_target(visual_payload=payload)
        self.assertIsNotNone(target)
        assert target is not None
        store.upsert_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            target_id=target.target_id,
            source_path=str(target.source_path),
            public_path=target.public_path,
            prompt_version=target.prompt_version,
            provider="openai_compat",
            model_name="dummy",
            status="pending",
            summary="",
            observation={},
            timestamp=1,
        )

        service.schedule_scene_observation(visual_payload=payload)
        for _ in range(40):
            observation = store.get_vision_observation(
                observation_type=target.observation_type,
                resource_fingerprint=target.resource_fingerprint,
                prompt_version=target.prompt_version,
            )
            if observation and observation.get("status") == "ready":
                break
            time.sleep(0.05)

        assert observation is not None
        self.assertEqual(observation["status"], "ready")
        self.assertEqual(observation["observation"]["summary"], "客厅里有柔和的暖光。")


if __name__ == "__main__":
    unittest.main()
