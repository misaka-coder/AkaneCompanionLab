from __future__ import annotations

import json
import asyncio
import sys
import io
import shutil
import zipfile
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.akane_local_capability_host import LocalDemucsRuntime, _cover_media, create_app
from companion_v01.plugin_subprocess import PluginProcessRunner


class LocalMediaCapabilityHostTests(unittest.TestCase):
    def test_external_runtime_uses_package_and_closes_each_operation(self) -> None:
        calls = []

        class PackageRuntime:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            async def probe(self):
                return {"ok": True, "cuda_available": True, "sample_rate": 44100, "channels": 2}

            async def separate_media(self, **kwargs):
                calls.append(("separate", kwargs))
                root = kwargs["output_root"]
                root.mkdir()
                (root / "vocals.wav").write_bytes(b"fixture vocals")
                (root / "instrumental.wav").write_bytes(b"fixture instrumental")
                return {"ok": True, "device_used": "cuda"}

            async def aclose(self):
                calls.append(("close", {}))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("scripts.akane_local_capability_host.local_demucs_class", return_value=PackageRuntime):
                runtime = LocalDemucsRuntime(python_path=root / "python.exe", package_root=root / "packages")
                stems = runtime.separate(
                    source_path=root / "source.mp3", output_root=root / "stems", model="htdemucs_ft"
                )
            self.assertEqual(runtime.public_status()["executor"], "isolated_cuda")
            self.assertEqual(stems["vocals"].read_bytes(), b"fixture vocals")
            self.assertEqual(stems["device_used"], "cuda")
            self.assertEqual([name for name, _ in calls], ["init", "close", "init", "separate", "close"])
            self.assertEqual(calls[2][1]["model"], "htdemucs_ft")
            self.assertEqual(calls[2][1]["python"], root / "python.exe")
            self.assertEqual(calls[2][1]["package_root"], root / "packages")
            self.assertIsNone(calls[3][1]["model_info"])
            runtime.separate(source_path=root / "source.mp3", output_root=root / "stems-default", model="htdemucs")
            self.assertEqual(calls[-2][1]["model_info"]["sample_rate"], 44100)
            self.assertEqual(calls[-2][1]["model_info"]["channels"], 2)

    def test_missing_package_is_structured_unavailable(self) -> None:
        with patch("scripts.akane_local_capability_host.local_demucs_class", side_effect=RuntimeError("missing")):
            runtime = LocalDemucsRuntime(python_path=None, package_root=None)
        self.assertFalse(runtime.ready)
        self.assertEqual(runtime.public_status()["reason"], "separation_package_unavailable")
        with self.assertRaisesRegex(RuntimeError, "separation_package_unavailable"):
            runtime.separate(source_path=Path("source"), output_root=Path("outputs"), model="htdemucs")

    def test_external_failure_falls_back_to_same_package_with_current_python(self):
        calls = []

        class PackageRuntime:
            def __init__(self, **kwargs):
                self.python = kwargs["python"]
                calls.append(self.python)

            async def probe(self):
                if self.python != sys.executable:
                    raise RuntimeError("demucs_python_not_found")
                return {"ok": True, "cuda_available": False}

            async def aclose(self):
                pass

        with patch("scripts.akane_local_capability_host.local_demucs_class", return_value=PackageRuntime):
            runtime = LocalDemucsRuntime(python_path=Path("missing-python"), package_root=None)
        self.assertEqual(calls, [Path("missing-python"), sys.executable])
        self.assertTrue(runtime.ready)
        self.assertEqual(runtime.public_status()["fallback_reason"], "demucs_python_not_found")

    def test_real_service_and_cli_use_offline_package_on_compressed_input(self):
        from plugins.akane_audio_separation.tests.test_local_runtime import audio, write_input

        runtime = LocalDemucsRuntime(python_path=None, package_root=None)
        if not runtime.ready:
            self.skipTest("Prepared offline Demucs runtime required: " + runtime.public_status()["reason"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.wav"
            compressed = root / "source.flac"
            write_input(source)
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), str(compressed)], check=True)
            before = compressed.read_bytes()

            # Also exercises the legacy synchronous binding inside an async route.
            async def in_route():
                return runtime.separate(source_path=compressed, output_root=root / "service", model="htdemucs")

            stems = asyncio.run(in_route())
            self.assertEqual(stems["device_used"], "cpu")
            self.assertEqual(audio(stems["vocals"])[0], (2, 44100, 88200))
            self.assertNotEqual(audio(stems["vocals"])[1], audio(stems["instrumental"])[1])
            self.assertEqual(compressed.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in (root / "service").iterdir()), ["instrumental.wav", "vocals.wav"])
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/akane_demucs_worker.py",
                    "--source",
                    str(compressed),
                    "--output-root",
                    str(root / "cli"),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertNotIn(str(root), completed.stdout)
            self.assertEqual(audio(root / "cli/vocals.wav")[0], (2, 44100, 88200))
            self.assertNotEqual(audio(root / "cli/vocals.wav")[1], audio(root / "cli/instrumental.wav")[1])
            from fastapi.testclient import TestClient

            app = create_app(
                ffmpeg_path=Path(shutil.which("ffmpeg")),
                whisper_cache_dir=None,
                whisper_model="small",
                rvc_base_url="http://127.0.0.1:9",
                rvc_root_dir=None,
                separation_model="HP5_only_main_vocal",
            )
            with TestClient(app) as client:
                with compressed.open("rb") as upload:
                    response = client.post(
                        "/v1/audio/separate",
                        files={"file": ("source.flac", upload, "audio/flac")},
                        data={"model": "htdemucs", "output_format": "wav"},
                    )
            self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
            timings = json.loads(response.headers["X-Akane-Media-Timings"])
            self.assertIn(timings["device"], {"cpu", "cuda"})
            self.assertNotIn(str(root), response.headers["X-Akane-Media-Timings"])
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertEqual(archive.namelist(), ["vocals.wav", "instrumental.wav"])
                signals = []
                for name in archive.namelist():
                    import wave

                    with wave.open(io.BytesIO(archive.read(name)), "rb") as output:
                        self.assertEqual(
                            (output.getframerate(), output.getnchannels(), output.getnframes()), (44100, 2, 88200)
                        )
                        signals.append(output.readframes(output.getnframes()))
                self.assertNotEqual(signals[0], signals[1])
            self.assertEqual(compressed.read_bytes(), before)

    def test_local_cover_mix_uses_real_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, output = root / "source.wav", root / "cover.mp3"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", str(source)],
                check=True,
            )

            async def mix():
                runner = PluginProcessRunner()
                try:
                    media = _cover_media(Path(shutil.which("ffmpeg")), runner)
                    await media.mix(
                        converted_vocals=source,
                        instrumental=source,
                        output_path=output,
                        output_format="mp3",
                        vocal_gain_db=0,
                        instrumental_gain_db=-1,
                    )
                    return await media.probe_duration(output)
                finally:
                    await runner.aclose()

            duration = asyncio.run(mix())
            self.assertGreater(output.stat().st_size, 1000)
            self.assertAlmostEqual(duration, 0.4, delta=0.06)


if __name__ == "__main__":
    unittest.main()
