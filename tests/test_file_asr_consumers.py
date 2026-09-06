"""Real speech through preserved voice API and local service package bindings."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from companion_v01.routes.voice import run_asr_transcription
from scripts.akane_local_capability_host import LocalAsrRuntime, create_app
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]


class AsrConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.speech = Path(cls.temp.name) / "speech.wav"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(ROOT / "plugins/akane_file_transcription/tests/synthesize_fixture.ps1"),
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
        cls.temp.cleanup()

    def test_missing_business_library_degrades_without_crashing_service_startup(self):
        with patch(
            "scripts.akane_local_capability_host.asr_business_module", side_effect=RuntimeError("asr_business_missing")
        ):
            runtime = LocalAsrRuntime(ffmpeg_path=Path(shutil.which("ffmpeg")), cache_dir=None, default_model="tiny")
        self.assertFalse(runtime.ready)
        self.assertEqual(runtime.public_status()["reason"], "asr_business_missing")

    def test_existing_realtime_voice_entry_uses_shared_model_and_pcm_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, files = services(Path(tmp))
            engine = SimpleNamespace(local_media_executor=None, _get_generated_file_service=lambda: files)
            config = SimpleNamespace(
                ASR_WHISPER_MODEL_SIZE="tiny",
                WHISPER_MODEL_SIZE="tiny",
                ASR_WHISPER_DEVICE="cpu",
                ASR_WHISPER_COMPUTE_TYPE="int8",
                ASR_LANGUAGE="en",
                ASR_VAD_FILTER=True,
                WHISPER_CACHE_DIR="",
            )
            before = self.speech.read_bytes()

            # The legacy blocking voice function also works inside a server's
            # active event loop: preparation drains rather than nesting run().
            async def request():
                return run_asr_transcription(
                    engine=engine,
                    config_module=config,
                    audio_bytes=before,
                    filename="speech.wav",
                    language="en",
                    content_type="audio/wav",
                )

            # Broken optional file-plugin settings must not break the existing
            # voice entry's explicitly configured preparation/model.
            with patch.dict("os.environ", {"AKANE_ASR_MODEL": "invalid-file-plugin-setting"}):
                result = asyncio.run(request())
            self.assertTrue(result["ok"], result)
            self.assertIn("hello world", result["text"].lower())
            self.assertIn("speech recognition", result["text"].lower())
            self.assertNotIn(str(Path(tmp)), str(result))
            first = files._load_faster_whisper_model(model_size="tiny", device="cpu", compute_type="int8")
            second = files._load_faster_whisper_model(model_size="tiny", device="cpu", compute_type="int8")
            self.assertIs(first, second)
            self.assertEqual(self.speech.read_bytes(), before)
            self.assertFalse(hasattr(files, "transcribe_media"))

    def test_real_asgi_asr_protocol_returns_actual_recognized_words(self):
        unavailable_separation = SimpleNamespace(ready=False, public_status=lambda: {"ready": False})
        with patch("scripts.akane_local_capability_host.LocalDemucsRuntime", return_value=unavailable_separation):
            app = create_app(
                ffmpeg_path=Path(shutil.which("ffmpeg")),
                whisper_cache_dir=None,
                whisper_model="tiny",
                rvc_base_url="http://127.0.0.1:9",
                rvc_root_dir=None,
                separation_model="HP5_only_main_vocal",
            )
        response = TestClient(app).post(
            "/v1/audio/transcriptions",
            files={"file": ("speech.wav", self.speech.read_bytes(), "audio/wav")},
            data={"model": "tiny", "language": "en", "vad_filter": "true"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("hello world", payload["text"].lower())
        self.assertEqual(payload["model"], "tiny")
        self.assertTrue(payload["segments"])
        self.assertTrue(all(0 <= s["start"] <= s["end"] <= payload["duration"] for s in payload["segments"]))
        self.assertNotIn(str(self.speech.parent), str(payload))


if __name__ == "__main__":
    unittest.main()
