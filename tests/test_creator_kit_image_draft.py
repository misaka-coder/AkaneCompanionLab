from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "desktop_pet_creator_kit" / "scripts" / "create-character-pack.mjs"
VALIDATOR = ROOT / "desktop_pet_creator_kit" / "scripts" / "validate-character-pack.mjs"


class CreatorKitImageDraftTests(unittest.TestCase):
    def test_create_pack_from_image_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw"
            output = root / "packs"
            raw.mkdir()
            output.mkdir()
            (raw / "正常.png").write_bytes(b"fake png")
            (raw / "开心.webp").write_bytes(b"fake webp")
            (raw / "思考中.jpg").write_bytes(b"fake jpg")

            subprocess.run(
                [
                    "node",
                    str(SCRIPT),
                    "--from-images",
                    str(raw),
                    "--id",
                    "demo_draft",
                    "--name",
                    "Demo",
                    "--to",
                    str(output),
                    "--force",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            pack_dir = output / "demo_draft"
            character = json.loads((pack_dir / "character.json").read_text(encoding="utf-8"))

            self.assertEqual(character["appearance"]["default_outfit"], "default")
            self.assertEqual(character["appearance"]["default_emotion"], "正常")
            self.assertEqual(character["appearance"]["music_emotion"], "开心")
            self.assertIn("思考中", character["appearance"]["recommended_emotions"])
            self.assertIn("开心", character["emotion_aliases"]["happy"])
            self.assertTrue((pack_dir / "persona.md").is_file())
            self.assertTrue((pack_dir / "assets" / "characters" / "default" / "正常.png").is_file())
            self.assertTrue((pack_dir / "assets" / "characters" / "default" / "开心.webp").is_file())

            subprocess.run(
                ["node", str(VALIDATOR), str(pack_dir)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
