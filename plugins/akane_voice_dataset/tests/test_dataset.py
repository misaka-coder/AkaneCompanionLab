from __future__ import annotations

import asyncio
from array import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from akane_voice_dataset.options import DatasetError, Options, PRESETS
from akane_voice_dataset.runtime import DatasetRuntime, Source


def fixture(path, *, seconds=4.0, rate=16000, stereo=False, silence=False):
    samples = array("h")
    for index in range(int(seconds * rate)):
        t = index / rate
        amplitude = 0 if silence or (0.8 <= t % 3 < 2.2) else (32500 if t < 0.3 else 5000)
        value = int(amplitude * math.sin(2 * math.pi * 440 * t))
        samples.append(value)
        if stereo:
            samples.append(-value)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2 if stereo else 1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())


class OptionTests(unittest.TestCase):
    def test_defaults_and_rejection(self):
        for profile, values in PRESETS.items():
            self.assertEqual(Options(profile=profile).target_sr, values[0])
        for args in (
            {"profile": "unknown"},
            {"target_sr": 0},
            {"mono": "false"},
            {"min_clip_seconds": float("nan")},
            {"min_silence_ms": 100.5},
            {"min_clip_seconds": 12, "max_clip_seconds": 4},
        ):
            with self.assertRaises(DatasetError):
                Options(**args)
        for title in ("../path", "C:\\private", "title\nsecret"):
            with self.assertRaises(DatasetError):
                Source(Path("test"), "audio_1", title, 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class DatasetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audio = self.root / "audio.wav"
        fixture(self.audio)
        self.source = Source(self.audio, "audio_1", "音频.wav", 1)
        self.runtime = DatasetRuntime()
        self.addAsyncCleanup(self.runtime.aclose)
        self.digest = hashlib.sha256(self.audio.read_bytes()).hexdigest()

    async def build(self, **kwargs):
        output = self.root / "result.zip"
        output.unlink(missing_ok=True)
        result = await self.runtime.build(
            sources=kwargs.pop("sources", [self.source]), output=output, work_root=self.root, **kwargs
        )
        self.assertTrue(result["ok"], result)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(
                set(archive.namelist()),
                {"README.md", "manifest.json"} | {"slices/" + s["filename"] for s in manifest["slices"]},
            )
            self.assertEqual(result["stats"], manifest["stats"])
            self.assertNotIn(str(self.root), json.dumps(manifest))
            for item in manifest["slices"]:
                with wave.open(io.BytesIO(archive.read("slices/" + item["filename"]))) as audio:
                    self.assertEqual(audio.getframerate(), manifest["options"]["target_sr"])
                    self.assertEqual(audio.getnchannels(), item["channels"])
                    self.assertAlmostEqual(
                        audio.getnframes() / audio.getframerate(), item["duration_seconds"], delta=0.001
                    )
                    self.assertAlmostEqual(item["end_time"] - item["start_time"], item["duration_seconds"], delta=0.002)
                self.assertEqual(item["source_handle"], "audio_1")
            self.assertIn("Full source", archive.read("README.md").decode())
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), self.digest)
        self.assertFalse(list(self.root.glob("dataset-*")))
        return manifest

    async def test_real_profiles_pause_slices_quality_and_original(self):
        self.assertTrue((await self.runtime.probe())["ok"])
        for profile in PRESETS:
            manifest = await self.build(options=Options(profile=profile))
            self.assertGreaterEqual(len(manifest["slices"]), 2)
            self.assertEqual(manifest["profile"], profile)
            self.assertEqual(manifest["options"]["target_sr"], PRESETS[profile][0])
            self.assertTrue(manifest["issue_slices"]["too_short"])
            self.assertTrue(manifest["issue_slices"]["clipping"])

    async def test_stereo_actual_channels_and_antiphase_are_not_fake_silence(self):
        fixture(self.audio, stereo=True)
        self.digest = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        manifest = await self.build(options=Options(mono=False, target_sr=24000, min_clip_seconds=0.5))
        self.assertTrue(all(s["channels"] == 2 for s in manifest["slices"]))
        self.assertFalse(manifest["issue_slices"].get("empty_or_failed"))

    async def test_96khz_pcm_wav_is_readable_and_max_length_is_a_flag(self):
        manifest = await self.build(options=Options(target_sr=96000, min_clip_seconds=0.5, max_clip_seconds=1.0))
        self.assertTrue(manifest["issue_slices"]["too_long"])
        self.assertTrue(all(s["sample_rate"] == 96000 for s in manifest["slices"]))

    async def test_actual_ffmpeg_repeated_cancel_drains_before_cleanup(self):
        original = self.runtime.runner.run

        async def realtime(args, **kwargs):
            if "-map_metadata" in args:
                args = list(args)
                args.insert(args.index("-i"), "-re")
            return await original(args, **kwargs)

        with patch.object(self.runtime.runner, "run", side_effect=realtime):
            task = asyncio.create_task(
                self.runtime.build(sources=[self.source], output=self.root / "ffmpeg.zip", work_root=self.root)
            )
            for _ in range(1000):
                if list(self.root.glob("dataset-*/source01.pcm")):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(list(self.root.glob("dataset-*/source01.pcm")))
            processes = tuple(self.runtime.runner.processes)
            self.assertTrue(processes)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(p.returncode is not None for p in processes))
        self.assertFalse(list(self.root.glob("dataset-*")))
        self.assertFalse((self.root / "ffmpeg.zip").exists())

    async def test_loudnorm_changes_level_and_keeps_original(self):
        plain = await self.build()
        normalized = await self.build(options=Options(normalize_volume=True))
        self.assertNotEqual(plain["slices"][0]["rms_dbfs"], normalized["slices"][0]["rms_dbfs"])
        self.assertTrue(normalized["options"]["normalize_volume"])

    async def test_silence_retained_as_flagged_not_recommended(self):
        fixture(self.audio, silence=True)
        self.digest = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        manifest = await self.build()
        self.assertEqual(manifest["stats"]["recommended_count"], 0)
        self.assertEqual(manifest["stats"]["slice_count"], 1)
        self.assertIn("empty_or_failed", manifest["slices"][0]["flags"])

    async def test_partial_failure_clean_requirement_and_budgets(self):
        missing = Source(self.root / "missing.wav", "audio_missing", "missing.wav", 2)
        manifest = await self.build(sources=[self.source, missing])
        self.assertEqual(manifest["stats"]["failed_source_count"], 1)
        result = await self.runtime.build(
            sources=[self.source],
            output=self.root / "clean.zip",
            options=Options(clean_first=True),
            work_root=self.root,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["sources"][0]["reason"], "dataset_cleaning_required")
        with patch("akane_voice_dataset.runtime.MAX_PCM_BYTES", 100):
            failed = await self.runtime.build(
                sources=[self.source], output=self.root / "limit.zip", work_root=self.root
            )
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["sources"][0]["reason"], "dataset_pcm_budget_exceeded")
        self.assertFalse(list(self.root.glob("dataset-*")))
        self.assertFalse((self.root / "clean.zip").exists())

    async def test_protected_bad_media_and_existing_output_are_honest(self):
        protected = self.root / "protected.ncm"
        protected.write_bytes(b"encrypted fixture")
        bad = self.root / "bad.wav"
        bad.write_bytes(b"not wav")
        for path, reason in ((protected, "protected_media_format"), (bad, "dataset_audio_invalid")):
            result = await self.runtime.build(
                sources=[Source(path, "audio_1", path.name, 1)], output=self.root / "bad.zip", work_root=self.root
            )
            self.assertEqual(result["sources"][0]["reason"], reason)
        with self.assertRaisesRegex(DatasetError, "dataset_output_exists"):
            await self.runtime.build(sources=[self.source], output=self.audio)
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), self.digest)

    async def test_video_uses_real_audio_clock(self):
        video = self.root / "video.mp4"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:r=1:d=8",
            "-i",
            str(self.audio),
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(video),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        self.assertEqual(await process.wait(), 0)
        manifest = await self.build(sources=[Source(video, "audio_1", "video.mp4", 1)])
        self.assertLessEqual(max(s["end_time"] for s in manifest["slices"]), 4.1)

    async def test_actual_pcm_worker_repeated_cancel_has_no_late_zip(self):
        # A long continuous PCM source keeps the real CPU slicing worker busy.
        fixture(self.audio, seconds=120, rate=16000)
        output = self.root / "cancel.zip"
        task = asyncio.create_task(
            self.runtime.build(
                sources=[self.source], output=output, work_root=self.root, options=Options(target_sr=96000)
            )
        )
        for _ in range(3000):
            if output.exists() and self.runtime.runner.processes:
                break
            if task.done():
                self.fail(f"worker finished before cancellation: {task.result()}")
            await asyncio.sleep(0.01)
        self.assertTrue(output.exists(), "actual archive worker did not start")
        processes = tuple(self.runtime.runner.processes)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(p.returncode is not None for p in processes))
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob("dataset-*")))

    async def test_real_process_timeout_and_unavailable_dependency(self):
        original = self.runtime.runner.run

        async def timeout(args, **kwargs):
            if any(str(a).endswith("worker.py") for a in args):
                kwargs["timeout"] = 0.001
            return await original(args, **kwargs)

        with patch.object(self.runtime.runner, "run", side_effect=timeout):
            with self.assertRaisesRegex(DatasetError, "dataset_timeout"):
                await self.runtime.build(sources=[self.source], output=self.root / "timeout.zip", work_root=self.root)
        self.assertFalse((self.root / "timeout.zip").exists())
        self.assertFalse(self.runtime.runner.processes)
        with patch.dict(os.environ, {"AKANE_MEDIA_FFMPEG": "missing-akane-ffmpeg"}):
            with self.assertRaisesRegex(DatasetError, "ffmpeg_not_found"):
                await self.runtime.probe()


if __name__ == "__main__":
    unittest.main()
