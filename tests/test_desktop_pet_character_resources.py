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


if __name__ == "__main__":
    unittest.main()
