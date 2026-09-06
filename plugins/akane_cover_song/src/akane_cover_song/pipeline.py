"""Cover orchestration without host resources, Jobs, prompts or delivery."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import shutil
import threading
import time

from .cache import CoverCache, cache_key
from .errors import CoverSongError


PIPELINE_VERSION = "rvc-cover-v3"
STEM_VERSION = "rvc-cover-stems-v3"
PROTECTED = {"kgm", "kgma", "mflac", "mgg", "ncm", "qmc", "qmc0", "qmc3", "qmcflac", "tkm", "vpr", "uc"}


def error(stage, reason, message="翻唱处理未完成。"):
    return CoverSongError(stage=stage, reason=reason, public_message=message)


@dataclass(frozen=True)
class CoverOptions:
    pitch_shift: int = 0
    index_rate: float = 0.6
    filter_radius: int = 3
    rms_mix_rate: float = 0.25
    protect: float = 0.33
    vocal_gain_db: float = 0.0
    instrumental_gain_db: float = -1.0

    def __post_init__(self):
        for name, lower, upper in (("pitch_shift", -24, 24), ("filter_radius", 0, 7)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise error("options", "cover_options_invalid")
        for name, lower, upper in (
            ("index_rate", 0, 1),
            ("rms_mix_rate", 0, 1),
            ("protect", 0, 0.5),
            ("vocal_gain_db", -12, 12),
            ("instrumental_gain_db", -12, 6),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise error("options", "cover_options_invalid")


class ProviderCalls:
    """Own and drain one invocation's blocking provider calls.

    Pass ``cancelled`` to RvcWebUiProvider at construction. A cancelled await
    does not release input/work files while its thread is still in WebUI.
    Unconfirmed transport errors retain their failure identity.
    """

    def __init__(self):
        self.cancel_event = threading.Event()

    def cancelled(self):
        return self.cancel_event.is_set()

    async def call(self, function, *args, **kwargs):
        if self.cancelled():
            raise asyncio.CancelledError
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        was_cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                was_cancelled = True
                self.cancel_event.set()
            except Exception:
                # Retrieve the exact failure below; never report uncertain
                # remote completion as a confirmed user cancellation.
                break
        try:
            result = task.result()
        except CoverSongError as exc:
            if exc.reason == "operation_cancelled":
                raise asyncio.CancelledError from None
            raise
        if was_cancelled:
            raise asyncio.CancelledError
        return result


class CoverPipeline:
    def __init__(
        self,
        *,
        provider,
        media,
        cache: CoverCache,
        calls=None,
        default_model="",
        max_input_bytes=256 * 1024 * 1024,
        max_duration_seconds=900,
    ):
        self.provider, self.media, self.cache = provider, media, cache
        self.calls = calls or ProviderCalls()
        self.default_model = default_model
        self.max_input_bytes = max_input_bytes
        self.max_duration_seconds = max_duration_seconds

    async def run(
        self,
        *,
        source_path,
        work_dir,
        song_title="",
        artist="",
        voice_model="auto",
        options=None,
        output_format="mp3",
        force_rebuild=False,
    ):
        options = options or CoverOptions()
        if (
            not isinstance(options, CoverOptions)
            or output_format not in ("mp3", "wav", "flac")
            or type(force_rebuild) is not bool
        ):
            raise error("options", "cover_options_invalid")
        title = " ".join(str(song_title or "").split())[:120]
        artist = " ".join(str(artist or "").split())[:80]
        params = asdict(options)
        started, timings = time.perf_counter(), {}
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        output = work_dir / f"cover.{output_format}"
        if output.exists() or output.is_symlink():
            raise error("source", "cover_output_already_exists")
        model = await self.calls.call(self.provider.resolve_voice_model, voice_model, default_model=self.default_model)
        if source_path is None:
            if force_rebuild:
                raise error("source", "source_required_for_rebuild")
            entry = self.cache.find_cover(
                song_title=title, artist=artist, model_name=model, output_format=output_format, params=params
            )
            shutil.copyfile(entry["paths"]["cover"], output)
            return {
                "path": output,
                "metadata": entry["metadata"],
                "cache_key": entry["key"],
                "processing": {"cache_hit": True, "seconds": {"total": time.perf_counter() - started}},
            }
        source_path = Path(source_path)
        if not source_path.is_file():
            raise error("source", "source_file_missing")
        if source_path.suffix.lower().lstrip(".") in PROTECTED:
            raise error("source", "protected_media_format", "请提供普通音频或视频文件，不支持平台加密缓存。")
        if source_path.stat().st_size > self.max_input_bytes:
            raise error("source", "input_too_large")
        duration = await self.media.probe_duration(source_path)
        if duration > self.max_duration_seconds:
            raise error("source", "duration_too_long")
        title = title or source_path.stem[:120] or "未命名歌曲"
        tick = time.perf_counter()
        digest = hashlib.sha256()
        with source_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                await asyncio.sleep(0)
        source_sha = digest.hexdigest()
        timings["source_hash"] = time.perf_counter() - tick
        tick = time.perf_counter()
        fingerprint = await self.calls.call(self.provider.model_fingerprint, model)
        timings["model_fingerprint"] = time.perf_counter() - tick
        base = {
            "source_sha256": source_sha,
            "provider": self.provider.provider_id,
            "separation_model": self.provider.separation_model,
        }
        key = cache_key(
            {
                **base,
                "pipeline": PIPELINE_VERSION,
                "model_fingerprint": fingerprint,
                "voice_model": model,
                "params": params,
                "output_format": output_format,
            }
        )
        entry = None if force_rebuild else self.cache.get("covers", key)
        if entry:
            shutil.copyfile(entry["paths"]["cover"], output)
            return {
                "path": output,
                "metadata": {**entry["metadata"], "song_title": title, "artist": artist},
                "cache_key": key,
                "processing": {"cache_hit": True, "seconds": timings},
            }
        stem_key = cache_key({**base, "pipeline": STEM_VERSION})
        stem_hit, stem_stored, notices = False, False, []
        full_renderer = getattr(self.provider, "render_full_cover", None)
        if callable(full_renderer):
            # Existing local media service owns its full Demucs/RVC request.
            # Preserve this backend without copying its transfer chain here.
            tick = time.perf_counter()
            conversion = await self.calls.call(
                self._full_cover,
                full_renderer,
                source_path=source_path,
                output_path=output,
                model_name=model,
                output_format=output_format,
                **params,
            )
            timings["local_full_pipeline"] = time.perf_counter() - tick
        else:
            stems = None if force_rebuild else self.cache.get("stems", stem_key)
            if stems:
                # Work copies protect immutable cache audio from provider code.
                vocals, instrumental = work_dir / "vocals.wav", work_dir / "instrumental.wav"
                shutil.copyfile(stems["paths"]["vocals"], vocals)
                shutil.copyfile(stems["paths"]["instrumental"], instrumental)
                stem_hit = True
            else:
                prepared = work_dir / "source.wav"
                tick = time.perf_counter()
                await self.media.decode(source_path=source_path, output_path=prepared)
                timings["decode"] = time.perf_counter() - tick
                tick = time.perf_counter()
                vocals, instrumental = await self.calls.call(
                    self.provider.separate_vocals, source_path=prepared, work_dir=work_dir
                )
                timings["separation"] = time.perf_counter() - tick
                for path in (vocals, instrumental):
                    await self.media.probe_duration(path)
                try:
                    self.cache.put(
                        "stems", stem_key, files={"vocals": vocals, "instrumental": instrumental}, metadata=base
                    )
                    stem_stored = True
                except (OSError, ValueError):
                    notices.append("stem_cache_write_failed")
            converted = work_dir / "converted_vocals.wav"
            tick = time.perf_counter()
            conversion = await self.calls.call(
                self.provider.convert_voice,
                source_path=vocals,
                output_path=converted,
                model_name=model,
                **{k: v for k, v in params.items() if not k.endswith("gain_db")},
            )
            timings["voice_conversion"] = time.perf_counter() - tick
            tick = time.perf_counter()
            await self.media.mix(
                converted_vocals=converted,
                instrumental=instrumental,
                output_path=output,
                output_format=output_format,
                vocal_gain_db=options.vocal_gain_db,
                instrumental_gain_db=options.instrumental_gain_db,
            )
            timings["mix"] = time.perf_counter() - tick
        actual_duration = await self.media.probe_duration(output)
        if output.stat().st_size <= 0 or output.stat().st_size > 1024 * 1024 * 1024:
            raise error("mix", "cover_output_invalid")
        rvc = {
            str(k): round(float(v), 3)
            for k, v in (conversion.get("timings") or {}).items()
            if isinstance(v, (int, float)) and math.isfinite(v)
        }
        metadata = {
            "song_title": title,
            "artist": artist,
            "voice_model": model,
            "params": params,
            "output_format": output_format,
            "duration_seconds": actual_duration,
            "pipeline": PIPELINE_VERSION,
        }
        try:
            self.cache.put("covers", key, files={"cover": output}, metadata=metadata)
        except (OSError, ValueError):
            notices.append("cover_cache_write_failed")
        timings["total"] = time.perf_counter() - started
        return {
            "path": output,
            "metadata": metadata,
            "cache_key": key,
            "processing": {
                "cache_hit": False,
                "stems_cache_hit": stem_hit,
                "stems_cache_stored": stem_stored,
                "seconds": {k: round(v, 3) for k, v in timings.items()},
                "rvc": rvc,
                "notices": notices,
            },
        }

    def _full_cover(self, renderer, **kwargs):
        with self.provider.exclusive():
            return renderer(**kwargs)
