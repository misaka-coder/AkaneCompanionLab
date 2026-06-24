from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from companion_v01.desktop_pet_character_resources import (
    DesktopPetCharacterResourceService,
    sanitize_character_pack_id,
)


def write_bytes(path: Path, content: bytes = b"stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class DesktopPetCharacterResourceTests(unittest.TestCase):
    def test_sanitize_character_pack_id_rejects_paths(self) -> None:
        self.assertEqual(sanitize_character_pack_id("akane_sample"), "akane_sample")
        self.assertEqual(sanitize_character_pack_id("../web"), "")
        self.assertEqual(sanitize_character_pack_id("bad/name"), "")

    def test_character_pack_manifest_uses_pack_asset_urls(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"

        write_bytes(characters_dir / "mika_pack" / "assets" / "characters" / "猫娘" / "开心.png")
        write_bytes(characters_dir / "mika_pack" / "assets" / "characters" / "猫娘" / "害羞.png")

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        manifest = service.get_manifest("mika_pack")

        self.assertIsNotNone(manifest)
        payload = manifest.refresh() if manifest is not None else {}
        outfit = payload["characters"]["outfits"][0]

        self.assertEqual(payload["defaults"]["outfit"], "猫娘")
        self.assertEqual({item["id"] for item in outfit["emotions"]}, {"开心", "害羞"})
        self.assertTrue(
            outfit["emotions"][0]["path"].startswith(
                "/desktop-pet-character-packs/mika_pack/assets/characters/猫娘/"
            )
        )

        prompt_context = manifest.build_character_prompt_context() if manifest is not None else ""
        self.assertIn("开心", prompt_context)
        self.assertIn("害羞", prompt_context)

    def test_list_character_packs_returns_safe_display_metadata(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        write_json(
            characters_dir / "reimu" / "character.json",
            {
                "identity": {
                    "name": "Reimu",
                    "app_name": "Reimu Pet",
                    "user_title": "你",
                }
            },
        )
        write_json(characters_dir / "bad/name" / "character.json", {"identity": {"name": "Bad"}})
        (characters_dir / "no_json").mkdir(parents=True, exist_ok=True)

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        packs = service.list_character_packs()

        self.assertEqual(
            packs,
            [
                {
                    "pack_id": "reimu",
                    "id": "reimu",
                    "name": "Reimu",
                    "app_name": "Reimu Pet",
                    "user_title": "你",
                }
            ],
        )
        serialized = json.dumps(packs, ensure_ascii=False)
        self.assertNotIn(str(characters_dir), serialized)

    def test_character_pack_metadata_drives_desktop_persona_context_and_aliases(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        pack_dir = characters_dir / "mika_pack"

        write_bytes(pack_dir / "assets" / "characters" / "猫娘" / "开心.png")
        write_bytes(pack_dir / "assets" / "characters" / "猫娘" / "害羞.png")
        write_json(
            pack_dir / "character.json",
            {
                "identity": {
                    "id": "mika_pack",
                    "name": "Mika",
                    "app_name": "Mika Pet",
                    "user_title": "店长",
                },
                "appearance": {
                    "default_outfit": "猫娘",
                    "default_emotion": "开心",
                    "music_emotion": "开心",
                },
                "dialogue": {
                    "proactive_wake_prompt": "像坐在桌边一样轻轻接话。",
                    "local_click_lines": [
                        {"text": "店长，我在。", "emotion": "开心"},
                        {"text": "突然被点到会害羞。", "emotion": "害羞"},
                    ],
                },
                "emotion_aliases": {
                    "cheerful": ["开心", "normal"],
                    "shy": ["害羞", "normal"],
                },
            },
        )
        (pack_dir / "persona.md").write_text(
            "Mika speaks warmly and keeps replies concise.",
            encoding="utf-8",
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        manifest = service.get_manifest("mika_pack")
        self.assertIsNotNone(manifest)
        normalized = manifest.normalize_visual_output(
            {"emotion": "cheerful", "character": {"outfit": "猫娘"}}
        )
        self.assertEqual(normalized["emotion"], "开心")

        context = service.build_persona_prompt_context(
            "mika_pack",
            resource_manifest=manifest,
        )
        self.assertIn("Mika Pet / Mika", context["system_context"])
        self.assertIn("默认称呼用户：店长", context["system_context"])
        self.assertIn("cheerful -> 开心", context["system_context"])
        self.assertIn("底层项目名", context["system_context"])
        self.assertIn("Mika speaks warmly", context["reference_context"])
        self.assertIn("店长，我在。", context["reference_context"])
        self.assertEqual(
            service.build_persona_prompt_context("../web"),
            {"system_context": "", "reference_context": "", "active_id": ""},
        )

    def test_character_pack_metadata_drives_qq_persona_context_with_shared_emotion_ids(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        pack_dir = characters_dir / "mika_pack"

        write_bytes(pack_dir / "assets" / "characters" / "猫娘" / "开心.png")
        write_json(
            pack_dir / "character.json",
            {
                "identity": {
                    "id": "mika_pack",
                    "name": "Mika",
                    "app_name": "Mika Pet",
                    "user_title": "店长",
                    "self_reference": "我",
                    "relationship": "会在 QQ 里陪店长聊天的看板娘。",
                },
                "appearance": {
                    "default_outfit": "猫娘",
                    "default_emotion": "开心",
                },
                "persona_form": {
                    "speaking_style": "温柔但简短。",
                    "boundaries": "不要把自己说成通用客服。",
                },
                "emotion_aliases": {"cheerful": ["开心"]},
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        context = service.build_persona_prompt_context("mika_pack", client_mode="qq_text")

        self.assertIn("[CHARACTER PACK - qq_text]", context["system_context"])
        self.assertIn("当前 QQ 聊天角色包：Mika Pet / Mika", context["system_context"])
        self.assertIn("默认称呼用户：店长", context["system_context"])
        self.assertIn("QQ 端只发送文字", context["system_context"])
        self.assertIn("角色自称：我", context["system_context"])
        self.assertIn("会在 QQ 里陪店长聊天", context["system_context"])
        self.assertIn("图片文件名去掉扩展名", context["system_context"])
        self.assertIn("- 猫娘: 开心", context["system_context"])
        self.assertIn("cheerful -> 开心", context["system_context"])
        self.assertIn("说话风格: 温柔但简短。", context["reference_context"])
        self.assertIn("边界与禁忌: 不要把自己说成通用客服。", context["reference_context"])
        self.assertNotIn("desktop_pet only", context["system_context"])
        self.assertNotIn("character.outfit", context["system_context"])
        self.assertNotIn("默认服装", context["system_context"])

    def test_qq_delivery_mface_map_expands_only_current_pack_emotion_aliases(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        reimu_dir = characters_dir / "reimu_pack"
        akane_dir = characters_dir / "akane_pack"

        write_json(
            reimu_dir / "character.json",
            {
                "identity": {"id": "reimu_pack", "name": "Reimu"},
                "emotion_aliases": {"happy": ["开心", "卖萌"]},
                "qq_delivery": {
                    "emotion_mfaces": {
                        "enabled": True,
                        "map": {
                            "happy": {
                                "emoji_package_id": 1,
                                "emoji_id": "reimu-happy",
                                "key": "reimu-key",
                                "summary": "灵梦开心",
                            }
                        },
                    }
                },
            },
        )
        write_json(
            akane_dir / "character.json",
            {
                "identity": {"id": "akane_pack", "name": "Akane"},
                "emotion_aliases": {"happy": ["开心"]},
                "qq_delivery": {
                    "emotion_mfaces": {
                        "enabled": True,
                        "map": {
                            "happy": {
                                "emoji_package_id": 2,
                                "emoji_id": "akane-happy",
                                "key": "akane-key",
                                "summary": "Akane 开心",
                            }
                        },
                    }
                },
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        reimu_config = service.load_qq_delivery_config("reimu_pack")
        akane_config = service.load_qq_delivery_config("akane_pack")

        reimu_map = reimu_config["emotion_mfaces"]["map"]
        akane_map = akane_config["emotion_mfaces"]["map"]
        self.assertEqual(reimu_map["开心"]["emoji_id"], "reimu-happy")
        self.assertEqual(reimu_map["卖萌"]["emoji_id"], "reimu-happy")
        self.assertEqual(reimu_map["happy"]["emoji_id"], "reimu-happy")
        self.assertEqual(akane_map["开心"]["emoji_id"], "akane-happy")
        self.assertNotEqual(reimu_map["开心"]["emoji_id"], akane_map["开心"]["emoji_id"])

    def test_resolve_emotion_image_file_uses_current_character_pack_asset(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        reimu_dir = characters_dir / "reimu_pack"
        akane_dir = characters_dir / "akane_pack"

        write_bytes(reimu_dir / "assets" / "characters" / "default" / "开心.png", b"reimu")
        write_bytes(akane_dir / "assets" / "characters" / "default" / "开心.png", b"akane")
        write_json(
            reimu_dir / "character.json",
            {
                "identity": {"id": "reimu_pack", "name": "Reimu"},
                "appearance": {"default_outfit": "default", "default_emotion": "开心"},
                "emotion_aliases": {"happy": ["开心"]},
            },
        )
        write_json(
            akane_dir / "character.json",
            {
                "identity": {"id": "akane_pack", "name": "Akane"},
                "appearance": {"default_outfit": "default", "default_emotion": "开心"},
                "emotion_aliases": {"happy": ["开心"]},
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        reimu_image = service.resolve_emotion_image_file("reimu_pack", "happy")
        akane_image = service.resolve_emotion_image_file("akane_pack", "happy")

        self.assertTrue(reimu_image["path"].endswith("reimu_pack\\assets\\characters\\default\\开心.png"))
        self.assertTrue(akane_image["path"].endswith("akane_pack\\assets\\characters\\default\\开心.png"))
        self.assertNotEqual(reimu_image["path"], akane_image["path"])

    def test_qq_examples_and_output_collapse_to_only_existing_emotion_image(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        pack_dir = characters_dir / "reimu_pack"

        write_bytes(pack_dir / "assets" / "characters" / "default" / "害羞.png")
        write_json(
            pack_dir / "character.json",
            {
                "identity": {"id": "reimu_pack", "name": "Reimu"},
                "appearance": {
                    "default_outfit": "default",
                    "default_emotion": "普通",
                },
                "persona_form": {
                    "example_lines": [
                        {"text": "原本标成开心。", "emotion": "开心"},
                        {"text": "原本标成生气。", "emotion": "生气"},
                    ]
                },
                "dialogue": {
                    "local_click_lines": [
                        {"text": "原本标成疑惑。", "emotion": "疑惑"},
                    ]
                },
                "emotion_aliases": {
                    "happy": ["开心", "害羞"],
                    "angry": ["生气", "害羞"],
                },
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        manifest = service.get_manifest("reimu_pack")
        self.assertIsNotNone(manifest)
        context = service.build_persona_prompt_context(
            "reimu_pack",
            resource_manifest=manifest,
            client_mode="qq_text",
        )

        combined = "\n".join([context["system_context"], context["reference_context"]])
        self.assertIn("- default: 害羞", combined)
        self.assertEqual(combined.count("emotion=害羞"), 3)
        self.assertNotIn("emotion=开心", combined)
        self.assertNotIn("emotion=生气", combined)
        self.assertNotIn("emotion=疑惑", combined)
        self.assertEqual(manifest.normalize_emotion_output({"emotion": "开心"})["emotion"], "害羞")

    def test_v02_persona_form_fields_are_available_to_desktop_prompt(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        pack_dir = characters_dir / "reimu_demo"

        write_bytes(pack_dir / "assets" / "characters" / "default" / "normal.png")
        write_json(
            pack_dir / "character.json",
            {
                "schema_version": "akane.character.v0.2",
                "identity": {
                    "id": "reimu_demo",
                    "name": "Reimu",
                    "app_name": "Reimu Pet",
                    "self_reference": "我",
                    "user_title": "你",
                    "relationship": "住在桌面边上的巫女，会吐槽但也会帮忙。",
                },
                "persona_form": {
                    "personality_keywords": ["慵懒", "毒舌"],
                    "character_core": "看起来没干劲，但其实很可靠。",
                    "behavior_style": "用户忙碌时少打扰；用户低落时平静陪着。",
                    "speaking_style": "短句偏多，吐槽自然。",
                    "catchphrases": ["真麻烦啊"],
                    "boundaries": "不要把自己说成通用客服。",
                    "interaction_principles": "不要长篇说教，关心藏在具体提醒里。",
                    "proactive_style": "先轻轻吐槽一句，再问是否需要帮忙。",
                    "example_lines": [{"text": "又卡住了？把问题说出来。", "emotion": "normal"}],
                    "extra_setting": "补充世界观。",
                },
                "appearance": {
                    "default_outfit": "default",
                    "default_emotion": "normal",
                    "music_emotion": "normal",
                    "required_emotions": ["normal"],
                },
                "dialogue": {
                    "local_click_lines": [{"text": "嗯？有事就说。", "emotion": "normal"}],
                },
                "emotion_aliases": {"normal": ["normal"]},
                "layout": {"outfits": {"default": {"window": {"width": 420, "height": 620}}}},
                "voice": {"provider": "", "profile_id": "", "notes": ""},
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        identity = service.build_character_identity("reimu_demo")
        self.assertEqual(identity["character_id"], "reimu_demo")
        self.assertEqual(identity["user_label"], "你")

        context = service.build_persona_prompt_context("reimu_demo")
        self.assertEqual(context["active_id"], "reimu_demo")
        self.assertIn("角色自称：我", context["system_context"])
        self.assertIn("住在桌面边上的巫女", context["system_context"])
        self.assertIn("性格关键词: 慵懒、毒舌", context["reference_context"])
        self.assertIn("角色核心: 看起来没干劲，但其实很可靠。", context["reference_context"])
        self.assertIn("行为倾向: 用户忙碌时少打扰", context["reference_context"])
        self.assertIn("互动原则: 不要长篇说教", context["reference_context"])
        self.assertIn("又卡住了？把问题说出来。", context["reference_context"])

    def test_character_voice_preference_is_declarative_and_sanitized(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        characters_dir = Path(temp_dir.name) / "characters"
        pack_dir = characters_dir / "voice_demo"

        write_json(
            pack_dir / "character.json",
            {
                "identity": {
                    "id": "voice_demo",
                    "name": "Voice Demo",
                    "app_name": "Voice Demo",
                    "user_title": "用户",
                },
                "appearance": {},
                "dialogue": {},
                "voice": {
                    "provider": "gpt_sovits",
                    "profile_id": "voice_main",
                    "notes": "本地声线档案，不包含路径。",
                    "model_path": r"C:\Users\ExampleUser\secret.pth",
                    "token": "secret",
                },
            },
        )

        service = DesktopPetCharacterResourceService(characters_dir=characters_dir)
        voice = service.build_character_voice_preference("voice_demo")

        self.assertEqual(
            voice,
            {
                "packId": "voice_demo",
                "provider": "gpt_sovits",
                "profileId": "voice_main",
                "notes": "本地声线档案，不包含路径。",
            },
        )
        serialized = json.dumps(voice, ensure_ascii=False).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("model_path", serialized)
        self.assertEqual(service.build_character_voice_preference("../web"), {})


if __name__ == "__main__":
    unittest.main()
