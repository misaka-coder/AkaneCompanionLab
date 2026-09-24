from __future__ import annotations

import asyncio
import hashlib
import math
import os
import shutil
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from akane_media_convert import CAPABILITY_ID, MediaConverter, dependencies, stop_process
from akane_media_convert.conversion import ConversionError, FORMATS, Options, command, seconds
from companion_v01.plugin_api import PluginResourceResult


def write_audio(path: Path, *, silence: bool = False, duration: float = 4, sample_rate: int = 16000):
    samples = array("h")
    for index in range(int(duration * sample_rate)):
        t = index / sample_rate
        quiet = silence and (t < 1 or t >= duration - 1)
        sample = 0 if quiet else int(10000 * math.sin(2 * math.pi * 440 * t))
        samples.extend((sample, sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def read_samples(path: Path):
    with wave.open(str(path), "rb") as source:
        values = array("h", source.readframes(source.getnframes()))
        return values, source.getframerate(), source.getnchannels()


def rms(values):
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


class OptionTests(unittest.TestCase):
    def test_time_formats_and_rejection_are_explicit(self):
        for value in ("90", "1:30", "00:01:30", "1分30秒", "1m30s", "1.5min"):
            self.assertEqual(seconds(value, field="start_time"), 90)
        for value in ("nan", "inf", "-1", "1:2:3:4", "bad"):
            with self.assertRaises(ConversionError):
                seconds(value, field="start_time")
        for extra in (
            {"start_time": "5", "end_time": "2"},
            {"speed_ratio": 0.1},
            {"volume_gain_db": float("nan")},
            {"bitrate": "320k -i secret"},
            {"channels": 3},
        ):
            with self.assertRaises(ConversionError):
                Options.from_args({"output_format": "wav", **extra})

    def test_crop_duration_is_an_input_option_before_speed_filters(self):
        options = Options.from_args({"output_format": "wav", "start_time": "1", "end_time": "3", "speed_ratio": 2})
        argv = command("ffmpeg", Path("input.wav"), Path("output.wav"), options, source_duration=4)
        self.assertLess(argv.index("-t"), argv.index("-i"))
        self.assertEqual(argv[argv.index("-t") + 1], "2")
        self.assertIn("atempo=2", argv)


class DependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_creation_drains_real_child(self):
        started, release = asyncio.Event(), asyncio.Event()
        real_create = asyncio.create_subprocess_exec
        children = []

        async def delayed(*args, **kwargs):
            child = await real_create(*args, **kwargs)
            children.append(child)
            started.set()
            await release.wait()
            return child

        adapter = MediaConverter(None)
        try:
            with patch("asyncio.create_subprocess_exec", side_effect=delayed):
                task = asyncio.create_task(adapter._run([sys.executable, "-c", "import time; time.sleep(60)"]))
                await asyncio.wait_for(started.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            self.assertIsNotNone(children[0].returncode)
            self.assertFalse(adapter._processes)
        finally:
            release.set()
            await adapter.aclose()

    async def test_repeated_cancellation_still_drains_process_exit(self):
        terminated = asyncio.Event()
        exited = asyncio.Event()

        class Process:
            returncode = None

            def terminate(self):
                terminated.set()

            async def wait(self):
                await exited.wait()
                self.returncode = -1

        process = Process()
        task = asyncio.create_task(stop_process(process))
        await terminated.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        exited.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(process.returncode, -1)

    async def test_missing_dependency_never_reports_ready(self):
        adapter = MediaConverter(None)
        with patch.dict(os.environ, {"AKANE_MEDIA_FFMPEG": "akane-nonexistent-ffmpeg"}):
            result = await adapter.health()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "ffmpeg_not_found")
        await adapter.aclose()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe required")
class RealConversionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        write_audio(self.source)

        async def open_resource(target):
            return PluginResourceResult(True, "ready", path=self.source, handle=target, name="source.wav")

        self.adapter = MediaConverter(SimpleNamespace(open=open_resource))

    async def asyncTearDown(self):
        await self.adapter.aclose()
        self.temporary.cleanup()

    async def invoke(self, **args):
        return await self.adapter.invoke(
            CAPABILITY_ID, {"source_id": "audio_001", "output_format": "wav", **args}, None
        )

    async def test_all_declared_formats_produce_probeable_audio_without_modifying_input(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for output_format in FORMATS:
            with self.subTest(output_format=output_format):
                result = await self.invoke(output_format=output_format)
                self.assertFalse(result.is_error, result.reason)
                draft = result.content.artifacts[0]
                self.assertEqual(draft.output_format, output_format)
                self.assertGreater(draft.path.stat().st_size, 0)
                media = result.content.content.content["media_info"]
                self.assertAlmostEqual(media["duration_seconds"], 4, delta=0.3)
                self.assertEqual(media["channels"], 2)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    async def test_crop_speed_sample_rate_channels_gain_and_fades_reach_audio(self):
        result = await self.invoke(
            start_time="1",
            end_time="3",
            speed_ratio=2,
            sample_rate=8000,
            channels=1,
            volume_gain_db=-6,
            fade_in_seconds=0.1,
            fade_out_seconds=0.1,
        )
        self.assertFalse(result.is_error, result.reason)
        samples, rate, channels = read_samples(result.content.artifacts[0].path)
        self.assertEqual((rate, channels), (8000, 1))
        self.assertAlmostEqual(len(samples) / rate, 1, delta=0.06)
        middle = rms(samples[2000:5000])
        self.assertTrue(3000 < middle < 3900, middle)
        self.assertLess(rms(samples[:80]), middle / 5)
        self.assertLess(rms(samples[-80:]), middle / 3)

    async def test_silence_trim_and_fade_out_use_actual_trimmed_end(self):
        write_audio(self.source, silence=True)
        result = await self.invoke(trim_silence=True, fade_out_seconds=0.2)
        self.assertFalse(result.is_error, result.reason)
        samples, rate, channels = read_samples(result.content.artifacts[0].path)
        self.assertAlmostEqual(len(samples) / (rate * channels), 2, delta=0.1)
        self.assertLess(rms(samples[-160:]), rms(samples[rate : rate + 1000]) / 5)

    async def test_normalization_is_applied_and_report_matches_output(self):
        result = await self.invoke(normalize_volume=True, sample_rate=16000)
        self.assertFalse(result.is_error, result.reason)
        samples, rate, channels = read_samples(result.content.artifacts[0].path)
        original, _, _ = read_samples(self.source)
        self.assertLess(rms(samples), rms(original) * 0.9)
        self.assertEqual(result.content.content.content["media_info"]["sample_rate"], rate)
        self.assertEqual(result.content.content.content["media_info"]["channels"], channels)

    async def test_video_audio_extraction_and_playlist_rejection(self):
        ffmpeg, _ = dependencies()
        video = self.root / "source.mp4"
        code, _ = await self.adapter._run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=32x32:r=10",
                "-i",
                str(self.source),
                "-shortest",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(video),
            ]
        )
        self.assertEqual(code, 0)
        self.source = video
        result = await self.invoke()
        self.assertFalse(result.is_error, result.reason)
        self.assertAlmostEqual(result.content.content.content["media_info"]["duration_seconds"], 4, delta=0.3)
        playlist = self.root / "input.m3u8"
        playlist.write_text(
            f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n{video.as_posix()}\n#EXT-X-ENDLIST\n", encoding="utf-8"
        )
        self.source = playlist
        result = await self.invoke()
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason, "media_probe_failed")

    async def test_real_conversion_cancel_waits_for_process_and_removes_partial_output(self):
        write_audio(self.source, duration=20)
        real_command = command

        def realtime_command(*args, **kwargs):
            argv = real_command(*args, **kwargs)
            argv.insert(argv.index("-i"), "-re")
            return argv

        with patch("akane_media_convert.command", realtime_command):
            task = asyncio.create_task(self.invoke())
            for _ in range(500):
                if list(self.root.glob("converted-*.wav")):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(list(self.root.glob("converted-*.wav")))
            processes = tuple(self.adapter._processes)
            self.assertTrue(processes)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            self.assertTrue(all(process.returncode is not None for process in processes))
            self.assertFalse(self.adapter._processes)
            self.assertEqual(list(self.root.glob("converted-*")), [])

    async def test_execution_timeout_is_structured_and_removes_output(self):
        real_run = self.adapter.runner.run

        async def timeout_conversion(argv, **kwargs):
            if any(str(arg).startswith("pcm_") for arg in argv):
                Path(argv[-1]).write_bytes(b"partial")
                raise asyncio.TimeoutError
            return await real_run(argv, **kwargs)

        with patch.object(self.adapter.runner, "run", side_effect=timeout_conversion):
            result = await self.invoke()
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason, "media_execution_timeout")
        self.assertEqual(result.status, "timeout")
        self.assertEqual(list(self.root.glob("converted-*")), [])


if __name__ == "__main__":
    unittest.main()
