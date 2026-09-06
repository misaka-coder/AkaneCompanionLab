"""ASGI service integration: real media/pipeline, explicit ML provider doubles."""

import asyncio
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import UploadFile

from scripts import akane_local_capability_host as host


class RvcFixture:
    instances = []
    started = threading.Event()
    release = threading.Event()
    converted_sources = []
    fail = False

    def __init__(self, **kwargs):
        self.cancelled = kwargs.get("cancelled", lambda: False)
        self.separation_model = kwargs["separation_model"]
        self.instances.append(self)

    def resolve_voice_model(self, *args, **kwargs):
        return "Fixture.pth"

    def model_fingerprint(self, model):
        return {"model": model}

    def convert_voice(self, *, source_path, output_path, **kwargs):
        self.converted_sources.append(source_path)
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("fixture_timeout")
        if self.fail:
            raise host.CoverSongError(
                stage="provider", reason="fixture_inference_failed", public_message="测试推理失败。"
            )
        shutil.copyfile(source_path, output_path)
        return {"timings": {"voice_synthesis": 0.1}}

    def separate_vocals(self, *, source_path, work_dir):
        return DemucsFixture().paths(source_path, work_dir)


class DemucsFixture:
    ready = True
    count = 0

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def paths(source, directory):
        directory.mkdir(parents=True, exist_ok=True)
        paths = directory / "vocals.wav", directory / "instrumental.wav"
        for path in paths:
            shutil.copyfile(source, path)
        return paths

    def separate(self, *, source_path, output_root, model):
        type(self).count += 1
        vocals, instrumental = self.paths(source_path, output_root)
        return {"vocals": vocals, "instrumental": instrumental}


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ffmpeg = shutil.which("ffmpeg")
        self.assertTrue(self.ffmpeg, "Real ffmpeg required")
        self.source = self.root / "source.wav"
        subprocess.run(
            [self.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=0.4", str(self.source)],
            check=True,
            capture_output=True,
        )
        RvcFixture.instances, RvcFixture.converted_sources, RvcFixture.fail = [], [], False
        RvcFixture.started.clear()
        RvcFixture.release.set()
        DemucsFixture.count = 0
        self.patches = [
            patch.object(host, "RvcWebUiProvider", RvcFixture),
            patch.object(host, "LocalAsrRuntime", lambda **kwargs: None),
            patch.object(host, "LocalDemucsRuntime", DemucsFixture),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = host.create_app(
            ffmpeg_path=Path(self.ffmpeg),
            whisper_cache_dir=None,
            whisper_model="small",
            rvc_base_url="http://127.0.0.1:9",
            rvc_root_dir=None,
            separation_model="default-uvr",
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def post(self, **data):
        return await self.client.post(
            "/v1/rvc/cover",
            files={"file": ("source.wav", self.source.read_bytes(), "audio/wav")},
            data={"model_name": "Fixture.pth", **data},
        )

    async def test_real_service_pipeline_three_formats_and_no_server_cache(self):
        for format in ("wav", "flac", "mp3"):
            response = await self.post(output_format=format)
            self.assertEqual(response.status_code, 200, response.text[:200] if response.status_code != 200 else "")
            output = self.root / f"output.{format}"
            output.write_bytes(response.content)
            probe = subprocess.run(
                [
                    shutil.which("ffprobe"),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
            self.assertAlmostEqual(float(json.loads(probe.stdout)["format"]["duration"]), 0.4, delta=0.06)
            seconds = json.loads(response.headers["X-Akane-Cover-Timings"])
            self.assertIn("mix", seconds)
            self.assertNotIn("decode", seconds)  # Demucs owns the single decode.
            self.assertEqual(seconds["rvc_voice_synthesis"], 0.1)
            self.assertFalse(RvcFixture.converted_sources[-1].exists())
        self.assertEqual(DemucsFixture.count, 3)

    async def test_invalid_options_and_failed_inference_are_not_audio(self):
        response = await self.post(protect="0.8")
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"]["reason"], "cover_options_invalid")
        self.assertEqual(DemucsFixture.count, 0)
        RvcFixture.fail = True
        response = await self.post(output_format="wav")
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"]["reason"], "fixture_inference_failed")
        self.assertFalse(RvcFixture.converted_sources[-1].exists())

    async def test_asgi_cancellation_drains_conversion_before_work_cleanup(self):
        RvcFixture.release.clear()
        task = asyncio.create_task(self.post(output_format="wav"))
        self.assertTrue(await asyncio.to_thread(RvcFixture.started.wait, 3))
        source = RvcFixture.converted_sources[-1]
        task.cancel()
        await asyncio.sleep(0.03)
        task.cancel()
        await asyncio.sleep(0.03)
        self.assertFalse(task.done())
        self.assertTrue(source.is_file())
        RvcFixture.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(source.exists())

    async def test_uvr_route_uses_request_scoped_model(self):
        # Call the route with real UploadFile instances, avoiding shared provider mutation.
        route = next(route.endpoint for route in self.app.routes if route.path == "/v1/rvc/separate")
        response = await route(
            file=UploadFile(filename="source.wav", file=io.BytesIO(self.source.read_bytes())),
            requested_separation_model="custom-uvr",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RvcFixture.instances[0].separation_model, "default-uvr")
        self.assertEqual(RvcFixture.instances[-1].separation_model, "custom-uvr")
