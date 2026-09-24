"""Exercise the real CLI's destination-volume staging and failure cleanup."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "desktop_pet_creator_kit/scripts/import-character-pack.mjs"


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class CharacterPackCliImportTests(unittest.TestCase):
    def make_zip(self, root, *, invalid=False):
        character = json.loads((ROOT / "desktop_pet_creator_kit/templates/character_pack/character.json").read_text(encoding="utf-8"))
        character["identity"]["id"] = "test_import"
        character["appearance"]["music_emotion"] = "normal"
        character["appearance"]["recommended_emotions"] = []
        archive = root / "test_import.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("test_import/character.json", "{" if invalid else json.dumps(character))
            bundle.writestr("test_import/persona.md", "A test character.")
            bundle.writestr("test_import/assets/characters/default/normal.png", b"fixture")
        return archive

    def run_import(self, archive, destination):
        return subprocess.run([shutil.which("node"), str(IMPORTER), str(archive), "--to", str(destination)],
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_destination_volume_staging_and_existing_pack_preserved(self):
        # On Windows the checkout may be on F: while TemporaryDirectory is C:.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "characters"
            archive = self.make_zip(root)
            result = self.run_import(archive, destination)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(str(destination / ".tmp_import_"), result.stdout)
            installed = destination / "test_import/character.json"
            before = installed.read_bytes()
            self.assertFalse(list(destination.glob(".tmp_import_*")))
            repeat = self.run_import(archive, destination)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual(installed.read_bytes(), before)
            self.assertFalse(list(destination.glob(".tmp_import_*")))

    def test_invalid_pack_cleans_staging_without_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "characters"
            result = self.run_import(self.make_zip(root, invalid=True), destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((destination / "test_import").exists())
            self.assertFalse(list(destination.glob(".tmp_import_*")))
