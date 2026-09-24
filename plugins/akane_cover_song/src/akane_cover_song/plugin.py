"""Public SDK binding; the independent pipeline owns all cover processing."""

from __future__ import annotations

import asyncio
from dataclasses import fields
import os
from pathlib import Path
import shutil

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResultExperience,
    PluginResultPayload,
)
from companion_v01.plugin_subprocess import PluginProcessRunner, drain

from . import CoverCache, CoverMedia, CoverOptions, CoverPipeline, CoverSongError, ProviderCalls
from . import RemoteRvcClient, RemoteRvcProvider, RvcWebUiProvider

PLUGIN_ID = "akane.cover-song"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
PERMISSIONS = (
    "capability.prompt.invoke",
    "resource.read",
    "artifact.write",
    "network.read",
    "storage.write",
    "connection.rvc.read",
)
MAX_BYTES = 1024 * 1024 * 1024
MIMES = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac"}


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="角色音色翻唱",
        short_hint=(
            "用已配置的本机 RVC 对当前音频/视频翻唱，自动分轨、转声和混音。只接受材料句柄；"
            "仅明确点播以前完成的翻唱时可省略 source_id、用 song_title 查当前用户缓存，缺失需提供材料。"
            "没有音域证据时 pitch_shift 保持 0。QQ 的 delivery=auto 请求语音交付，其他客户端请求文件交付，"
            "none 仅登记；登记不等于已发送或播放。取消等待远端真实终态，断连可能无法确认推理结束。"
        ),
        visible_in=("desktop", "qq"),
        prompt_exposed=True,
        risk="medium",
        confirm="first_time",
        effects=("network", "filesystem", "plugin_state"),
        trigger=None,
        inputs=(
            CapabilityIOSlot("source_id", "string", raw={"maxLength": 512}),
            CapabilityIOSlot("song_title", "string", raw={"maxLength": 120}),
            CapabilityIOSlot("artist", "string", raw={"maxLength": 80}),
            CapabilityIOSlot("voice_model", "string", raw={"maxLength": 120}),
            *(
                CapabilityIOSlot(name, kind, raw={"minimum": low, "maximum": high})
                for name, kind, low, high in (
                    ("pitch_shift", "integer", -24, 24),
                    ("index_rate", "number", 0, 1),
                    ("filter_radius", "integer", 0, 7),
                    ("rms_mix_rate", "number", 0, 1),
                    ("protect", "number", 0, 0.5),
                    ("vocal_gain_db", "number", -12, 12),
                    ("instrumental_gain_db", "number", -12, 6),
                )
            ),
            CapabilityIOSlot("output_format", "string", raw={"enum": tuple(MIMES)}),
            CapabilityIOSlot("delivery", "string", raw={"enum": ("auto", "voice", "file", "both", "none")}),
            CapabilityIOSlot("force_rebuild", "boolean"),
        ),
        outputs=(CapabilityIOSlot("files", "file", required=True, max_bytes=MAX_BYTES, delivery="generated_file"),),
        raw={
            "execution_class": "long_task",
            "followup": "required",
            "memory_mode": "timeline",
            "idempotency": "effectful",
        },
    )


def fail(reason, stage="options"):
    return CoverSongError(stage=stage, reason=reason, public_message="翻唱处理未完成。")


def label(args, key, maximum, default=""):
    value = args.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or any(c in value for c in "/\\\0\r\n"):
        raise fail("cover_label_invalid")
    return value.strip()


