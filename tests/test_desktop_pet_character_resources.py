from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
