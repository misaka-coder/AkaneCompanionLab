from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.gift_assets import GiftAssetLibrary
from companion_v01.store import MemoryStore


class GiftAssetLibraryTests(unittest.TestCase):
    def test_save_audio_gift_persists_file_and_asset_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            library = GiftAssetLibrary(root / "gifts", store=store)

            record = library.save_audio_gift(
                profile_user_id="user_a",
                session_id="session_a",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )

            saved_path = library.base_dir / Path(record["storage_relpath"])

            self.assertEqual(record["status"], "pending")
            self.assertEqual(record["display_name"], "夜色")
            self.assertEqual(record["asset_type"], "audio")
            self.assertTrue(saved_path.exists())
            self.assertEqual(saved_path.read_bytes(), b"stub-audio")

    def test_build_internalized_bgm_entries_uses_public_asset_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            library = GiftAssetLibrary(root / "gifts", store=store)
            offered = library.save_audio_gift(
                profile_user_id="user_a",
                session_id="session_a",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            library.apply_action(
                profile_user_id="user_a",
                asset_id=offered["asset_id"],
                action="internalize",
                timestamp=120,
            )

            entries = library.build_internalized_bgm_entries("user_a")

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["id"], offered["resource_id"])
            self.assertTrue(entries[0]["path"].startswith("/user-assets/"))
            self.assertIn("私人收藏歌曲", entries[0]["ai_hint"])


if __name__ == "__main__":
    unittest.main()
