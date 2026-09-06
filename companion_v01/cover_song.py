from __future__ import annotations

import os
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .generated_files_media import build_generated_media_info_projection
from .plugin_subprocess import PluginProcessRunner, run_completed


# Transitional built-in cover service: all RVC protocol now belongs to the
# independent package. Removed with the built-in tool at the plugin cutover.
try:
    from akane_cover_song.cache import CoverCache
    from akane_cover_song.media import CoverMedia
    from akane_cover_song.pipeline import (
        PIPELINE_VERSION as _PIPELINE_VERSION,
        CoverOptions,
        CoverPipeline,
        ProviderCalls,
    )
    from akane_cover_song.rvc import CoverSongError, RvcWebUiProvider
except ModuleNotFoundError as exc:
    if exc.name != "akane_cover_song":
        raise
    from plugins.akane_cover_song.src.akane_cover_song.rvc import CoverSongError, RvcWebUiProvider
    from plugins.akane_cover_song.src.akane_cover_song.media import CoverMedia
    from plugins.akane_cover_song.src.akane_cover_song.cache import CoverCache
    from plugins.akane_cover_song.src.akane_cover_song.pipeline import (
        PIPELINE_VERSION as _PIPELINE_VERSION,
        CoverOptions,
        CoverPipeline,
        ProviderCalls,
    )


class _LegacyMediaBinding:
    """Keep legacy overridable media methods until the built-in tool removal."""

    def __init__(self, service):
        self.service = service
        self.calls = ProviderCalls()

    async def probe_duration(self, path):
        return await self.calls.call(self.service._probe_duration, path)

    async def decode(self, **kwargs):
        return await self.calls.call(self.service._decode_source, **kwargs)

    async def mix(self, **kwargs):
        return await self.calls.call(self.service._mix_tracks, **kwargs)


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

    def capability_status(self) -> dict[str, Any]:
        if not self.ffmpeg_path:
            return {"enabled": False, "status": "missing_executor", "reason": "ffmpeg_not_found"}
        if not self.ffprobe_path:
            return {"enabled": False, "status": "missing_executor", "reason": "ffprobe_not_found"}
        return self.provider.capability_status()

    def list_voice_models(self) -> list[str]:
        return self.provider.list_voice_models()

    def _cache(self, profile_user_id):
        return CoverCache(self.cache_root, scope=profile_user_id)

    def has_cached_cover(self, *, profile_user_id: str, max_entries: int = 128) -> bool:
        return self._cache(profile_user_id).has_cover(max_entries=max_entries)

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
        normalized_format = self._normalize_output_format(output_format or self.default_output_format)
        normalized_delivery = self._normalize_delivery(delivery or self.default_delivery)
        source = None
        if str(source_target or "").strip():
            source = self.generated_file_service._resolve_media_source(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=source_target,
            )
            if source is None:
                return self._failure("source", "source_not_found", "没有找到要翻唱的音频或视频材料。")
        job_dir = self.generated_file_service.work_dir / "_cover_song_tmp" / uuid.uuid4().hex
        options = CoverOptions(
            pitch_shift=max(-24, min(24, int(pitch_shift or 0))),
            index_rate=self._bounded_float(index_rate, 0, 1, 0.6),
            filter_radius=max(0, min(7, int(filter_radius or 0))),
            rms_mix_rate=self._bounded_float(rms_mix_rate, 0, 1, 0.25),
            protect=self._bounded_float(protect, 0, 0.5, 0.33),
            vocal_gain_db=self._bounded_float(vocal_gain_db, -12, 12, 0),
            instrumental_gain_db=self._bounded_float(instrumental_gain_db, -12, 6, -1),
        )
        pipeline = CoverPipeline(
            provider=self.provider,
            media=_LegacyMediaBinding(self),
            cache=self._cache(profile_user_id),
            default_model=self.default_model,
            max_input_bytes=self.max_input_bytes,
            max_duration_seconds=self.max_duration_seconds,
        )
        try:
            result = run_completed(
                lambda: pipeline.run(
                    source_path=Path(source.get("absolute_path") or "") if source else None,
                    work_dir=job_dir,
                    song_title=song_title or (str(source.get("title") or "") if source else ""),
                    artist=artist,
                    voice_model=voice_model,
                    options=options,
                    output_format=normalized_format,
                    force_rebuild=force_rebuild,
                )
            )
            metadata = result["metadata"]
            source = source or {
                "source_type": "cover_cache",
                "source_id": "cover_cache::" + result["cache_key"],
                "handle": "",
                "title": metadata["song_title"],
                "extra_source_ids": [],
            }
            return self._publish_generated(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
                cached_output=result["path"],
                song_title=metadata["song_title"],
                artist=metadata["artist"],
                model_name=metadata["voice_model"],
                params=metadata["params"],
                output_format=normalized_format,
                delivery=normalized_delivery,
                cache_key=result["cache_key"],
                cache_hit=result["processing"]["cache_hit"],
                timestamp=int(timestamp or time.time()),
                processing=result["processing"],
            )
        except CoverSongError as exc:
            return self._failure(exc.stage, exc.reason, exc.public_message)
        except (OSError, ValueError, TypeError, TimeoutError):
            return self._failure("pipeline", "cover_song_failed", "翻唱未完成，原始材料和已完成产物未修改。")
        finally:
            if job_dir.exists():
                try:
                    shutil.rmtree(job_dir)
                except OSError:
                    logging.getLogger(__name__).warning("cover_temporary_cleanup_failed")

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

    def _link_or_copy(self, source: Path, destination: Path) -> None:
        if destination.exists():
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

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
