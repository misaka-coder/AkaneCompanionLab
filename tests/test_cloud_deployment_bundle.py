from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_akane_cloud_bundle import (
    CloudBundleError,
    prepare_cloud_bundle,
    verify_cloud_data_root,
    verify_prepared_bundle,
)


class CloudDeploymentBundleTests(unittest.TestCase):
    def _source_root(self, root: Path) -> Path:
        source = root / "source"
        engine = source / "users_data" / "akane_memory_v01"
        engine.mkdir(parents=True)
        connection = sqlite3.connect(engine / "akane_memory_v01.db")
        try:
            connection.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, content TEXT)")
            connection.execute("CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO chat_messages(content) VALUES ('memory')")
            connection.execute("INSERT INTO chat_sessions DEFAULT VALUES")
            connection.execute("INSERT INTO memory_summaries DEFAULT VALUES")
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(engine / "memcore_v01.db")
        try:
            connection.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT)")
            connection.execute("INSERT INTO messages(content) VALUES ('memory')")
            connection.commit()
        finally:
            connection.close()
        (engine / "care_runtime.json").write_text('{"coins": 7}', encoding="utf-8")
        (engine / "chroma").mkdir()
        (engine / "chroma" / "derived.db").write_bytes(b"derived")
        (engine / "attachment_inbox_files").mkdir()
        (engine / "attachment_inbox_files" / "old.bin").write_bytes(b"old")
        (source / "state").mkdir()
        (source / "state" / "qq_gateway_state.json").write_text("{}", encoding="utf-8")
        selected = source / "characters" / "reimu"
        selected.mkdir(parents=True)
        (selected / "character.json").write_text("{}", encoding="utf-8")
        (selected / "portrait.png").write_bytes(b"portrait")
        (selected / "_local").mkdir()
        (selected / "_local" / "private.json").write_text("{}", encoding="utf-8")
        other = source / "characters" / "other"
        other.mkdir(parents=True)
        (other / "character.json").write_text("{}", encoding="utf-8")
        plugin = source / "instances" / "local-default" / "plugins" / "private.plugin"
        plugin.mkdir(parents=True)
        (plugin / "state.json").write_text("{}", encoding="utf-8")
        local = source / "users_data" / "_local"
        local.mkdir(parents=True)
        (local / "model_service.json").write_text('{"api_key":"secret"}', encoding="utf-8")
        return source

    def test_bundle_contains_core_continuity_without_local_or_old_bot_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source_root(root)
            output = root / "bundle"

            result = prepare_cloud_bundle(
                source_root=source,
                output_dir=output,
                instance_id="akane-cloud",
                character_pack_id="reimu",
                qq_profile_ref="akane-cloud-qq",
                additional_character_pack_ids=("other",),
            )

            self.assertTrue(result["ok"])
            data = output / "data"
            self.assertTrue((data / "characters" / "reimu" / "character.json").is_file())
            self.assertTrue((data / "characters" / "other" / "character.json").is_file())
            self.assertFalse((data / "characters" / "reimu" / "_local").exists())
            self.assertFalse((data / "state" / "qq_gateway_state.json").exists())
            self.assertFalse((data / "instances" / "akane-cloud" / "plugins").exists())
            self.assertFalse((data / "users_data" / "_local").exists())
            self.assertFalse((data / "users_data" / "akane_memory_v01" / "chroma").exists())

            verification = verify_cloud_data_root(
                data,
                expected_instance_id="akane-cloud",
                expected_character_pack_id="reimu",
                expected_qq_profile_ref="akane-cloud-qq",
                expected_character_pack_ids=("reimu", "other"),
            )
            self.assertEqual(verification["counts"]["chat_messages"], 1)
            self.assertEqual(verification["counts"]["memcore_messages"], 1)

            manifest = json.loads((output / "bundle-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["data_profile"], "core-continuity")
            self.assertEqual(manifest["character_pack_ids"], ["reimu", "other"])
            self.assertTrue(all(not Path(item["path"]).is_absolute() for item in manifest["files"]))
            env_template = (output / "instance.env.example").read_text(encoding="utf-8")
            self.assertIn("AKANE_INSTANCE_ID=akane-cloud", env_template)
            self.assertNotIn("QQ_CHARACTER_PACK_ID=", env_template)
            self.assertIn("MASTER_QQ=replace-with-owner-qq-number", env_template)
            self.assertNotIn("api_key\":\"secret", env_template)

            prepared = verify_prepared_bundle(output)
            self.assertEqual(prepared["status"], "valid")
            self.assertEqual(prepared["instance_id"], "akane-cloud")
            self.assertEqual(prepared["character_pack_ids"], ["other", "reimu"])

            (data / "characters" / "reimu" / "portrait.png").write_bytes(b"tampered")
            with self.assertRaises(CloudBundleError) as raised:
                verify_prepared_bundle(output)
            self.assertEqual(raised.exception.reason, "bundle_inventory_mismatch")

    def test_missing_bundle_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            with self.assertRaises(CloudBundleError) as raised:
                verify_prepared_bundle(missing)

            self.assertEqual(raised.exception.reason, "bundle_directory_invalid")


if __name__ == "__main__":
    unittest.main()