class CoverSong:
    provider_id = "provider.akane.cover-song"

    def __init__(self, resources, connections, storage):
        self.resources, self.connections, self.storage = resources, connections, Path(storage)
        self._active = {}
        self._closed = False
        self.ffmpeg = shutil.which(os.environ.get("AKANE_MEDIA_FFMPEG") or "ffmpeg")
        self.ffprobe = shutil.which(os.environ.get("AKANE_MEDIA_FFPROBE") or "ffprobe")

    async def health(self):
        if self._closed:
            return HealthStatus(False, "unavailable", "cover_plugin_closed")
        if not callable(getattr(self.resources, "work_directory", None)):
            return HealthStatus(False, "unavailable", "resource_work_directory_sdk_required")
        if not self.ffmpeg or not self.ffprobe:
            return HealthStatus(False, "unavailable", "cover_ffmpeg_required")
        runner = PluginProcessRunner()
        try:
            for executable in (self.ffmpeg, self.ffprobe):
                code, _ = await runner.run([executable, "-version"], timeout=10)
                if code:
                    return HealthStatus(False, "unavailable", "cover_ffmpeg_unavailable")
            # Named connections are intentionally unavailable outside invoke.
            return HealthStatus(True, "runtime_ready")
        except (OSError, asyncio.TimeoutError):
            return HealthStatus(False, "unavailable", "cover_ffmpeg_unavailable")
        finally:
            await runner.aclose()

    async def list_capabilities(self):
        return (descriptor(),)

    async def invoke(self, capability_id, args, context):
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        runner, calls = PluginProcessRunner(), ProviderCalls()
        task = asyncio.current_task()
        self._active[task] = calls
        try:
            if self._closed:
                raise fail("cover_plugin_closed")
            if not context.profile_user_id or not context.session_id:
                raise fail("cover_context_required")
            source_id = label(args, "source_id", 512)
            title, artist = label(args, "song_title", 120), label(args, "artist", 80)
            model = label(args, "voice_model", 120, "auto")
            if not source_id and not title:
                raise fail("source_required", "source")
            options = CoverOptions(**{f.name: args[f.name] for f in fields(CoverOptions) if f.name in args})
            connection = await self.connections.resolve("rvc")
            if not connection.ok:
                return CapabilityResult(is_error=True, status=connection.status, reason=connection.reason)
            config = connection.options
            fmt = args.get("output_format", config.get("default_output_format", "mp3"))
            delivery = args.get("delivery", config.get("default_delivery", "auto"))
            if fmt not in MIMES or delivery not in ("auto", "voice", "file", "both", "none"):
                raise fail("cover_options_invalid")
            if delivery == "auto":
                delivery = "voice" if context.client_mode == "qq_text" else "file"
            common = {
                "base_url": connection.base_url,
                "timeout_seconds": config.get("timeout_seconds", 1800),
                "cancelled": calls.cancelled,
            }
            if config.get("backend") == "remote":
                provider = RemoteRvcProvider(
                    client=RemoteRvcClient(**common),
                    default_model=connection.model,
                    separation_model=config.get("separation_model", "HP5_only_main_vocal"),
                )
            elif config.get("backend") == "webui":
                provider = RvcWebUiProvider(
                    **common,
                    root_dir=config.get("root_dir", ""),
                    separation_model=config.get("separation_model", "HP5_only_main_vocal"),
                )
            else:
                raise fail("cover_backend_invalid")
            source_path, source_handle = None, ""
            if source_id:
                source = await self.resources.open(source_id)
                if not source.ok:
                    return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
                source_path, source_handle = source.path, source.handle
                title = title or Path(source.name).stem[:120] or "未命名歌曲"
            media = CoverMedia(run=runner.run, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
            cache = await calls.call(CoverCache, self.storage, scope=context.profile_user_id)
            pipeline = CoverPipeline(
                provider=provider,
                media=media,
                cache=cache,
                calls=calls,
                default_model=connection.model,
                max_input_bytes=config.get("max_input_bytes", 256 * 1024 * 1024),
                max_duration_seconds=config.get("max_duration_seconds", 900),
            )
            result = await pipeline.run(
                source_path=source_path,
                work_dir=await self.resources.work_directory(),
                song_title=title,
                artist=artist,
                voice_model=model,
                options=options,
                output_format=fmt,
                force_rebuild=args.get("force_rebuild", False),
            )
            output = result["path"]
            # The single pipeline validates new and restored output exactly once.
            audio = result["metadata"]["audio_info"]
            if not 0 < output.stat().st_size <= MAX_BYTES:
                raise fail("cover_output_invalid", "output")
            processing = result["processing"]
            notices = list(processing.get("notices", []))
            notices = list(dict.fromkeys(notices))
            metadata = result["metadata"]
            summary = ("已恢复翻唱缓存" if processing["cache_hit"] else "已完成角色音色翻唱") + f"，生成 {fmt} 文件。"
            facts = ["原始材料未修改；已交给文件登记链路，登记不等于已发送或播放。"]
            if "provider_output_audio_format_changed" in notices:
                facts.append(
                    f"实际音频为 {audio['sample_rate']} Hz、{audio['channels']} 声道，并非新混音目标的 44100 Hz 立体声；历史缓存未被暗中改写。"
                )
            if any("cache_write_failed" in item for item in notices):
                facts.append("音频已完成，但部分缓存写入失败，后续可能需要重新处理。")
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=ManagedArtifactPayload(
                    artifacts=(
                        ManagedArtifactDraft(
                            path=output,
                            title=f"{metadata['song_title']}_翻唱"[:120],
                            output_format=fmt,
                            mime_type=MIMES[fmt],
                            summary=summary,
                            send_to_user=delivery != "none",
                            delivery_mode="file" if delivery == "none" else delivery,
                        ),
                    ),
                    content=PluginResultPayload(
                        content={
                            "source_handle": source_handle,
                            "song_title": metadata["song_title"],
                            "artist": metadata["artist"],
                            "voice_model": metadata["voice_model"],
                            "output_format": fmt,
                            "audio_info": audio,
                            "processing": {**processing, "notices": notices},
                        },
                        experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                    ),
                ),
            )
        except CoverSongError as exc:
            return CapabilityResult(is_error=True, status="error", reason=exc.reason, content={"stage": exc.stage})
        except asyncio.TimeoutError:
            return CapabilityResult(is_error=True, status="error", reason="cover_processing_timeout")
        except (OSError, ValueError, TypeError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="cover_execution_failed")
        finally:
            await runner.aclose()
            self._active.pop(task, None)

    async def aclose(self):
        self._closed = True
        tasks = tuple(self._active)
        for task in tasks:
            self._active[task].cancel_event.set()
            task.cancel()
        if tasks:
            await drain(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)))


class CoverSongPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(
            CoverSong(registrar.get_resource_port(), registrar.get_connection_port(), registrar.get_storage_dir())
        )


def create_plugin():
    return CoverSongPlugin()
