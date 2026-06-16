from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from akane_paths import (
    AkaneDataPaths,
    ensure_akane_data_paths,
    get_akane_data_paths,
    migrate_legacy_data,
    resolve_akane_data_root,
)


class UserDataPathTests(unittest.TestCase):
    def test_explicit_root_has_priority_and_keeps_stable_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "custom-akane"
            paths = get_akane_data_paths(
                environ={"AKANE_DATA_ROOT": str(root), "LOCALAPPDATA": "ignored"},
                platform="win32",
            )

            self.assertEqual(root, paths.root)
            self.assertEqual(root / "users_data", paths.users_data)
            self.assertEqual(root / "characters", paths.characters)
            self.assertEqual(root / "state", paths.state)
            self.assertEqual(root / "logs", paths.logs)

    def test_windows_default_uses_local_app_data(self) -> None:
        root = resolve_akane_data_root(
            environ={"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"},
            platform="win32",
            home=Path(r"C:\Users\Example"),
        )

        self.assertEqual(Path(r"C:\Users\Example\AppData\Local") / "Akane", root)

    def test_ensure_creates_only_the_declared_data_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Akane"
            paths = AkaneDataPaths(
                root=root,
                users_data=root / "users_data",
                characters=root / "characters",
                state=root / "state",
                logs=root / "logs",
            )

            ensured = ensure_akane_data_paths(paths)

            self.assertEqual(paths, ensured)
            for directory in (root, paths.users_data, paths.characters, paths.state, paths.logs):
                self.assertTrue(directory.is_dir())

    def test_legacy_migration_copies_missing_files_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project = base / "project"
            root = base / "data" / "Akane"
            paths = AkaneDataPaths(
                root=root,
                users_data=root / "users_data",
                characters=root / "characters",
                state=root / "state",
                logs=root / "logs",
            )
            legacy_memory = project / "users_data" / "master" / "memory.txt"
            legacy_character = project / "desktop_pet_creator_kit" / "characters" / "demo" / "character.json"
            legacy_memory.parent.mkdir(parents=True)
            legacy_character.parent.mkdir(parents=True)
            legacy_memory.write_text("legacy memory", encoding="utf-8")
            legacy_character.write_text('{"name":"legacy"}', encoding="utf-8")
            destination_character = paths.characters / "demo" / "character.json"
            destination_character.parent.mkdir(parents=True)
            destination_character.write_text('{"name":"current"}', encoding="utf-8")

            result = migrate_legacy_data(project, paths=paths)

            self.assertEqual(1, result.copied)
            self.assertEqual(1, result.skipped)
            self.assertEqual(0, result.failed)
            self.assertEqual(
                "legacy memory",
                (paths.users_data / "master" / "memory.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual('{"name":"current"}', destination_character.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
