"""Cancellable FFmpeg preparation and a single owned slicing/archive worker."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from companion_v01.plugin_subprocess import PluginProcessRunner
from .options import DatasetError, MAX_DURATION, MAX_PCM_BYTES, Options


INPUT_OPTIONS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv",
)
PROTECTED = {"kgm", "kgma", "ncm", "qmc", "qmc0", "qmc3", "qmcflac", "mflac", "mgg", "tkm", "vpr", "uc"}


@dataclass(frozen=True)
class Source:
    path: Path
    handle: str
    title: str
    source_index: int
    cleaned: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.handle, str)
            or not 1 <= len(self.handle) <= 512
            or any(c in self.handle for c in "/\\\r\n\0")
            or not isinstance(self.title, str)
            or not 1 <= len(self.title) <= 255
            or any(c in self.title for c in "/\\\r\n\0")
            or isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or not 1 <= self.source_index <= 20
            or not isinstance(self.cleaned, bool)
        ):
            raise DatasetError("dataset_source_invalid")


class DatasetRuntime:
    def __init__(self):
        self.runner = PluginProcessRunner()

    def binary(self, name):
        path = shutil.which(os.environ.get("AKANE_MEDIA_" + name.upper(), "") or name)
        if not path:
            raise DatasetError(name + "_not_found")
        return path

    async def probe(self):
        try:
            for name in ("ffmpeg", "ffprobe"):
                code, _ = await self.runner.run([self.binary(name), "-version"], timeout=10)
                if code:
                    raise DatasetError(name + "_unavailable")
            return {"ok": True, "provider": "ffmpeg_pcm"}
        except (OSError, asyncio.TimeoutError):
            raise DatasetError("dataset_dependency_unavailable") from None

    async def prepare(self, source, destination, options):
        path = Path(source.path)
        if path.suffix.lower().lstrip(".") in PROTECTED:
            raise DatasetError("protected_media_format")
        if not path.is_file() or not path.stat().st_size:
            raise DatasetError("dataset_source_missing")
        if options.clean_first and not source.cleaned:
            raise DatasetError("dataset_cleaning_required")
        code, output = await self.runner.run(
            [
                self.binary("ffprobe"),
                "-v",
                "error",
                *INPUT_OPTIONS,
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration:format=duration",
                "-of",
                "json",
                path,
            ],
            timeout=15,
            capture=True,
        )
        try:
            payload = json.loads(output)
            streams = payload["streams"]
            duration = float(streams[0].get("duration") or payload.get("format", {}).get("duration"))
            if code or not math.isfinite(duration) or duration <= 0:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise DatasetError("dataset_audio_invalid") from None
        if duration > MAX_DURATION:
            raise DatasetError("dataset_duration_exceeded")
        if duration * options.target_sr * (1 if options.mono else 2) * 2 > MAX_PCM_BYTES:
            raise DatasetError("dataset_pcm_budget_exceeded")
        args = [
            self.binary("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            "-n",
            *INPUT_OPTIONS,
            "-i",
            path,
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
        ]
        if options.normalize_volume:
            args.extend(["-af", "loudnorm=I=-18:TP=-1.5:LRA=11"])
        # A dishonest container clock cannot decode indefinitely. Reject the
        # over-limit result instead of silently returning a truncated dataset.
        args.extend(
            [
                "-t",
                str(MAX_DURATION + 0.1),
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(options.target_sr),
                "-ac",
                "1" if options.mono else "2",
                "-f",
                "s16le",
                destination,
            ]
        )
        code, _ = await self.runner.run(args, timeout=900)
        if code or not destination.is_file() or not destination.stat().st_size:
            raise DatasetError("dataset_audio_prepare_failed")
        if destination.stat().st_size / (options.target_sr * (1 if options.mono else 2) * 2) > MAX_DURATION:
            raise DatasetError("dataset_duration_exceeded")

    async def build(self, *, sources, output, title="训练素材", options=None, failures=(), work_root=None):
        output, options = Path(output), options or Options()
        if output.exists() or any(Path(s.path).resolve() == output.resolve() for s in sources):
            raise DatasetError("dataset_output_exists")
        if (
            not 1 <= len(sources) <= 20
            or len(sources) + len(failures) > 20
            or not isinstance(title, str)
            or not 1 <= len(title) <= 120
            or any(c in title for c in "/\\\0\r\n")
            or len({s.source_index for s in sources}) != len(sources)
        ):
            raise DatasetError("dataset_request_invalid")
        seen = {s.source_index for s in sources}
        for failure in failures:
            if (
                not isinstance(failure, dict)
                or set(failure) - {"source_index", "handle", "title", "status", "slice_count", "reason"}
                or failure.get("status") != "failed"
                or failure.get("slice_count") != 0
                or not isinstance(failure.get("reason"), str)
                or not failure["reason"].replace("_", "").isalnum()
                or len(failure["reason"]) > 100
                or failure.get("source_index") in seen
            ):
                raise DatasetError("dataset_failure_invalid")
            Source(Path("unused"), failure.get("handle"), failure.get("title"), failure.get("source_index"))
            seen.add(failure["source_index"])
        succeeded = False
        try:
            with tempfile.TemporaryDirectory(prefix="dataset-", dir=work_root) as temporary:
                root = Path(temporary)
                prepared, errors, total = [], list(failures), 0
                for source in sources:
                    path = root / f"source{source.source_index:02d}.pcm"
                    try:
                        await self.prepare(source, path, options)
                    except DatasetError as exc:
                        errors.append(
                            {
                                "source_index": source.source_index,
                                "handle": source.handle,
                                "title": source.title,
                                "status": "failed",
                                "slice_count": 0,
                                "reason": str(exc),
                            }
                        )
                        path.unlink(missing_ok=True)
                        continue
                    total += path.stat().st_size
                    if total > MAX_PCM_BYTES:
                        raise DatasetError("dataset_pcm_budget_exceeded")
                    prepared.append(
                        {
                            "prepared": str(path),
                            "source_index": source.source_index,
                            "handle": source.handle,
                            "title": source.title,
                            "cleaned": source.cleaned,
                        }
                    )
                if not prepared:
                    return {"ok": False, "reason": "dataset_no_usable_sources", "sources": errors}
                request = root / "request.json"
                request.write_text(
                    json.dumps(
                        {
                            "sources": prepared,
                            "failures": errors,
                            "title": title,
                            "options": asdict(options),
                            "output": str(output),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                code, data = await self.runner.run(
                    [sys.executable, Path(__file__).with_name("worker.py"), request], capture=True, timeout=900
                )
                try:
                    result = json.loads(data)
                except (ValueError, TypeError):
                    raise DatasetError("dataset_worker_failed") from None
                if code or result.get("ok") is not True:
                    reason = result.get("reason")
                    allowed = {
                        "dataset_pcm_invalid",
                        "dataset_pcm_budget_exceeded",
                        "dataset_slice_budget_exceeded",
                        "dataset_output_too_large",
                        "dataset_no_slices_created",
                    }
                    raise DatasetError(reason if reason in allowed else "dataset_worker_failed")
                succeeded = True
                return {**result, "sources_failed": errors}
        except asyncio.TimeoutError:
            raise DatasetError("dataset_timeout") from None
        except OSError:
            raise DatasetError("dataset_io_failed") from None
        finally:
            if not succeeded:
                output.unlink(missing_ok=True)

    async def aclose(self):
        await self.runner.aclose()
