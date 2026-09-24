from __future__ import annotations

import asyncio
from array import array
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from akane_voice_clean.cleaning import CleaningError, FORMATS, MODES, Options, VoiceCleaner


def write_audio(path, seconds=2):
    samples = array("h")
    for n in range(48000):
        t = n / 48000
        samples.append(int(10000 * math.sin(2 * math.pi * 30 * t) + 4000 * math.sin(2 * math.pi * 800 * t)))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setframerate(48000)
        target.setsampwidth(2)
        for _ in range(seconds):
            target.writeframes(samples.tobytes())


def amplitude(path, hz):
    with wave.open(str(path), "rb") as source:
        values = array("h", source.readframes(source.getnframes()))
        rate = source.getframerate()
    if sys.byteorder != "little":
        values.byteswap()
    values = values[rate // 2 : rate]
    return abs(sum(v * math.sin(2 * math.pi * hz * n / rate) for n, v in enumerate(values))) / len(values)


class OptionTests(unittest.TestCase):
    def test_invalid_options_are_not_silently_defaulted(self):
        for args in ({"mode": "unknown"}, {"quality": "fast"}, {"output_format": "zip"}, {"post_filter": "false"}):
            with self.subTest(args=args), self.assertRaises(CleaningError):
                Options(**args)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required")
class CleaningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.wav"
        write_audio(self.source)
        self.cleaner = VoiceCleaner()

    async def asyncTearDown(self):
        await self.cleaner.aclose()
        self.tmp.cleanup()

    async def clean(self, **kwargs):
        options = Options(**kwargs)
        output = self.root / f"output.{options.output_format}"
        result = await self.cleaner.clean(source=self.source, output=output, options=options)
        return result, output

    async def test_all_formats_are_real_audio_and_original_is_unchanged(self):
        original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for fmt in FORMATS:
            result, output = await self.clean(quality="basic", output_format=fmt)
            self.assertEqual(result["backend_used"], "basic_ffmpeg")
            self.assertEqual(result["fallback_reason"], "")
            self.assertEqual(result["media_info"]["sample_rate"], 48000)
            self.assertEqual(result["media_info"]["channels"], 1)
            self.assertAlmostEqual(result["media_info"]["duration_seconds"], 2, delta=0.1)
            self.assertGreater(output.stat().st_size, 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original)

    async def test_every_mode_and_post_filter_changes_signal_with_honest_notice(self):
        hashes = set()
        for mode in MODES:
            for post_filter in (False, True):
                result, output = await self.clean(quality="basic", mode=mode, post_filter=post_filter)
                self.assertLess(amplitude(output, 30), amplitude(self.source, 30) * 0.4)
                self.assertGreater(amplitude(output, 800), 100)
                self.assertEqual(result["post_filter_applied"], post_filter)
                self.assertEqual(bool(result["notices"]), mode in ("deecho", "dereverb"))
                hashes.add(hashlib.sha256(output.read_bytes()).hexdigest())
                output.unlink()
        self.assertGreaterEqual(len(hashes), 4)

    async def test_basic_does_not_require_or_invoke_ml(self):
        with patch.object(self.cleaner, "ai", side_effect=AssertionError("basic invoked AI")):
            result, _ = await self.clean(quality="basic")
        self.assertEqual(result["backend_used"], "basic_ffmpeg")

    async def test_missing_interpreter_auto_falls_back_but_explicit_ai_fails(self):
        self.cleaner.python = "akane_nonexistent_clean_python"
        result, output = await self.clean(quality="auto")
        self.assertEqual(result["fallback_reason"], "deepfilternet_python_not_found")
        self.assertEqual(result["backend_used"], "basic_ffmpeg")
        output.unlink()
        with self.assertRaisesRegex(CleaningError, "^deepfilternet_python_not_found$"):
            await self.clean(quality="ai")
        self.assertEqual(list(self.root.iterdir()), [self.source])

    async def test_actual_missing_model_fails_offline_without_creating_cache(self):
        self.cleaner.model_root = self.root / "not-provisioned"
        with self.assertRaisesRegex(CleaningError, "^deepfilternet_model_missing$"):
            await self.cleaner.ai()
        self.assertFalse(self.cleaner.model_root.exists())

    async def test_actual_default_environment_auto_reports_its_real_backend(self):
        failure = ""
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            failure = str(exc)
        result, output = await self.clean(quality="auto")
        self.assertEqual(result["backend_used"], "basic_ffmpeg" if failure else "deepfilternet")
        self.assertEqual(result["fallback_reason"], failure)
        self.assertNotIn(str(self.root), str(result))
        self.assertNotEqual(output.read_bytes(), self.source.read_bytes())

    async def test_timeout_is_structured_and_drains_actual_ffmpeg(self):
        real_run = self.cleaner.runner.run

        async def short_timeout(argv, **kwargs):
            if "-nostdin" in argv:
                argv.insert(argv.index("-i"), "-re")
                kwargs["timeout"] = 0.15
            return await real_run(argv, **kwargs)

        with patch.object(self.cleaner.runner, "run", side_effect=short_timeout):
            with self.assertRaisesRegex(CleaningError, "^cleaning_timeout$"):
                await self.clean(quality="basic")
        self.assertFalse(self.cleaner.runner.processes)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    async def test_video_with_longer_picture_keeps_audio_length(self):
        video = self.root / "video.mp4"
        code, _ = await self.cleaner.runner.run(
            [
                shutil.which("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x32:r=10:d=4",
                "-i",
                str(self.source),
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(video),
            ],
            timeout=30,
        )
        self.assertEqual(code, 0)
        self.source = video
        before = video.read_bytes()
        result, _ = await self.clean(quality="basic")
        self.assertAlmostEqual(result["media_info"]["duration_seconds"], 2, delta=0.1)
        self.assertEqual(video.read_bytes(), before)

    async def test_original_and_existing_output_cannot_be_overwritten(self):
        original = self.source.read_bytes()
        with self.assertRaisesRegex(CleaningError, "cleaning_output_already_exists"):
            await self.cleaner.clean(source=self.source, output=self.source)
        output = self.root / "old.wav"
        output.write_bytes(b"existing")
        with self.assertRaisesRegex(CleaningError, "cleaning_output_already_exists"):
            await self.cleaner.clean(source=self.source, output=output)
        self.assertEqual(output.read_bytes(), b"existing")
        self.assertEqual(self.source.read_bytes(), original)

    async def test_playlist_bad_media_and_protected_source_fail(self):
        for name, data, reason in (
            ("bad.wav", b"garbage", "cleaning_media_invalid"),
            ("protected.ncm", b"garbage", "protected_media_format"),
            ("playlist.m3u8", b"#EXTM3U\nhttps://example.invalid/audio.wav\n", "cleaning_media_invalid"),
        ):
            self.source = self.root / name
            self.source.write_bytes(data)
            with self.subTest(name=name), self.assertRaisesRegex(CleaningError, reason):
                await self.clean(quality="basic")
        self.assertFalse(list(self.root.glob("cleaning-*")))
        self.assertFalse((self.root / "output.wav").exists())

    async def test_running_ffmpeg_cancel_drains_and_cleans_partial_files(self):
        write_audio(self.source, seconds=20)
        real_run = self.cleaner.runner.run

        async def slow_encode(argv, **kwargs):
            if "-nostdin" in argv:
                argv.insert(argv.index("-i"), "-re")
            return await real_run(argv, **kwargs)

        with patch.object(self.cleaner.runner, "run", side_effect=slow_encode):
            task = asyncio.create_task(self.clean(quality="basic"))
            try:
                for _ in range(500):
                    if list(self.root.glob("cleaning-*/prepared.wav")):
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(list(self.root.glob("cleaning-*/prepared.wav")))
                children = tuple(self.cleaner.runner.processes)
                self.assertTrue(children)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                self.assertTrue(all(child.returncode is not None for child in children))
                self.assertEqual(list(self.root.iterdir()), [self.source])
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_prepared_real_ai_environment_when_available(self):
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            self.skipTest(f"Prepared DeepFilterNet environment required: {exc}")
        original = self.source.read_bytes()
        result, output = await self.clean(quality="ai")
        self.assertEqual(result["backend_used"], "deepfilternet")
        self.assertEqual(result["fallback_reason"], "")
        self.assertNotEqual(output.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)

    async def test_real_ai_post_filter_reaches_audio(self):
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            self.skipTest(f"Prepared DeepFilterNet environment required: {exc}")
        plain, output = await self.clean(quality="ai")
        plain_audio = output.read_bytes()
        output.unlink()
        filtered, output = await self.clean(quality="ai", post_filter=True)
        self.assertFalse(plain["post_filter_applied"])
        self.assertTrue(filtered["post_filter_applied"])
        self.assertEqual(filtered["backend_used"], "deepfilternet")
        self.assertNotEqual(output.read_bytes(), plain_audio)

    async def test_real_ai_worker_cancel_waits_for_exit(self):
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            self.skipTest(f"Prepared DeepFilterNet environment required: {exc}")
        write_audio(self.source, seconds=120)
        real_run = self.cleaner.runner.run
        ai_started = asyncio.Event()

        async def track_worker(argv, **kwargs):
            if "--source" in argv:
                ai_started.set()
            return await real_run(argv, **kwargs)

        with patch.object(self.cleaner.runner, "run", side_effect=track_worker):
            task = asyncio.create_task(self.clean(quality="ai"))
            try:
                await asyncio.wait_for(ai_started.wait(), 10)
                await asyncio.sleep(2)
                children = tuple(self.cleaner.runner.processes)
                self.assertTrue(children)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 10)
                self.assertTrue(all(child.returncode is not None for child in children))
                self.assertEqual(list(self.root.iterdir()), [self.source])
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_empty_checkpoint_cannot_be_reported_as_loaded_ai_model(self):
        if not self.cleaner.model_root:
            self.skipTest("Explicit prepared model directory required")
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            self.skipTest(f"Prepared DeepFilterNet environment required: {exc}")
        model = self.root / "invalid-model"
        (model / "checkpoints").mkdir(parents=True)
        shutil.copy2(Path(self.cleaner.model_root) / "config.ini", model / "config.ini")
        code, _ = await self.cleaner.runner.run(
            [
                self.cleaner.python,
                "-c",
                "import sys, torch; torch.save({}, sys.argv[1])",
                str(model / "checkpoints/model_96.ckpt.best"),
            ],
            timeout=30,
        )
        self.assertEqual(code, 0)
        self.cleaner.model_root = model
        with self.assertRaisesRegex(CleaningError, "^deepfilternet_model_unavailable$"):
            await self.cleaner.ai()

    async def test_real_model_probe_needs_no_descendant_process(self):
        if not self.cleaner.model_root:
            self.skipTest("Explicit prepared model directory required")
        try:
            await self.cleaner.ai()
        except CleaningError as exc:
            self.skipTest(f"Prepared DeepFilterNet environment required: {exc}")
        # This uses the real model/library. Subprocess creation is rejected,
        # while Python's Windows platform probe may use its real OS API fallback.
        script = (
            "import sys, runpy\n"
            "def deny_spawn(event, args):\n"
            "    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn'):\n"
            "        raise PermissionError('unexpected_descendant')\n"
            "sys.addaudithook(deny_spawn)\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        worker = Path(__file__).resolve().parents[1] / "src/akane_voice_clean/worker.py"
        code, raw = await self.cleaner.runner.run(
            [
                self.cleaner.python,
                "-c",
                script,
                str(worker),
                "--probe",
                "--device",
                "cpu",
                "--model-root",
                str(self.cleaner.model_root),
            ],
            capture=True,
            timeout=60,
        )
        self.assertEqual(code, 0, raw)
        self.assertTrue(json.loads(raw)["ok"])


if __name__ == "__main__":
    unittest.main()
