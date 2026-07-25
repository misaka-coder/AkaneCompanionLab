from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.akane_local_capability_host import LocalDemucsRuntime


class LocalMediaCapabilityHostTests(unittest.TestCase):
    def test_external_demucs_runtime_reports_cuda_and_returns_worker_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            python_path = root / "python.exe"
            python_path.write_bytes(b"python")
            package_root = root / "packages"
            (package_root / "demucs").mkdir(parents=True)
            source = root / "song.mp3"
            source.write_bytes(b"audio")
            output_root = root / "stems"

            def fake_run(command, **_kwargs):
                if "--probe" in command:
                    payload = {
                        "ok": True,
                        "cuda_available": True,
                        "device": "Test GPU",
                    }
                else:
                    output_root.mkdir(parents=True, exist_ok=True)
                    output_root.joinpath("vocals.wav").write_bytes(b"vocals")
                    output_root.joinpath("instrumental.wav").write_bytes(b"instrumental")
                    payload = {"ok": True, "device": "cuda", "seconds": 1.25}
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(payload),
                    stderr="",
                )

            with (
                patch("scripts.akane_local_capability_host.subprocess.run", side_effect=fake_run),
                patch("scripts.akane_local_capability_host.importlib.util.find_spec", return_value=None),
            ):
                runtime = LocalDemucsRuntime(
                    python_path=python_path,
                    package_root=package_root,
                )
                status = runtime.public_status()
                stems = runtime.separate(
                    source_path=source,
                    output_root=output_root,
                    model="htdemucs",
                )
                self.assertTrue(status["ready"])
                self.assertEqual(status["executor"], "isolated_cuda")
                self.assertEqual(status["device"], "cuda")
                self.assertEqual(stems["device_used"], "cuda")
                self.assertEqual(stems["vocals"].read_bytes(), b"vocals")
                self.assertEqual(stems["instrumental"].read_bytes(), b"instrumental")

    def test_missing_external_and_in_process_demucs_is_structured_unavailable(self) -> None:
        with patch("scripts.akane_local_capability_host.importlib.util.find_spec", return_value=None):
            runtime = LocalDemucsRuntime(
                python_path=None,
                package_root=None,
            )

        status = runtime.public_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["reason"], "demucs_cuda_runtime_not_configured")


if __name__ == "__main__":
    unittest.main()
