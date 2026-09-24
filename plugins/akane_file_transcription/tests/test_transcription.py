from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from akane_file_transcription.local import LocalTranscriber, Options, TranscriptionError
from akane_file_transcription.render import render, timestamp


class FormatTests(unittest.TestCase):
    def test_offsets_formats_timestamps_and_unicode(self):
        transcript = {
            "source": {"handle": "audio_1"},
            "duration_seconds": 2,
            "segments": [{"start": 0.125, "end": 1.999, "text": "你好 world"}],
            "text": "你好 world",
        }
        for fmt in ("md", "txt", "json", "srt", "vtt"):
            text = render([transcript, transcript], output_format=fmt, title="测试")
            self.assertIn("你好 world", text)
            if fmt in ("srt", "vtt"):
                self.assertIn("00:00:02" + ("." if fmt == "vtt" else ",") + "125 -->", text)
            if fmt == "json":
                self.assertEqual(len(json.loads(text)["transcripts"]), 2)
        self.assertNotIn("[", render([transcript], output_format="md", title="test", with_timestamps=False))
        self.assertEqual(timestamp(3599.9996, subtitle=True), "01:00:00,000")

    def test_options_reject_invalid_values_instead_of_silent_substitution(self):
        for values in (
            {"model_size": "unknown"},
            {"device": "fake"},
            {"compute_type": "fp8"},
            {"language": "x\nInjected"},
            {"vad_filter": "false"},
        ):
            with self.subTest(values=values), self.assertRaises(TranscriptionError):
                Options(**values)


@unittest.skipUnless(
    os.name == "nt" and shutil.which("ffmpeg") and importlib.util.find_spec("faster_whisper"),
    "Windows speech synthesis, FFmpeg and prepared faster-whisper required",
)
class RealTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.speech = Path(cls.fixture_dir.name) / "speech.wav"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(Path(__file__).with_name("synthesize_fixture.ps1")),
                "-OutputPath",
                str(cls.speech),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("speech_fixture_synthesis_failed")

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = LocalTranscriber(model="tiny", device="cpu", compute_type="int8")

    async def asyncTearDown(self):
        await self.runtime.aclose()
        self.tmp.cleanup()

    async def test_real_offline_speech_and_timestamps_vad(self):
        before = hashlib.sha256(self.speech.read_bytes()).hexdigest()
        probe = await self.runtime.probe()
        self.assertEqual(probe["model"], "tiny")
        for vad in (False, True):
            result = await self.runtime.transcribe(
                source=self.speech, options=Options(language="en", vad_filter=vad), work_root=self.root
            )
            self.assertIn("hello world", result["text"].lower())
            self.assertIn("speech recognition", result["text"].lower())
            self.assertEqual(result["device"], "cpu")
            self.assertEqual(result["model"], "tiny")
            self.assertTrue(all(0 <= s["start"] <= s["end"] <= result["duration_seconds"] for s in result["segments"]))
            self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(hashlib.sha256(self.speech.read_bytes()).hexdigest(), before)

    async def test_video_longer_than_audio_uses_audio_clock(self):
        video = self.root / "video.mp4"
        await self.runtime.runner.run(
            [
                self.runtime.binary("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=s=64x64:r=1:d=20",
                "-i",
                self.speech,
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                video,
            ]
        )
        result = await self.runtime.transcribe(source=video, options=Options(language="en"), work_root=self.root)
        self.assertIn("hello world", result["text"].lower())
        self.assertLess(result["duration_seconds"], 18)

    async def test_missing_model_and_invalid_runtime_are_honest(self):
        missing = LocalTranscriber(model="tiny", cache_dir=self.root / "empty-cache")
        bad = LocalTranscriber(model="tiny", python=self.root / "missing-python")
        try:
            with self.assertRaisesRegex(TranscriptionError, "^asr_model_missing$"):
                await missing.probe()
            with self.assertRaisesRegex(TranscriptionError, "^asr_python_not_found$"):
                await bad.probe()
        finally:
            await missing.aclose()
            await bad.aclose()

    async def test_silence_and_bad_media_do_not_become_success(self):
        silent = self.root / "silence.wav"
        with wave.open(str(silent), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 32000)
        with self.assertRaisesRegex(TranscriptionError, "^asr_no_speech$"):
            await self.runtime.transcribe(source=silent, work_root=self.root)
        bad = self.root / "bad.wav"
        bad.write_bytes(b"not audio")
        with self.assertRaisesRegex(TranscriptionError, "^asr_audio_prepare_failed$"):
            await self.runtime.transcribe(source=bad, work_root=self.root)
        playlist = self.root / "list.m3u"
        playlist.write_text(str(self.speech), encoding="utf-8")
        with self.assertRaisesRegex(TranscriptionError, "^asr_audio_prepare_failed$"):
            await self.runtime.transcribe(source=playlist, work_root=self.root)

    async def test_actual_model_cancel_waits_for_exit_and_removes_prepared_copy(self):
        long = self.root / "long.wav"
        with wave.open(str(self.speech), "rb") as source:
            params, pcm = source.getparams(), source.readframes(source.getnframes())
        with wave.open(str(long), "wb") as output:
            output.setparams(params)
            for _ in range(40):
                output.writeframesraw(pcm)
        running = asyncio.create_task(
            self.runtime.transcribe(source=long, options=Options(language="en", vad_filter=False), work_root=self.root)
        )
        for _ in range(600):
            if list(self.root.glob("asr-*/prepared.wav")):
                break
            await asyncio.sleep(0.05)
        self.assertTrue(list(self.root.glob("asr-*/prepared.wav")))
        await asyncio.sleep(2)
        self.assertFalse(running.done())
        processes = tuple(self.runtime.runner.processes)
        self.assertTrue(processes)
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertTrue(all(p.returncode is not None for p in processes))
        self.assertEqual(list(self.root.glob("asr-*")), [])
        self.assertTrue(long.is_file())


if __name__ == "__main__":
    unittest.main()
