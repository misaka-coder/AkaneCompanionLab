"""Cache/flow fixtures are explicit doubles; media execution uses FFmpeg.

Actual RVC model acceptance is run separately on the isolated local instance.
"""

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from companion_v01.plugin_subprocess import PluginProcessRunner
from plugins.akane_cover_song.src.akane_cover_song import (
    CoverCache,
    CoverMedia,
    CoverOptions,
    CoverPipeline,
    CoverSongError,
    ProviderCalls,
)
from plugins.akane_cover_song.src.akane_cover_song.cache import cache_key
from plugins.akane_cover_song.src.akane_cover_song import cache as cache_module
from plugins.akane_cover_song.tests.test_media import tone


class CacheTests(unittest.TestCase):
    def test_title_restore_hashes_latest_only_and_falls_back_from_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = CoverCache(root / "cache", scope="u")
            source = root / "source.wav"
            tone(source)
            meta = dict(song_title="song", artist="artist", voice_model="voice.pth", output_format="wav")
            keys = [cache_key({"render": index}) for index in range(10)]
            for key in keys:
                cache.put("covers", key, files={"cover": source}, metadata=meta)
            args = dict(song_title="song", artist="artist", model_name="voice", output_format="wav")
            with patch.object(cache_module, "digest_file", wraps=cache_module.digest_file) as digest:
                self.assertEqual(cache.find_cover(**args)["key"], keys[-1])
                self.assertEqual(digest.call_count, 1)
            tone(source, amplitude=0.3)
            newest = cache_key({"render": 11})
            cache.put("covers", newest, files={"cover": source}, metadata=meta)
            path = cache.get("covers", newest)["paths"]["cover"]
            content = path.read_bytes()
            path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
            with patch.object(cache_module, "digest_file", wraps=cache_module.digest_file) as digest:
                self.assertEqual(cache.find_cover(**args)["key"], keys[-1])
                self.assertEqual(digest.call_count, 2)

    def test_atomic_manifest_survives_partial_write_and_scope_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = CoverCache(root / "cache", scope="a/b")
            other = CoverCache(root / "cache", scope="a_b")
            self.assertNotEqual(cache.root, other.root)
            key = cache_key({"source": 1})
            first, second = root / "first.wav", root / "second.wav"
            tone(first, amplitude=0.1)
            tone(second, amplitude=0.2)
            cache.put("stems", key, files={"vocals": first, "instrumental": first}, metadata={"take": 1})
            real_copy = shutil.copyfile
            count = 0

            def fail_second(src, dst):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("fixture_disk_full")
                return real_copy(src, dst)

            with patch("plugins.akane_cover_song.src.akane_cover_song.cache.shutil.copyfile", side_effect=fail_second):
                with self.assertRaises(OSError):
                    cache.put("stems", key, files={"vocals": second, "instrumental": second}, metadata={"take": 2})
            entry = cache.get("stems", key)
            self.assertEqual(entry["metadata"], {"take": 1})
            self.assertEqual(entry["paths"]["vocals"].read_bytes(), first.read_bytes())
            self.assertEqual(entry["paths"]["instrumental"].read_bytes(), first.read_bytes())
            self.assertIsNone(other.get("stems", key))
            first.write_bytes(b"changed source")
            self.assertIsNotNone(cache.get("stems", key))
            entry["paths"]["vocals"].write_bytes(b"corrupt cache")
            self.assertIsNone(cache.get("stems", key))
            self.assertFalse(list(cache.root.rglob("*.tmp")))

    def test_title_lookup_requires_completed_integrity_and_matching_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = CoverCache(root / "cache", scope="u")
            source = root / "source.wav"
            tone(source)
            metadata = {
                "song_title": "同名歌",
                "artist": "A",
                "voice_model": "voice.pth",
                "output_format": "wav",
                "params": asdict(CoverOptions()),
            }
            args = dict(
                song_title="同名歌",
                artist="",
                model_name="voice.pth",
                output_format="wav",
                params=asdict(CoverOptions()),
            )
            with self.assertRaises(CoverSongError):
                cache.find_cover(**args)
            key = cache_key({"id": 1})
            cache.put("covers", key, files={"cover": source}, metadata=metadata)
            self.assertTrue(cache.has_cover())
            self.assertEqual(cache.find_cover(**args)["key"], key)
            with self.assertRaises(CoverSongError) as caught:
                cache.find_cover(**{**args, "params": asdict(CoverOptions(pitch_shift=1))})
            self.assertEqual(caught.exception.reason, "cached_cover_not_found")
            cache.put("covers", cache_key({"id": 2}), files={"cover": source}, metadata={**metadata, "artist": "B"})
            with self.assertRaises(CoverSongError) as caught:
                cache.find_cover(**args)
            self.assertEqual(caught.exception.reason, "cached_cover_ambiguous")
            self.assertEqual(cache.find_cover(**{**args, "artist": "A"})["key"], key)

    def test_concurrent_process_writers_publish_whole_stem_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "a.wav", root / "b.wav"
            tone(first, amplitude=0.1)
            tone(second, amplitude=0.2)
            key = cache_key({"id": 1})
            code = (
                "from akane_cover_song import CoverCache; import sys; from pathlib import Path; "
                "c=CoverCache(sys.argv[1],scope='u'); p=Path(sys.argv[3]); "
                "c.put('stems',sys.argv[2],files={'vocals':p,'instrumental':p},metadata={'take':p.stem})"
            )
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            children = [
                subprocess.Popen(
                    [sys.executable, "-c", code, str(root / "cache"), key, str(path)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for path in (first, second)
            ]
            try:
                for child in children:
                    _, err = child.communicate(timeout=20)
                    self.assertEqual(child.returncode, 0, err.decode())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
            entry = CoverCache(root / "cache", scope="u").get("stems", key)
            self.assertIn(entry["metadata"]["take"], ("a", "b"))
            self.assertEqual(entry["paths"]["vocals"].read_bytes(), entry["paths"]["instrumental"].read_bytes())


class RecordingProvider:
    provider_id = "fixture_only"
    separation_model = "fixture_split"

    def __init__(self):
        self.separations, self.conversions = 0, 0

    def resolve_voice_model(self, model, *, default_model=""):
        return default_model or "voice.pth" if model == "auto" else model

    def model_fingerprint(self, model):
        return {"model": model, "fixture": True}

    def separate_vocals(self, *, source_path, work_dir):
        self.separations += 1
        a, b = work_dir / "fixture-vocals.wav", work_dir / "fixture-instrumental.wav"
        shutil.copyfile(source_path, a)
        shutil.copyfile(source_path, b)
        return a, b

    def convert_voice(self, *, source_path, output_path, **kwargs):
        self.conversions += 1
        shutil.copyfile(source_path, output_path)
        return {"timings": {"inference_request": 0.01}}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runner = PluginProcessRunner()
        self.source = self.root / "source.wav"
        tone(self.source)
        self.cache = CoverCache(self.root / "cache", scope="user")
        self.provider = RecordingProvider()
        self.media = CoverMedia(run=self.runner.run, ffmpeg=shutil.which("ffmpeg"), ffprobe=shutil.which("ffprobe"))
        self.pipeline = CoverPipeline(
            provider=self.provider, media=self.media, cache=self.cache, default_model="voice.pth"
        )

    async def asyncTearDown(self):
        await self.runner.aclose()
        self.tmp.cleanup()

    async def run_cover(self, **kwargs):
        args = {
            "source_path": self.source,
            "work_dir": self.root / uuid.uuid4().hex,
            "output_format": "wav",
            "song_title": "测试歌",
            **kwargs,
        }
        return await self.pipeline.run(**args)

    async def test_two_cache_layers_force_rebuild_and_title_restore_with_real_mix(self):
        first = await self.run_cover()
        self.assertFalse(first["processing"]["cache_hit"])
        same = await self.run_cover()
        self.assertTrue(same["processing"]["cache_hit"])
        changed = await self.run_cover(voice_model="other.pth")
        self.assertTrue(changed["processing"]["stems_cache_hit"])
        self.assertEqual((self.provider.separations, self.provider.conversions), (1, 2))
        await self.run_cover(force_rebuild=True)
        self.assertEqual((self.provider.separations, self.provider.conversions), (2, 3))
        restored = await self.run_cover(source_path=None)
        self.assertTrue(restored["processing"]["cache_hit"])
        self.assertEqual(restored["path"].read_bytes(), first["path"].read_bytes())
        self.assertNotIn(str(self.root), json.dumps(first["metadata"]))
        self.assertNotIn(str(self.root), json.dumps(first["processing"]))
        with self.assertRaises(CoverSongError) as caught:
            await self.run_cover(source_path=None, force_rebuild=True)
        self.assertEqual(caught.exception.reason, "source_required_for_rebuild")

    async def test_changed_provider_namespace_invalidates_both_source_cache_layers(self):
        self.provider.cache_namespace = lambda: "first-endpoint-or-separator-revision"
        first = await self.run_cover()
        self.provider.cache_namespace = lambda: "second-endpoint-or-separator-revision"
        changed = await self.run_cover()
        self.assertNotEqual(first["cache_key"], changed["cache_key"])
        self.assertFalse(changed["processing"]["cache_hit"])
        self.assertFalse(changed["processing"]["stems_cache_hit"])
        self.assertEqual((self.provider.separations, self.provider.conversions), (2, 2))
        self.assertTrue((await self.run_cover(source_path=None))["processing"]["cache_hit"])

    async def test_failed_conversion_leaves_only_completed_stems_and_no_cover(self):
        def fail(**kwargs):
            raise CoverSongError(stage="voice", reason="fixture_failure", public_message="fixture")

        self.provider.convert_voice = fail
        with self.assertRaises(CoverSongError):
            await self.run_cover()
        self.assertFalse(self.cache.has_cover())
        self.assertEqual(len(list(self.cache.root.joinpath("stems").glob("*.json"))), 1)

    async def test_title_only_restore_works_offline_and_does_not_guess_ambiguous_voice(self):
        first = await self.run_cover()
        await self.run_cover(voice_model="other.pth")
        with patch.object(
            self.provider, "resolve_voice_model", side_effect=AssertionError("offline provider must not be called")
        ) as resolve:
            restored = await self.run_cover(source_path=None)
            self.assertEqual(restored["path"].read_bytes(), first["path"].read_bytes())
            resolve.assert_not_called()
            self.pipeline.default_model = ""
            with self.assertRaises(CoverSongError) as caught:
                await self.run_cover(source_path=None)
            self.assertEqual(caught.exception.reason, "cached_voice_model_ambiguous")
            selected = await self.run_cover(source_path=None, voice_model="voice")
            self.assertEqual(selected["metadata"]["voice_model"], "voice.pth")

    async def test_cache_integrity_io_runs_off_event_loop(self):
        await self.run_cover()
        event_loop_thread = threading.get_ident()
        original = self.cache.find_cover

        def checked(**kwargs):
            self.assertNotEqual(threading.get_ident(), event_loop_thread)
            return original(**kwargs)

        with patch.object(self.cache, "find_cover", side_effect=checked):
            self.assertTrue((await self.run_cover(source_path=None))["processing"]["cache_hit"])

    async def test_cache_write_failure_preserves_real_output_with_explicit_notice(self):
        with patch.object(self.cache, "put", side_effect=OSError("fixture_disk_full")):
            result = await self.run_cover()
        self.assertTrue(result["path"].is_file())
        self.assertEqual(result["processing"]["notices"], ["stem_cache_write_failed", "cover_cache_write_failed"])

    async def test_repeated_cancel_drains_provider_and_keeps_uncertain_failure(self):
        for fail in (False, True):
            bridge = ProviderCalls()
            entered, release = threading.Event(), threading.Event()

            def blocking():
                entered.set()
                release.wait(5)
                if fail:
                    raise CoverSongError(
                        stage="voice", reason="rvc_remote_completion_unconfirmed", public_message="fixture"
                    )
                return "terminal"

            task = asyncio.create_task(bridge.call(blocking))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(entered.is_set())
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertTrue(bridge.cancelled())
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(CoverSongError if fail else asyncio.CancelledError) as caught:
                await task
            if fail:
                self.assertEqual(caught.exception.reason, "rvc_remote_completion_unconfirmed")


if __name__ == "__main__":
    unittest.main()
