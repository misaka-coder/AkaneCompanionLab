"""Local ML runtime selection and real, offline dependency probing."""

from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .media import MediaTools
from .process import ProcessRunner


class LocalSeparationError(RuntimeError):
    pass


class LocalDemucs:
    def __init__(self, *, python=None, model="htdemucs", model_root=None, device="auto", package_root=None):
        if model not in ("htdemucs", "htdemucs_ft"):
            raise LocalSeparationError("separation_model_not_supported")
        if device not in ("auto", "cpu", "cuda"):
            raise LocalSeparationError("separation_device_invalid")
        self.python = python or os.environ.get("AKANE_SEPARATION_PYTHON", "").strip() or sys.executable
        self.model = model
        self.model_root = model_root or os.environ.get("AKANE_SEPARATION_MODEL_ROOT", "").strip()
        self.device = device
        self.package_root = (
            package_root if package_root is not None else os.environ.get("AKANE_SEPARATION_PACKAGE_ROOT", "").strip()
        )
        self.runner = ProcessRunner()

    def command(self):
        python = shutil.which(str(self.python))
        if not python:
            raise LocalSeparationError("demucs_python_not_found")
        args = [python, str(Path(__file__).with_name("worker.py")), "--model", self.model]
        if self.model_root:
            args.extend(("--model-root", str(self.model_root)))
        if self.package_root:
            args.extend(("--package-root", str(self.package_root)))
        return args

    async def _execute(self, *args, timeout):
        try:
            code, output = await self.runner.run([*self.command(), *args], capture=True, timeout=timeout)
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError
        except (OSError, ValueError):
            raise LocalSeparationError("demucs_worker_unavailable") from None
        if code or not result.get("ok"):
            reason = result.get("reason")
            allowed = {
                "demucs_runtime_incompatible",
                "demucs_package_root_invalid",
                "demucs_model_missing",
                "demucs_model_unavailable",
                "demucs_model_invalid",
                "demucs_execution_failed",
                "demucs_inference_failed",
                "separation_input_pcm_invalid",
                "separation_output_invalid",
                "separation_cuda_unavailable",
            }
            raise LocalSeparationError(reason if reason in allowed else "demucs_execution_failed")
        return result

    async def probe(self):
        info = await self._execute("--probe", timeout=60)
        if self.device == "cuda" and not info.get("cuda_available"):
            raise LocalSeparationError("separation_cuda_unavailable")
        return info

    async def separate(self, *, source: Path, output_root: Path):
        return await self._execute(
            "--source", str(source), "--output-root", str(output_root), "--device", self.device, timeout=1800
        )

    async def aclose(self):
        await self.runner.aclose()

    async def separate_media(
        self, *, source: Path, output_root: Path, model_info=None, timeout=1800, ffmpeg=None, ffprobe=None
    ):
        """Single preparation/inference path shared by SDK and service callers."""
        media = MediaTools(ffmpeg=ffmpeg, ffprobe=ffprobe)
        try:
            async with asyncio.timeout(timeout):
                info = model_info or await self.probe()
                output_root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="prepared-", dir=output_root) as tmp:
                    prepared = Path(tmp) / "input.wav"
                    await media.encode(
                        source, prepared, "wav", sample_rate=info["sample_rate"], channels=info["channels"]
                    )
                    input_media = await media.probe_audio(prepared)
                    result = await self.separate(source=prepared, output_root=output_root)
                    return {**result, "input_media": input_media}
        finally:
            await media.aclose()
