from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest

from companion_v01.music_lyrics import OnlineLyricsService, parse_lrc_segments


class OnlineLyricsDesktopContractTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.searches = []
        self.lrc = "[00:02.50]第一句测试\n[00:05.00]第二句测试"
        self.service = OnlineLyricsService(
            base_dir=self.root,
            config_module=SimpleNamespace(MUSIC_ONLINE_LYRICS_ENABLED=True),
            search_func=self.search,
            clock=lambda: 100,
        )
        self.payload = {"trackKey": "track", "title": "测试歌曲", "artist": "测试歌手"}

    def search(self, query, providers):
        self.searches.append(query)
        return self.lrc

    def test_fresh_and_cached_results_use_desktop_timestamps_and_keep_original_fields(self):
        first = self.service.resolve_lyrics(profile_user_id="owner", payload=self.payload)
        cached = self.service.resolve_lyrics(profile_user_id="owner", payload=self.payload)
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertTrue(cached["cached"])
        self.assertEqual(first["segments"], cached["segments"])
        self.assertEqual(len(self.searches), 1)
        self.assertEqual(first["lineCount"], 2)
        for segment, original in zip(first["segments"], parse_lrc_segments(self.lrc)):
            self.assertEqual(segment, {**original, "time_seconds": original["start"]})

    def test_legacy_cache_is_adapted_without_refetching_or_rewriting(self):
        self.service.resolve_lyrics(profile_user_id="owner", payload=self.payload)
        path = next((self.root / "owner/music/lyrics_cache").glob("*.json"))
        raw = path.read_bytes()
        stored = json.loads(raw)
        self.assertNotIn("time_seconds", stored["segments"][0])
        result = self.service.resolve_lyrics(
            profile_user_id="owner", payload={**self.payload, "trackKey": "new-track-key"},
        )
        self.assertEqual(result["trackKey"], "new-track-key")
        self.assertEqual(result["segments"][0]["time_seconds"], 2.5)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(len(self.searches), 1)

    def test_missing_lyrics_and_provider_failures_remain_empty(self):
        self.lrc = ""
        missing = self.service.resolve_lyrics(profile_user_id="owner", payload=self.payload)
        self.assertEqual(missing["status"], "not-found")
        self.assertEqual(missing["segments"], [])
        cached = self.service.resolve_lyrics(profile_user_id="owner", payload=self.payload)
        self.assertEqual(cached["segments"], [])
        self.assertTrue(cached["cached"])

        def failed(query, providers):
            raise RuntimeError("test provider unavailable")

        self.service.search_func = failed
        unavailable = self.service.resolve_lyrics(profile_user_id="other-owner", payload=self.payload)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason"], "lyrics_provider_failed")
        self.assertEqual(unavailable["segments"], [])

    def test_low_confidence_and_other_profiles_keep_their_own_outcomes(self):
        low = self.service.resolve_lyrics(profile_user_id="owner", payload={"title": "unknown"})
        self.assertEqual(low["status"], "low-confidence")
        self.assertEqual(low["segments"], [])
        ready = self.service.resolve_lyrics(profile_user_id="other-owner", payload=self.payload)
        ready["segments"][0]["text"] = "caller mutation"
        cached = self.service.resolve_lyrics(profile_user_id="other-owner", payload=self.payload)
        self.assertEqual(cached["segments"][0]["text"], "第一句测试")


if __name__ == "__main__":
    unittest.main()
