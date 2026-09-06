from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from array import array


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from akane_audio_separation.local import LocalDemucs, LocalSeparationError
from akane_audio_separation.process import ProcessRunner
from akane_audio_separation.media import MediaTools, executable


def write_input(path, *, seconds=2):
    second = array("h")
    for n in range(44100):
        t = n / 44100
        left = int(7000 * math.sin(2 * math.pi * 220 * t) + 4000 * math.sin(2 * math.pi * 880 * t))
        right = int(6000 * math.sin(2 * math.pi * 330 * t) + 5000 * math.sin(2 * math.pi * 1100 * t))
        second.extend((left, right))
    if sys.byteorder != "little":
        second.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        for _ in range(seconds):
            output.writeframes(second.tobytes())


def audio(path):
    with wave.open(str(path), "rb") as source:
        info = (source.getnchannels(), source.getframerate(), source.getnframes())
        values = array("h", source.readframes(source.getnframes()))
        if sys.byteorder != "little":
            values.byteswap()
        return info, values


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_child_and_repeated_cancel_are_drained(self):
        runner = ProcessRunner()
        task = asyncio.create_task(runner.run([sys.executable, "-c", "import time; time.sleep(60)"]))
        while not runner.processes:
            await asyncio.sleep(0.01)
        process = next(iter(runner.processes))
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.returncode)
        self.assertFalse(runner.processes)
        await runner.aclose()

    async def test_cancel_during_creation_does_not_orphan_child(self):
        started, release = asyncio.Event(), asyncio.Event()
        real_create = asyncio.create_subprocess_exec
        children = []

        async def delayed(*args, **kwargs):
            child = await real_create(*args, **kwargs)
            children.append(child)
            started.set()
            await release.wait()
            return child

        runner = ProcessRunner()
        with patch("asyncio.create_subprocess_exec", side_effect=delayed):
            task = asyncio.create_task(runner.run([sys.executable, "-c", "import time; time.sleep(60)"]))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(children[0].returncode)
        await runner.aclose()

    async def test_close_waits_for_creation_and_real_exit(self):
        started, release = asyncio.Event(), asyncio.Event()
        real_create = asyncio.create_subprocess_exec
        children = []

        async def delayed(*args, **kwargs):
            child = await real_create(*args, **kwargs)
            children.append(child)
            started.set()
            await release.wait()
            return child

        runner = ProcessRunner()
        with patch("asyncio.create_subprocess_exec", side_effect=delayed):
            task = asyncio.create_task(runner.run([sys.executable, "-c", "import time; time.sleep(60)"]))
            await started.wait()
            closing = asyncio.create_task(runner.aclose())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            await closing
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaisesRegex(RuntimeError, "runner_closed"):
            await runner.run([sys.executable, "-c", "pass"])

    async def test_timeout_drains_and_runner_remains_usable(self):
        runner = ProcessRunner()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await runner.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.1)
            self.assertFalse(runner.processes)
            code, output = await runner.run([sys.executable, "-c", "print('ok')"], capture=True)
            self.assertEqual(code, 0)
            self.assertEqual(output.strip(), b"ok")
        finally:
            await runner.aclose()

    async def test_missing_interpreter_and_model_are_not_ready(self):
        runtime = LocalDemucs(python="akane_missing_demucs_python")
        with self.assertRaisesRegex(LocalSeparationError, "demucs_python_not_found"):
            await runtime.probe()
        await runtime.aclose()
        if importlib.util.find_spec("demucs") is not None:
            with tempfile.TemporaryDirectory() as tmp:
                runtime = LocalDemucs(model_root=Path(tmp))
                try:
                    with self.assertRaisesRegex(LocalSeparationError, "demucs_model_missing"):
                        await runtime.probe()
                    self.assertEqual(list(Path(tmp).iterdir()), [])
                finally:
                    await runtime.aclose()

    async def test_worker_failures_do_not_expose_paths_or_download_weights(self):
        if importlib.util.find_spec("demucs") is None:
            self.skipTest("Demucs runtime required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Known htdemucs signature, deliberately invalid hash/content.
            checkpoint = root / "955717e8-00000000.th"
            checkpoint.write_bytes(b"corrupt checkpoint")
            runtime = LocalDemucs(model_root=root)
            try:
                with self.assertRaisesRegex(LocalSeparationError, "^demucs_model_unavailable$"):
                    await runtime.probe()
                self.assertEqual(list(root.iterdir()), [checkpoint])
                self.assertEqual(checkpoint.read_bytes(), b"corrupt checkpoint")
            finally:
                await runtime.aclose()

    async def test_invalid_explicit_package_root_is_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LocalDemucs(package_root=tmp)
            try:
                with self.assertRaisesRegex(LocalSeparationError, "^demucs_package_root_invalid$"):
                    await runtime.probe()
            finally:
                await runtime.aclose()


@unittest.skipUnless(importlib.util.find_spec("demucs") is not None, "Demucs runtime required")
class RealDemucsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = LocalDemucs(device="cpu")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        try:
            self.info = await self.runtime.probe()
        except LocalSeparationError as exc:
            await self.runtime.aclose()
            self.temporary.cleanup()
            self.skipTest(f"Prepared offline Demucs runtime required: {exc}")

    async def asyncTearDown(self):
        await self.runtime.aclose()
        self.temporary.cleanup()

    async def test_real_model_returns_distinct_valid_stems_without_mutating_input(self):
        source = self.root / "source.wav"
        write_input(source)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        result = await self.runtime.separate(source=source, output_root=self.root / "stems")
        self.assertEqual(result["device_used"], "cpu")
        self.assertEqual(result["frames"], 88200)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        source_info, input_samples = audio(source)
        tracks = []
        for name in ("vocals", "instrumental"):
            info, samples = audio(self.root / "stems" / f"{name}.wav")
            self.assertEqual(info, source_info)
            self.assertGreater(max(map(abs, samples)), 0)
            self.assertNotEqual(samples, input_samples)
            tracks.append(samples)
        self.assertNotEqual(tracks[0], tracks[1])
        # Neither result is merely a channel copied into both output channels.
        self.assertTrue(any(a != b for a, b in zip(tracks[1][::2], tracks[1][1::2])))

    async def test_real_ml_worker_cancel_exits_before_acknowledgement(self):
        source = self.root / "long.wav"
        write_input(source, seconds=120)
        task = asyncio.create_task(self.runtime.separate(source=source, output_root=self.root / "stems"))
        while not self.runtime.runner.processes:
            await asyncio.sleep(0.01)
        process = next(iter(self.runtime.runner.processes))
        await asyncio.sleep(5)
        self.assertIsNone(process.returncode)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.runtime.runner.processes)
        self.assertFalse((self.root / "stems" / "vocals.wav").exists())

    async def test_wrong_pcm_is_structured_failure_without_stems(self):
        source = self.root / "wrong.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 8000)
        with self.assertRaisesRegex(LocalSeparationError, "separation_input_pcm_invalid"):
            await self.runtime.separate(source=source, output_root=self.root / "stems")
        self.assertFalse((self.root / "stems").exists())

    async def test_media_pipeline_timeout_drains_ml_and_removes_prepared_file(self):
        source = self.root / "long.wav"
        write_input(source, seconds=120)
        with self.assertRaises(asyncio.TimeoutError):
            await self.runtime.separate_media(
                source=source, output_root=self.root / "stems", model_info=self.info, timeout=4
            )
        self.assertFalse(self.runtime.runner.processes)
        self.assertEqual(list((self.root / "stems").iterdir()), [])

    async def test_video_with_longer_picture_uses_real_audio_duration(self):
        source = self.root / "source.wav"
        video = self.root / "source.mp4"
        write_input(source)
        code, _ = await self.runtime.runner.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x32:r=10:d=4",
                "-i",
                str(source),
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(video),
            ],
            timeout=30,
        )
        self.assertEqual(code, 0)
        before = video.read_bytes()
        media = MediaTools()
        try:
            info = await media.probe_audio(video)
            self.assertAlmostEqual(info["duration_seconds"], 2, delta=0.06)
            result = await self.runtime.separate_media(
                source=video, output_root=self.root / "stems", model_info=self.info
            )
        finally:
            await media.aclose()
        self.assertAlmostEqual(result["input_media"]["duration_seconds"], 2, delta=0.06)
        self.assertAlmostEqual(audio(self.root / "stems/vocals.wav")[0][2] / 44100, 2, delta=0.06)
        self.assertEqual(video.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
