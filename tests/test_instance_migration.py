from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.instance_profile import resolve_instance_context
from companion_v01.instance_runtime import InstanceRuntimeError, bind_instance_runtime
from scripts.migrate_akane_instance import MigrationError, migrate_instance


class InstanceMigrationTests(unittest.TestCase):
    def _source_root(self, root: Path) -> Path:
        source = root / "source"
        engine = source / "users_data" / "akane_memory_v01"
        engine.mkdir(parents=True)
        connection = sqlite3.connect(engine / "akane_memory_v01.db")
        try:
            connection.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT)")
            connection.execute("INSERT INTO messages(content) VALUES ('source-memory')")
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(engine / "memcore_v01.db")
        try:
            connection.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT)")
            connection.commit()
        finally:
            connection.close()
        (engine / "care_runtime.json").write_text('{"coins": 7}', encoding="utf-8")
        (engine / "chroma").mkdir()
        (engine / "chroma" / "chroma.sqlite3").write_text("derived", encoding="utf-8")
        (source / "state").mkdir()
        (source / "state" / "qq_gateway_state.json").write_text("{}", encoding="utf-8")
        (source / "workspace" / "Inbox").mkdir(parents=True)
        (source / "workspace" / "Inbox" / "input.txt").write_text("hello", encoding="utf-8")
        (source / "characters" / "akane_v1" / "assets").mkdir(parents=True)
        (source / "characters" / "akane_v1" / "character.json").write_text("{}", encoding="utf-8")
        (source / "characters" / "akane_v1" / "_local" / "memory").mkdir(parents=True)
        (source / "characters" / "akane_v1" / "_local" / "memory" / "private.md").write_text(
            "derived",
            encoding="utf-8",
        )
        plugin = source / "instances" / "finance-prod" / "plugins" / "finance.tools"
        plugin.mkdir(parents=True)
        (plugin / "state.json").write_text("{}", encoding="utf-8")
        (source / "users_data" / "_local").mkdir(parents=True)
        (source / "users_data" / "_local" / "model_service.json").write_text(
            '{"api_key":"must-not-copy"}',
            encoding="utf-8",
        )
        return source

    def test_offline_migration_copies_authority_and_excludes_derived_or_secret_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source_root(root)
            target = root / "target"

            result = migrate_instance(
                source_root=source,
                target_root=target,
                instance_id="finance-prod",
                character_pack_id="akane_v1",
            )

            self.assertEqual(result["status"], "completed")
            self.assertTrue((target / "users_data" / "akane_memory_v01" / "akane_memory_v01.db").is_file())
            self.assertTrue((target / "workspace" / "Inbox" / "input.txt").is_file())
            self.assertTrue(
                (target / "instances" / "finance-prod" / "plugins" / "finance.tools" / "state.json").is_file()
            )
            self.assertFalse((target / "users_data" / "akane_memory_v01" / "chroma").exists())
            self.assertFalse((target / "characters" / "akane_v1" / "_local").exists())
            self.assertFalse((target / "users_data" / "_local" / "model_service.json").exists())
            self.assertFalse((target / "migration-incomplete.json").exists())
            self.assertEqual(
                json.loads((target / "instance-binding.json").read_text(encoding="utf-8")),
                {"instance_id": "finance-prod", "schema_version": 1},
            )
            self.assertTrue((source / "users_data" / "_local" / "model_service.json").is_file())

            context = resolve_instance_context(data_root=target, selected_instance_id="finance-prod")
            lease = bind_instance_runtime(context, data_root=target, explicit_data_root=True)
            lease.release()

    def test_mid_migration_failure_marks_target_incomplete_and_blocks_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source_root(root)
            target = root / "target"

            with patch(
                "scripts.migrate_akane_instance._copy_optional_tree",
                side_effect=MigrationError("injected_copy_failure"),
            ):
                with self.assertRaises(MigrationError):
                    migrate_instance(
                        source_root=source,
                        target_root=target,
                        instance_id="finance-prod",
                        character_pack_id="akane_v1",
                    )

            marker = json.loads((target / "migration-incomplete.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["reason"], "injected_copy_failure")
            context_dir = target / "instances" / "finance-prod"
            context_dir.mkdir(parents=True, exist_ok=True)
            context_dir.joinpath("instance.toml").write_text(
                "schema_version = 1\n"
                'instance_id = "finance-prod"\n'
                'character_pack_id = "akane_v1"\n\n'
                "[features]\ncare = true\n",
                encoding="utf-8",
            )
            context = resolve_instance_context(data_root=target, selected_instance_id="finance-prod")
            with self.assertRaises(InstanceRuntimeError) as raised:
                bind_instance_runtime(context, data_root=target, explicit_data_root=True)
            self.assertEqual(raised.exception.reason, "instance_migration_incomplete")

    def test_running_source_is_rejected_before_target_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self._source_root(root)
            manifest = source / "instances" / "finance-prod" / "instance.toml"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                "schema_version = 1\n"
                'instance_id = "finance-prod"\n'
                'character_pack_id = "akane_v1"\n\n'
                "[features]\ncare = true\n",
                encoding="utf-8",
            )
            context = resolve_instance_context(data_root=source, selected_instance_id="finance-prod")
            lease = bind_instance_runtime(context, data_root=source, explicit_data_root=True)
            try:
                target = root / "target"
                with self.assertRaises(MigrationError) as raised:
                    migrate_instance(
                        source_root=source,
                        target_root=target,
                        instance_id="finance-prod",
                        character_pack_id="akane_v1",
                    )
                self.assertEqual(raised.exception.reason, "source_instance_running")
                self.assertFalse(target.exists())
            finally:
                lease.release()


if __name__ == "__main__":
    unittest.main()
