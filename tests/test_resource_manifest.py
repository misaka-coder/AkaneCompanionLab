from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from companion_v01.resource_manifest import ResourceManifest


def write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_meta(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ResourceManifestTests(unittest.TestCase):
    def make_assets_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        return temp_dir, Path(temp_dir.name) / "assets"

    def test_nested_scene_tree_respects_meta_defaults(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_meta(
            assets / "scenes" / "school" / "meta.json",
            {
                "id": "school",
                "name": "学校",
                "default_minor": "classroom",
            },
        )
        write_meta(
            assets / "scenes" / "school" / "classroom" / "meta.json",
            {
                "id": "classroom",
                "name": "教室",
                "default_background": "evening",
                "default_bgm": "evening",
            },
        )
        write_bytes(assets / "scenes" / "school" / "classroom" / "morning.png")
        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_meta(
            assets / "characters" / "校服" / "meta.json",
            {
                "id": "校服",
                "allowed_emotions": ["normal", "smug"],
                "default_emotion": "smug",
            },
        )
        write_bytes(assets / "characters" / "校服" / "normal.png")
        write_bytes(assets / "characters" / "校服" / "sumg.png")
        write_bytes(assets / "characters" / "校服" / "cry.png")
        write_bytes(assets / "bgm" / "school" / "classroom" / "morning.ogg")
        write_bytes(assets / "bgm" / "school" / "classroom" / "evening.ogg")

        manifest = ResourceManifest(assets).refresh()

        major = manifest["scenes"]["majors"][0]
        minor = major["minors"][0]
        outfit = manifest["characters"]["outfits"][0]

        self.assertEqual(major["id"], "school")
        self.assertEqual(minor["id"], "classroom")
        self.assertEqual([item["id"] for item in minor["backgrounds"]], ["evening", "morning"])
        self.assertEqual([item["id"] for item in minor["bgm_tracks"]], ["evening", "morning"])
        self.assertEqual(outfit["allowed_emotions"], ["smug", "normal"])
        self.assertEqual([item["id"] for item in outfit["emotions"]], ["smug", "normal"])
        self.assertEqual(manifest["defaults"]["emotion"], "smug")

    def test_legacy_scene_files_group_into_minors(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "home" / "卧室_白天.png")
        write_bytes(assets / "scenes" / "home" / "卧室_夜晚.png")
        write_bytes(assets / "scenes" / "home" / "客厅_黄昏.png")
        write_bytes(assets / "bgm" / "default" / "default" / "evening.ogg")

        manifest = ResourceManifest(assets).refresh()

        home = next(item for item in manifest["scenes"]["majors"] if item["id"] == "home")
        bedroom = next(item for item in home["minors"] if item["id"] == "卧室")
        living_room = next(item for item in home["minors"] if item["id"] == "客厅")

        self.assertEqual([item["id"] for item in bedroom["backgrounds"]], ["morning", "night"])
        self.assertEqual([item["id"] for item in living_room["backgrounds"]], ["evening"])
        self.assertEqual([item["id"] for item in bedroom["bgm_tracks"]], ["evening"])

    def test_compact_scene_file_names_remain_true_background_ids(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "家" / "夜晚卧室.png")
        write_bytes(assets / "scenes" / "家" / "白天卧室.png")
        write_bytes(assets / "scenes" / "家" / "白天客厅.png")
        write_bytes(assets / "scenes" / "街道" / "黄昏街道.png")
        write_bytes(assets / "bgm" / "default" / "default" / "evening.ogg")
        write_bytes(assets / "characters" / "猫娘" / "正常.png")

        manifest = ResourceManifest(assets)
        payload = manifest.refresh()

        home = next(item for item in payload["scenes"]["majors"] if item["id"] == "家")
        default_room = next(item for item in home["minors"] if item["id"] == "default")
        street = next(item for item in payload["scenes"]["majors"] if item["id"] == "街道")

        self.assertEqual(
            {item["id"] for item in default_room["backgrounds"]},
            {"夜晚卧室", "白天卧室", "白天客厅"},
        )
        self.assertEqual(street["minors"][0]["id"], "default")
        self.assertEqual(street["minors"][0]["backgrounds"][0]["id"], "黄昏街道")

        exact_background = manifest.normalize_visual_output(
            {
                "emotion": "normal",
                "character": {"outfit": "猫娘"},
                "scene": {"major": "家", "minor": "default", "background": "夜晚卧室"},
            }
        )
        exact_background_with_wrong_minor = manifest.normalize_visual_output(
            {
                "emotion": "normal",
                "character": {"outfit": "猫娘"},
                "scene": {"major": "家", "minor": "卧室", "background": "夜晚卧室"},
            }
        )
        street_evening = manifest.normalize_visual_output(
            {
                "emotion": "normal",
                "character": {"outfit": "猫娘"},
                "scene": {"major": "街道", "minor": "default", "background": "黄昏街道"},
            }
        )
        prompt_context = manifest.build_prompt_context()

        self.assertEqual(exact_background["scene"]["minor"], "default")
        self.assertEqual(exact_background["scene"]["background"], "夜晚卧室")
        self.assertEqual(exact_background_with_wrong_minor["scene"]["minor"], "default")
        self.assertEqual(exact_background_with_wrong_minor["scene"]["background"], "夜晚卧室")
        self.assertEqual(street_evening["scene"]["background"], "黄昏街道")
        self.assertIn("夜晚卧室", prompt_context)
        self.assertIn("不要把未列出的文件名自行拆成新场景", prompt_context)

    def test_flat_background_fallback_stays_available_alongside_scene_tree(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "backgrounds" / "evening.png")
        write_bytes(assets / "backgrounds" / "morning.png")
        write_bytes(assets / "scenes" / "home" / "卧室_白天.png")

        manifest = ResourceManifest(assets).refresh()

        major_ids = [item["id"] for item in manifest["scenes"]["majors"]]
        self.assertEqual(major_ids, ["default", "home"])
        self.assertEqual(manifest["defaults"]["major"], "default")
        self.assertEqual(manifest["defaults"]["background"], "evening")

    def test_normalize_visual_output_keeps_emotion_inside_outfit(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "school" / "classroom" / "morning.png")
        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_bytes(assets / "bgm" / "school" / "classroom" / "morning.ogg")
        write_bytes(assets / "bgm" / "school" / "classroom" / "evening.ogg")
        write_bytes(assets / "characters" / "校服" / "normal.png")
        write_bytes(assets / "characters" / "校服" / "sumg.png")
        write_bytes(assets / "characters" / "睡衣" / "normal.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()
        normalized = manifest.normalize_visual_output(
            {
                "emotion": "smug",
                "character": {"outfit": "睡衣"},
                "scene": {
                    "major": "school",
                    "minor": "classroom",
                    "background": "evening",
                    "bgm": "",
                },
            }
        )

        self.assertEqual(normalized["character"]["outfit"], "睡衣")
        self.assertEqual(normalized["emotion"], "normal")
        self.assertEqual(normalized["scene"]["background"], "evening")
        self.assertEqual(normalized["scene"]["bgm"], "evening")

    def test_common_emotion_aliases_resolve_to_available_chinese_character_assets(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "home" / "default" / "night.png")
        write_meta(
            assets / "characters" / "猫娘" / "meta.json",
            {
                "default_emotion": "正常",
            },
        )
        write_bytes(assets / "characters" / "猫娘" / "正常.png")
        write_bytes(assets / "characters" / "猫娘" / "开心.png")
        write_bytes(assets / "characters" / "猫娘" / "得意.png")
        write_bytes(assets / "characters" / "猫娘" / "脸红.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()

        cases = {
            "normal": "正常",
            "happy": "开心",
            "smug": "得意",
            "shy": "脸红",
        }
        for requested, expected in cases.items():
            with self.subTest(requested=requested):
                normalized = manifest.normalize_visual_output(
                    {
                        "emotion": requested,
                        "character": {"outfit": "猫娘"},
                        "scene": {
                            "major": "home",
                            "minor": "default",
                            "background": "night",
                        },
                    }
                )

                self.assertEqual(normalized["emotion"], expected)

    def test_custom_resource_names_and_notes_are_ai_readable(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_meta(
            assets / "scenes" / "battlefield" / "meta.json",
            {
                "id": "battlefield",
                "name": "古战场",
                "aliases": ["战场"],
            },
        )
        write_text(assets / "scenes" / "battlefield" / "ai.md", "这里是古代战争题材，不是日常早中晚场景。")
        write_meta(
            assets / "scenes" / "battlefield" / "frontline" / "meta.json",
            {
                "id": "frontline",
                "name": "前线营地",
                "aliases": ["前线"],
                "default_background": "siege_fire",
                "default_bgm": "war_drums",
            },
        )
        write_bytes(assets / "scenes" / "battlefield" / "frontline" / "dust_storm.png")
        write_bytes(assets / "scenes" / "battlefield" / "frontline" / "siege_fire.png")
        write_meta(
            assets / "scenes" / "battlefield" / "frontline" / "siege_fire.meta.json",
            {
                "name": "烽火压境",
                "aliases": ["第二阶段"],
                "description": "城门即将被攻破前的压迫感场景。",
            },
        )

        write_meta(
            assets / "bgm" / "battlefield" / "frontline" / "war_drums.meta.json",
            {
                "name": "战鼓压境",
                "aliases": ["战鼓"],
                "description": "推进战斗氛围的主旋律。",
            },
        )
        write_bytes(assets / "bgm" / "battlefield" / "frontline" / "war_drums.ogg")

        write_meta(
            assets / "characters" / "armor" / "meta.json",
            {
                "id": "armor",
                "name": "战甲",
                "aliases": ["铠甲"],
                "default_emotion": "battle_focus",
                "allowed_emotions": ["战斗专注", "热血爆发"],
            },
        )
        write_bytes(assets / "characters" / "armor" / "battle_focus.png")
        write_meta(
            assets / "characters" / "armor" / "battle_focus.meta.json",
            {
                "name": "战斗专注",
                "description": "进入战斗前的冷静状态。",
            },
        )
        write_bytes(assets / "characters" / "armor" / "rage_burst.png")
        write_meta(
            assets / "characters" / "armor" / "rage_burst.meta.json",
            {
                "name": "热血爆发",
                "aliases": ["爆发"],
            },
        )
        write_text(assets / "characters" / "armor" / "rage_burst.md", "适合高压战斗、突击、决断瞬间。")

        manifest = ResourceManifest(assets)
        payload = manifest.refresh()

        outfit = payload["characters"]["outfits"][0]
        self.assertEqual(outfit["id"], "armor")
        self.assertEqual(outfit["allowed_emotions"], ["battle_focus", "rage_burst"])
        self.assertEqual([item["id"] for item in outfit["emotions"]], ["battle_focus", "rage_burst"])

        normalized = manifest.normalize_visual_output(
            {
                "emotion": "热血爆发",
                "character": {"outfit": "战甲"},
                "scene": {
                    "major": "古战场",
                    "minor": "前线营地",
                    "background": "第二阶段",
                    "bgm": "战鼓",
                },
            }
        )

        self.assertEqual(normalized["character"]["outfit"], "armor")
        self.assertEqual(normalized["emotion"], "rage_burst")
        self.assertEqual(normalized["scene"]["major"], "battlefield")
        self.assertEqual(normalized["scene"]["minor"], "frontline")
        self.assertEqual(normalized["scene"]["background"], "siege_fire")
        self.assertEqual(normalized["scene"]["bgm"], "war_drums")

        prompt_context = manifest.build_prompt_context()
        self.assertIn("古战场", prompt_context)
        self.assertIn("前线营地", prompt_context)
        self.assertIn("第二阶段", prompt_context)
        self.assertIn("热血爆发", prompt_context)
        self.assertIn("古代战争题材", prompt_context)

        state_text = manifest.describe_visual_state(
            {
                "emotion": "热血爆发",
                "character": {"outfit": "战甲"},
                "scene": {
                    "major": "古战场",
                    "minor": "前线营地",
                    "background": "第二阶段",
                    "bgm": "战鼓",
                },
            }
        )
        self.assertIn("地点:", state_text)
        self.assertIn("古战场", state_text)
        self.assertIn("战甲", state_text)
        self.assertIn("战鼓压境", state_text)

    def test_extra_gift_bgm_tracks_can_be_used_globally(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "home" / "卧室_夜晚.png")
        write_bytes(assets / "bgm" / "default" / "default" / "night.ogg")
        write_bytes(assets / "characters" / "睡衣" / "normal.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()
        extra_tracks = [
            {
                "id": "gift_bgm_demo",
                "name": "夜色收藏",
                "description": "主人送来的私人收藏。",
                "path": "/user-assets/demo/night.flac",
                "aliases": ["夜色"],
                "ai_hint": "这是主人送给你的私人收藏歌曲，可以在任意场景下使用。",
            }
        ]

        normalized = manifest.normalize_visual_output(
            {
                "emotion": "normal",
                "character": {"outfit": "睡衣"},
                "scene": {
                    "major": "home",
                    "minor": "卧室",
                    "background": "night",
                    "bgm": "夜色",
                },
            },
            extra_bgm_tracks=extra_tracks,
        )
        prompt_context = manifest.build_prompt_context(extra_bgm_tracks=extra_tracks)
        state_text = manifest.describe_visual_state(
            {
                "emotion": "normal",
                "character": {"outfit": "睡衣"},
                "scene": {
                    "major": "home",
                    "minor": "卧室",
                    "background": "night",
                    "bgm": "gift_bgm_demo",
                },
            },
            extra_bgm_tracks=extra_tracks,
        )

        self.assertEqual(normalized["scene"]["bgm"], "gift_bgm_demo")
        self.assertIn("私人收藏 BGM", prompt_context)
        self.assertIn("夜色收藏", prompt_context)
        self.assertIn("夜色收藏", state_text)

    def test_resolve_visual_bundle_returns_runtime_objects(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_bytes(assets / "characters" / "default" / "normal.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()
        bundle = manifest.resolve_visual_bundle(
            {
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

        self.assertEqual(bundle["major"]["id"], "school")
        self.assertEqual(bundle["minor"]["id"], "classroom")
        self.assertEqual(bundle["background"]["id"], "evening")
        self.assertTrue(bundle["background"]["path"].startswith("/assets/"))

    def test_extra_scene_groups_can_be_selected_as_runtime_backgrounds(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_bytes(assets / "characters" / "default" / "normal.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()
        extra_scene_groups = [
            {
                "id": "gift_gallery",
                "name": "私人收藏",
                "minors": [
                    {
                        "id": "memories",
                        "name": "回忆",
                        "backgrounds": [
                            {
                                "id": "gift_scene_1",
                                "name": "窗边黄昏",
                                "path": "/user-assets/demo/window.png",
                                "aliases": ["IMG_0001"],
                            }
                        ],
                        "bgm_tracks": [],
                    }
                ],
            }
        ]

        normalized = manifest.normalize_visual_output(
            {
                "emotion": "normal",
                "character": {"outfit": "default"},
                "scene": {
                    "major": "gift_gallery",
                    "minor": "memories",
                    "background": "gift_scene_1",
                    "bgm": "",
                },
            },
            extra_scene_groups=extra_scene_groups,
        )
        bundle = manifest.resolve_visual_bundle(
            normalized,
            extra_scene_groups=extra_scene_groups,
        )

        self.assertEqual(normalized["scene"]["major"], "gift_gallery")
        self.assertEqual(normalized["scene"]["minor"], "memories")
        self.assertEqual(normalized["scene"]["background"], "gift_scene_1")
        self.assertEqual(bundle["background"]["path"], "/user-assets/demo/window.png")

    def test_extra_character_outfits_can_be_selected_as_runtime_emotions(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "scenes" / "school" / "classroom" / "evening.png")
        write_bytes(assets / "characters" / "default" / "normal.png")

        manifest = ResourceManifest(assets)
        manifest.refresh()
        extra_character_outfits = [
            {
                "id": "sailor_uniform",
                "name": "水手服",
                "aliases": ["水手服"],
                "allowed_emotions": ["quiet"],
                "emotions": [
                    {
                        "id": "quiet",
                        "name": "quiet",
                        "path": "/user-assets/demo/quiet.png",
                    }
                ],
            }
        ]

        normalized = manifest.normalize_visual_output(
            {
                "emotion": "quiet",
                "character": {"outfit": "水手服"},
                "scene": {
                    "major": "school",
                    "minor": "classroom",
                    "background": "evening",
                    "bgm": "",
                },
            },
            extra_character_outfits=extra_character_outfits,
        )
        bundle = manifest.resolve_visual_bundle(
            normalized,
            extra_character_outfits=extra_character_outfits,
        )
        prompt_context = manifest.build_prompt_context(extra_character_outfits=extra_character_outfits)

        self.assertEqual(normalized["character"]["outfit"], "sailor_uniform")
        self.assertEqual(normalized["emotion"], "quiet")
        self.assertEqual(bundle["outfit"]["name"], "水手服")
        self.assertEqual(bundle["emotion"]["path"], "/user-assets/demo/quiet.png")
        self.assertIn("水手服", prompt_context)
        self.assertIn("quiet", prompt_context)

    def test_character_only_assets_get_default_scene_and_custom_public_prefix(self) -> None:
        temp_dir, assets = self.make_assets_root()
        self.addCleanup(temp_dir.cleanup)

        write_bytes(assets / "characters" / "猫娘" / "开心.png")
        write_bytes(assets / "characters" / "猫娘" / "害羞.png")

        manifest = ResourceManifest(
            assets,
            public_prefix="/desktop-pet-character-packs/demo_pack/assets",
        )
        payload = manifest.refresh()
        outfit = payload["characters"]["outfits"][0]

        self.assertEqual(payload["defaults"]["major"], "default")
        self.assertEqual(payload["defaults"]["outfit"], "猫娘")
        self.assertEqual([item["id"] for item in outfit["emotions"]], ["害羞", "开心"])
        self.assertTrue(
            outfit["emotions"][0]["path"].startswith(
                "/desktop-pet-character-packs/demo_pack/assets/characters/猫娘/"
            )
        )

        normalized = manifest.normalize_visual_output(
            {
                "emotion": "thinking",
                "character": {"outfit": "猫娘"},
                "scene": {},
            }
        )

        self.assertEqual(normalized["character"]["outfit"], "猫娘")
        self.assertIn(normalized["emotion"], {"害羞", "开心"})


if __name__ == "__main__":
    unittest.main()
