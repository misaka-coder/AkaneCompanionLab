from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .generated_files_media import build_generated_media_info_projection
from .plugin_subprocess import PluginProcessRunner, run_completed


_PROTECTED_MEDIA_EXTENSIONS = {"kgm", "mflac", "mgg", "ncm", "qmc", "qmc0", "qmc3", "tkm"}
_PIPELINE_VERSION = "rvc-cover-v2"
_STEM_CACHE_VERSION = "rvc-cover-stems-v2"


# Transitional built-in cover service: all RVC protocol now belongs to the
# independent package. Removed with the built-in tool at the plugin cutover.
try:
    from akane_cover_song.media import CoverMedia
    from akane_cover_song.rvc import CoverSongError, RvcWebUiProvider
except ModuleNotFoundError as exc:
    if exc.name != "akane_cover_song":
        raise
    from plugins.akane_cover_song.src.akane_cover_song.rvc import CoverSongError, RvcWebUiProvider
    from plugins.akane_cover_song.src.akane_cover_song.media import CoverMedia


class CoverSongService:
    def __init__(
        self,
        *,
        generated_file_service: Any,
        provider: RvcWebUiProvider,
        cache_root: str | Path,
        default_model: str = "",
        default_output_format: str = "mp3",
        default_delivery: str = "auto",
        max_duration_seconds: float = 900.0,
        max_input_bytes: int = 256 * 1024 * 1024,
        ffmpeg_path: str = "",
        ffprobe_path: str = "",
    ) -> None:
        self.generated_file_service = generated_file_service
        self.provider = provider
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.default_model = str(default_model or "").strip()
        self.default_output_format = self._normalize_output_format(default_output_format)
        self.default_delivery = self._normalize_delivery(default_delivery)
        self.max_duration_seconds = max(15.0, min(3600.0, float(max_duration_seconds or 900.0)))
        self.max_input_bytes = max(1024 * 1024, int(max_input_bytes or 0))
        self.ffmpeg_path = str(ffmpeg_path or shutil.which("ffmpeg") or "").strip()
        self.ffprobe_path = str(ffprobe_path or shutil.which("ffprobe") or "").strip()
        self._cache_lock = threading.RLock()

    def capability_status(self) -> dict[str, Any]:
        if not self.ffmpeg_path:
            return {"enabled": False, "status": "missing_executor", "reason": "ffmpeg_not_found"}
        if not self.ffprobe_path:
            return {"enabled": False, "status": "missing_executor", "reason": "ffprobe_not_found"}
        return self.provider.capability_status()

    def list_voice_models(self) -> list[str]:
        return self.provider.list_voice_models()

    def has_cached_cover(self, *, profile_user_id: str, max_entries: int = 128) -> bool:
        """Check for a reusable completed cover without reading source media."""

        profile_dir = self._profile_cache_dir(profile_user_id)
        if not profile_dir.exists():
            return False
        checked = 0
        for manifest_path in profile_dir.glob("*/manifest.json"):
            if checked >= max(1, min(1000, int(max_entries or 128))):
                break
            checked += 1
            manifest = self._read_json(manifest_path)
            output_format = self._normalize_output_format(manifest.get("output_format") or "")
            output_path = manifest_path.parent / f"cover.{output_format}"
            if output_path.exists() and output_path.is_file() and output_path.stat().st_size > 0:
                return True
        return False

    def cover_song(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_target: str = "",
        song_title: str = "",
        artist: str = "",
        voice_model: str = "auto",
        pitch_shift: int = 0,
        index_rate: float = 0.6,
        filter_radius: int = 3,
        rms_mix_rate: float = 0.25,
        protect: float = 0.33,
        vocal_gain_db: float = 0.0,
        instrumental_gain_db: float = -1.0,
        output_format: str = "",
        delivery: str = "auto",
        force_rebuild: bool = False,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        run_started = time.perf_counter()
        effective_ts = int(timestamp or time.time())
        normalized_format = self._normalize_output_format(output_format or self.default_output_format)
        normalized_delivery = self._normalize_delivery(delivery or self.default_delivery)
        model_name = self.provider.resolve_voice_model(voice_model, default_model=self.default_model)
        params = {
            "pitch_shift": max(-24, min(24, int(pitch_shift or 0))),
            "index_rate": self._bounded_float(index_rate, 0.0, 1.0, 0.6),
            "filter_radius": max(0, min(7, int(filter_radius or 0))),
            "rms_mix_rate": self._bounded_float(rms_mix_rate, 0.0, 1.0, 0.25),
            "protect": self._bounded_float(protect, 0.0, 0.5, 0.33),
            "vocal_gain_db": self._bounded_float(vocal_gain_db, -12.0, 12.0, 0.0),
            "instrumental_gain_db": self._bounded_float(instrumental_gain_db, -12.0, 6.0, -1.0),
        }
        clean_title = self._clean_label(song_title, 120)
        clean_artist = self._clean_label(artist, 80)

        if not str(source_target or "").strip():
            return self._restore_cached_cover(
                profile_user_id=profile_user_id,
                session_id=session_id,
                song_title=clean_title,
                artist=clean_artist,
                model_name=model_name,
                output_format=normalized_format,
                delivery=normalized_delivery,
                timestamp=effective_ts,
            )

        source = self.generated_file_service._resolve_media_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=source_target,
        )
        if source is None:
            return self._failure("source", "source_not_found", "没有找到要翻唱的音频或视频材料。")
        source_path = Path(source.get("absolute_path") or "")
        if not source_path.exists() or not source_path.is_file():
            return self._failure("source", "source_file_missing", "要翻唱的来源文件已经不在本地工作区。")
        input_ext = source_path.suffix.lower().lstrip(".")
        if input_ext in _PROTECTED_MEDIA_EXTENSIONS:
            return self._failure(
                "source",
                "protected_media_format",
                "这个来源属于平台加密或专有缓存格式，请提供普通 MP3、FLAC、WAV、M4A 或视频文件。",
            )
        if source_path.stat().st_size > self.max_input_bytes:
            return self._failure("source", "input_too_large", "这份音频超过当前翻唱任务允许的文件大小。")
        try:
            duration = self._probe_duration(source_path)
        except CoverSongError as exc:
            return self._failure(exc.stage, exc.reason, exc.public_message)
        except (OSError, TimeoutError):
            return self._failure("source", "cover_media_probe_failed", "无法确认这份材料的音频时长。")
        if duration > self.max_duration_seconds:
            return self._failure(
                "source",
                "duration_too_long",
                f"这首音频约 {duration / 60:.1f} 分钟，超过当前 {self.max_duration_seconds / 60:.1f} 分钟的处理上限。",
            )

        inferred_title = self._clean_label(str(source.get("title") or source_path.stem), 120)
        clean_title = clean_title or inferred_title or "未命名歌曲"
        source_hash_started = time.perf_counter()
        source_hash = self._sha256_file(source_path)
        source_hash_seconds = time.perf_counter() - source_hash_started
        model_fingerprint_started = time.perf_counter()
        model_fingerprint = self.provider.model_fingerprint(model_name)
        model_fingerprint_seconds = time.perf_counter() - model_fingerprint_started
        cache_payload = {
            "pipeline": _PIPELINE_VERSION,
            "source_sha256": source_hash,
            "provider": self.provider.provider_id,
            "model_fingerprint": model_fingerprint,
            "separation_model": self.provider.separation_model,
            "voice_model": model_name,
            "params": params,
            "output_format": normalized_format,
        }
        cache_key = hashlib.sha256(
            json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_dir = self._profile_cache_dir(profile_user_id) / cache_key
        cached_output = cache_dir / f"cover.{normalized_format}"
        manifest_path = cache_dir / "manifest.json"
        if not force_rebuild and self._valid_cached_output(cached_output, manifest_path, cache_key):
            manifest = self._read_json(manifest_path)
            processing = {
                "cache_hit": True,
                "seconds": {
                    "source_hash": round(source_hash_seconds, 3),
                    "model_fingerprint": round(model_fingerprint_seconds, 3),
                    "total": round(time.perf_counter() - run_started, 3),
                },
            }
            return self._publish_generated(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
                cached_output=cached_output,
                song_title=clean_title or str(manifest.get("song_title") or ""),
                artist=clean_artist or str(manifest.get("artist") or ""),
                model_name=model_name,
                params=params,
                output_format=normalized_format,
                delivery=normalized_delivery,
                cache_key=cache_key,
                cache_hit=True,
                timestamp=effective_ts,
                processing=processing,
            )

        stem_cache_payload = {
            "pipeline": _STEM_CACHE_VERSION,
            "source_sha256": source_hash,
            "provider": self.provider.provider_id,
            "separation_model": self.provider.separation_model,
        }
        stem_cache_key = hashlib.sha256(
            json.dumps(stem_cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        stem_cache_dir = self._profile_cache_dir(profile_user_id) / "_stems" / stem_cache_key
        cached_vocals = stem_cache_dir / "vocals.wav"
        cached_instrumental = stem_cache_dir / "instrumental.wav"
        stem_manifest_path = stem_cache_dir / "manifest.json"
        stems_cache_hit = not force_rebuild and self._valid_cached_stems(
            vocals=cached_vocals,
            instrumental=cached_instrumental,
            manifest=stem_manifest_path,
            cache_key=stem_cache_key,
        )
        stems_cache_stored = False
        timings: dict[str, float] = {
            "source_hash": source_hash_seconds,
            "model_fingerprint": model_fingerprint_seconds,
        }
        rvc_timings: dict[str, float] = {}
        conversion: dict[str, Any] = {}
        job_dir = self.generated_file_service.work_dir / "_cover_song_tmp" / uuid.uuid4().hex
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            mixed_output = job_dir / f"cover.{normalized_format}"
            full_renderer = getattr(self.provider, "render_full_cover", None)
            if callable(full_renderer):
                # The local full renderer performs Demucs and RVC in one request.
                # It does not consume the legacy host-side stem cache.
                stems_cache_hit = False
                stems_cache_stored = False
                render_started = time.perf_counter()
                with self.provider.exclusive():
                    conversion = full_renderer(
                        source_path=source_path,
                        output_path=mixed_output,
                        model_name=model_name,
                        output_format=normalized_format,
                        pitch_shift=params["pitch_shift"],
                        index_rate=params["index_rate"],
                        filter_radius=params["filter_radius"],
                        rms_mix_rate=params["rms_mix_rate"],
                        protect=params["protect"],
                        vocal_gain_db=params["vocal_gain_db"],
                        instrumental_gain_db=params["instrumental_gain_db"],
                    )
                timings["local_full_pipeline"] = time.perf_counter() - render_started
                rvc_timings = {
                    str(key): round(float(value), 3)
                    for key, value in dict(conversion.get("timings") or {}).items()
                    if isinstance(value, (int, float))
                }
            else:
                prepared = job_dir / "source.wav"
                if not stems_cache_hit:
                    decode_started = time.perf_counter()
                    self._decode_source(source_path=source_path, output_path=prepared)
                    timings["decode"] = time.perf_counter() - decode_started
                converted_vocals = job_dir / "converted_vocals.wav"
                with self.provider.exclusive():
                    if not force_rebuild and self._valid_cached_stems(
                        vocals=cached_vocals,
                        instrumental=cached_instrumental,
                        manifest=stem_manifest_path,
                        cache_key=stem_cache_key,
                    ):
                        vocals, instrumental = cached_vocals, cached_instrumental
                        stems_cache_hit = True
                    else:
                        separation_started = time.perf_counter()
                        vocals, instrumental = self.provider.separate_vocals(
                            source_path=prepared,
                            work_dir=job_dir,
                        )
                        timings["separation"] = time.perf_counter() - separation_started
                        stem_cache_started = time.perf_counter()
                        stems_cache_stored = self._store_stem_cache(
                            vocals=vocals,
                            instrumental=instrumental,
                            cache_dir=stem_cache_dir,
                            manifest_path=stem_manifest_path,
                            cache_key=stem_cache_key,
                            source_hash=source_hash,
                            timestamp=effective_ts,
                        )
                        timings["stem_cache_write"] = time.perf_counter() - stem_cache_started
                    conversion_started = time.perf_counter()
                    conversion = self.provider.convert_voice(
                        source_path=vocals,
                        output_path=converted_vocals,
                        model_name=model_name,
                        pitch_shift=params["pitch_shift"],
                        index_rate=params["index_rate"],
                        filter_radius=params["filter_radius"],
                        rms_mix_rate=params["rms_mix_rate"],
                        protect=params["protect"],
                    )
                    timings["voice_conversion"] = time.perf_counter() - conversion_started
                    rvc_timings = {
                        str(key): round(float(value), 3)
                        for key, value in dict(conversion.get("timings") or {}).items()
                        if isinstance(value, (int, float))
                    }
                mix_started = time.perf_counter()
                self._mix_tracks(
                    converted_vocals=converted_vocals,
                    instrumental=instrumental,
                    output_path=mixed_output,
                    output_format=normalized_format,
                    vocal_gain_db=params["vocal_gain_db"],
                    instrumental_gain_db=params["instrumental_gain_db"],
                )
                timings["mix"] = time.perf_counter() - mix_started
            if not mixed_output.exists() or mixed_output.stat().st_size <= 0:
                raise CoverSongError(
                    stage="mix",
                    reason="cover_output_missing",
                    public_message="翻唱混音流程结束了，但没有生成可用的成品音频。",
                )
            processing = {
                "cache_hit": False,
                "stems_cache_hit": bool(stems_cache_hit),
                "stems_cache_stored": bool(stems_cache_stored),
                "seconds": {str(key): round(float(value), 3) for key, value in timings.items()},
                "rvc": rvc_timings,
            }
            manifest = {
                "cache_key": cache_key,
                "pipeline": _PIPELINE_VERSION,
                "song_title": clean_title,
                "artist": clean_artist,
                "voice_model": model_name,
                "source_sha256": source_hash,
                "output_format": normalized_format,
                "params": params,
                "index_name": Path(str(conversion.get("index_path") or "")).name,
                "created_at": effective_ts,
                "processing": processing,
            }
            cache_write_started = time.perf_counter()
            with self._cache_lock:
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_copy(mixed_output, cached_output)
                processing["seconds"]["final_audio_cache_write"] = round(
                    time.perf_counter() - cache_write_started,
                    3,
                )
                processing["seconds"]["total"] = round(time.perf_counter() - run_started, 3)
                self._atomic_write_json(manifest_path, manifest)
            processing["seconds"]["total"] = round(time.perf_counter() - run_started, 3)
            return self._publish_generated(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
                cached_output=cached_output,
                song_title=clean_title,
                artist=clean_artist,
                model_name=model_name,
                params=params,
                output_format=normalized_format,
                delivery=normalized_delivery,
                cache_key=cache_key,
                cache_hit=False,
                timestamp=effective_ts,
                processing=processing,
            )
        except CoverSongError as exc:
            processing = {
                "cache_hit": False,
                "stems_cache_hit": bool(stems_cache_hit),
                "seconds": {
                    **{str(key): round(float(value), 3) for key, value in timings.items()},
                    "total": round(time.perf_counter() - run_started, 3),
                },
                "rvc": rvc_timings,
            }
            return self._failure(exc.stage, exc.reason, exc.public_message, processing=processing)
        except Exception:
            processing = {
                "cache_hit": False,
                "stems_cache_hit": bool(stems_cache_hit),
                "seconds": {
                    **{str(key): round(float(value), 3) for key, value in timings.items()},
                    "total": round(time.perf_counter() - run_started, 3),
                },
                "rvc": rvc_timings,
            }
            return self._failure(
                "pipeline",
                "cover_song_failed",
                "翻唱流程遇到了未预期错误，输入和已有缓存仍然保留。",
                processing=processing,
            )
        finally:
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)

    def _restore_cached_cover(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        song_title: str,
        artist: str,
        model_name: str,
        output_format: str,
        delivery: str,
        timestamp: int,
    ) -> dict[str, Any]:
        if not song_title:
            return self._failure("cache", "source_or_title_required", "请提供歌曲材料，或者说出已经翻唱过的歌曲名。")
        matches: list[tuple[dict[str, Any], Path]] = []
        profile_dir = self._profile_cache_dir(profile_user_id)
        if profile_dir.exists():
            target_title = self._normalize_lookup(song_title)
            target_artist = self._normalize_lookup(artist)
            for manifest_path in profile_dir.glob("*/manifest.json"):
                manifest = self._read_json(manifest_path)
                if self._normalize_lookup(manifest.get("song_title")) != target_title:
                    continue
                if target_artist and self._normalize_lookup(manifest.get("artist")) != target_artist:
                    continue
                if str(manifest.get("voice_model") or "").lower() != model_name.lower():
                    continue
                if str(manifest.get("output_format") or "").lower() != output_format:
                    continue
                output = manifest_path.parent / f"cover.{output_format}"
                if output.exists() and output.stat().st_size > 0:
                    matches.append((manifest, output))
        if not matches:
            return self._failure("cache", "cached_cover_not_found", f"还没有找到《{song_title}》的现成翻唱缓存。")
        if len(matches) > 1 and not artist:
            artists = sorted({str(item[0].get("artist") or "未知原唱") for item in matches})
            if len(artists) > 1:
                return self._failure(
                    "cache",
                    "cached_cover_ambiguous",
                    f"《{song_title}》有多个缓存版本，请补充原唱：{'、'.join(artists[:6])}。",
                )
        manifest, cached_output = sorted(matches, key=lambda item: int(item[0].get("created_at") or 0), reverse=True)[0]
        source = {
            "source_type": "cover_cache",
            "source_id": f"cover_cache::{manifest.get('cache_key')}",
            "handle": "",
            "title": str(manifest.get("song_title") or song_title),
            "extra_source_ids": [],
        }
        return self._publish_generated(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source=source,
            cached_output=cached_output,
            song_title=str(manifest.get("song_title") or song_title),
            artist=str(manifest.get("artist") or artist),
            model_name=model_name,
            params=dict(manifest.get("params") or {}),
            output_format=output_format,
            delivery=delivery,
            cache_key=str(manifest.get("cache_key") or cached_output.parent.name),
            cache_hit=True,
            timestamp=timestamp,
            processing={"cache_hit": True, "seconds": {}},
        )

    def _publish_generated(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: dict[str, Any],
        cached_output: Path,
        song_title: str,
        artist: str,
        model_name: str,
        params: dict[str, Any],
        output_format: str,
        delivery: str,
        cache_key: str,
        cache_hit: bool,
        timestamp: int,
        processing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_label = Path(model_name).stem
        title = self.generated_file_service._normalize_title(f"{song_title}_{model_label}_翻唱") or "Akane翻唱"
        output_path = self.generated_file_service._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=output_format,
            timestamp=timestamp,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._link_or_copy(cached_output, output_path)
        source_ids = [str(source.get("source_id") or "").strip()]
        source_ids.extend(str(item or "").strip() for item in list(source.get("extra_source_ids") or []))
        source_ids = [item for item in source_ids if item]
        summary = f"《{song_title}》的 {model_label} RVC 翻唱成品"
        if artist:
            summary += f"（原唱：{artist}）"
        content_card = {
            "type": "cover_song",
            "summary": summary + ("，已命中缓存。" if cache_hit else "。"),
            "source": {
                "source_type": str(source.get("source_type") or ""),
                "source_id": str(source.get("source_id") or ""),
                "handle": str(source.get("handle") or ""),
                "title": str(source.get("title") or song_title),
            },
            "cover": {
                "song_title": song_title,
                "artist": artist,
                "provider": self.provider.provider_id,
                "voice_model": model_name,
                "pitch_shift": int(params.get("pitch_shift") or 0),
                "output_format": output_format,
                "cache_hit": bool(cache_hit),
                "cache_key_prefix": cache_key[:12],
                "pipeline_version": _PIPELINE_VERSION,
            },
            "processing": dict(processing or {"cache_hit": bool(cache_hit)}),
        }
        content_card["media_info"] = build_generated_media_info_projection(
            self.generated_file_service,
            output_path=output_path,
            output_format=output_format,
            source=source,
            hints={"file_size": output_path.stat().st_size},
        )
        generated = self.generated_file_service.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=title,
            output_format=output_format,
            storage_relpath=self.generated_file_service._storage_relpath(output_path),
            mime_type=self.generated_file_service._mime_type_for_format(output_format),
            file_ext=output_format,
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or ""),
            created_by_tool="cover_song",
            delivery_status="pending" if delivery != "none" else "not_requested",
            timestamp=timestamp,
        )
        generated["absolute_path"] = str(self.generated_file_service.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "send_to_user": delivery != "none",
            "delivery_mode": delivery,
            "cache_hit": bool(cache_hit),
            "processing": dict(processing or {"cache_hit": bool(cache_hit)}),
            "followup_context": (
                f"你刚刚已经完成《{song_title}》的 RVC 翻唱，结果是 {generated.get('generated_handle')}。"
                + ("这次命中了现成缓存，没有重复推理。" if cache_hit else "这次完成了人声分离、音色转换和重新混音。")
                + (f"交付方式是 {delivery}。" if delivery != "none" else "结果暂时只保留在生成区，没有主动发送。")
            ),
        }

    def _run_media(self, method, **kwargs):
        async def execute():
            runner = PluginProcessRunner()
            media = CoverMedia(run=runner.run, ffmpeg=self.ffmpeg_path, ffprobe=self.ffprobe_path)
            try:
                return await getattr(media, method)(**kwargs)
            finally:
                await runner.aclose()

        return run_completed(execute)

    def _decode_source(self, *, source_path: Path, output_path: Path) -> None:
        self._run_media("decode", source_path=source_path, output_path=output_path)

    def _mix_tracks(self, **kwargs) -> None:
        self._run_media("mix", **kwargs)

    def _probe_duration(self, path: Path) -> float:
        return self._run_media("probe_duration", path=path)

    def _valid_cached_output(self, output: Path, manifest: Path, cache_key: str) -> bool:
        if not output.exists() or not output.is_file() or output.stat().st_size <= 0 or not manifest.exists():
            return False
        return str(self._read_json(manifest).get("cache_key") or "") == cache_key

    def _valid_cached_stems(
        self,
        *,
        vocals: Path,
        instrumental: Path,
        manifest: Path,
        cache_key: str,
    ) -> bool:
        for path in (vocals, instrumental, manifest):
            if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
                return False
        payload = self._read_json(manifest)
        return (
            str(payload.get("cache_key") or "") == cache_key
            and str(payload.get("pipeline") or "") == _STEM_CACHE_VERSION
        )

    def _store_stem_cache(
        self,
        *,
        vocals: Path,
        instrumental: Path,
        cache_dir: Path,
        manifest_path: Path,
        cache_key: str,
        source_hash: str,
        timestamp: int,
    ) -> bool:
        try:
            with self._cache_lock:
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_copy(vocals, cache_dir / "vocals.wav")
                self._atomic_copy(instrumental, cache_dir / "instrumental.wav")
                self._atomic_write_json(
                    manifest_path,
                    {
                        "cache_key": cache_key,
                        "pipeline": _STEM_CACHE_VERSION,
                        "source_sha256": source_hash,
                        "provider": self.provider.provider_id,
                        "separation_model": self.provider.separation_model,
                        "created_at": int(timestamp),
                    },
                )
        except OSError:
            return False
        return True

    def _profile_cache_dir(self, profile_user_id: str) -> Path:
        safe_profile = self.generated_file_service._safe_filename(profile_user_id or "profile")[:80] or "profile"
        return self.cache_root / safe_profile

    def _atomic_copy(self, source: Path, destination: Path) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _link_or_copy(self, source: Path, destination: Path) -> None:
        if destination.exists():
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {"wave": "wav", "mpeg3": "mp3"}
        text = aliases.get(text, text)
        return text if text in {"mp3", "flac", "wav"} else "mp3"

    def _normalize_delivery(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {"qq_voice": "voice", "audio": "voice", "send": "auto", "不发送": "none"}
        text = aliases.get(text, text)
        return text if text in {"auto", "voice", "file", "both", "none"} else "auto"

    def _bounded_float(self, value: Any, lower: float, upper: float, default: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            parsed = default
        return max(lower, min(upper, parsed))

    def _clean_label(self, value: Any, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:limit]

    def _normalize_lookup(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())

    def _failure(
        self,
        stage: str,
        reason: str,
        message: str,
        *,
        processing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = {
            "ok": False,
            "generated": None,
            "stage": str(stage or "unknown"),
            "error": str(reason or "cover_song_failed"),
            "followup_context": f"这次翻唱没有完成：{message}请根据现有信息自然告诉用户，不要假装已生成文件。",
        }
        if processing:
            result["processing"] = dict(processing)
        return result
