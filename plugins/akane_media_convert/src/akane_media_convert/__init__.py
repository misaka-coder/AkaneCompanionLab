"""Optional converter using only the public Akane SDK and Python stdlib."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import uuid
from pathlib import Path

from companion_v01.plugin_subprocess import PluginProcessRunner, stop_process  # noqa: F401

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResultExperience,
    PluginResultPayload,
)

from .conversion import ConversionError, FORMATS, INPUT_OPTIONS, Options, PROTECTED_FORMATS, command


PLUGIN_ID = "akane.media-convert"
CAPABILITY_ID = f"{PLUGIN_ID}.run.v1"
VERSION = "0.1.0"
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write")
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024


def dependencies() -> tuple[str, str]:
    paths = []
    for binary in ("ffmpeg", "ffprobe"):
        configured = os.environ.get(f"AKANE_MEDIA_{binary.upper()}", "").strip()
        path = shutil.which(configured or binary)
        if not path:
            raise ConversionError(f"{binary}_not_found")
        paths.append(path)
    return tuple(paths)


def descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="媒体格式转换",
        short_hint="将当前附件或生成文件转换为常见音频格式，或从普通独立视频提取音频；支持裁剪、采样率、声道、码率、响度、增益、头尾静音、淡入淡出与变速。输入用已有材料句柄；不读取播放列表、网络流或受保护缓存。产物登记不等于已发送。",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("filesystem",),
        trigger=None,
        inputs=(
            CapabilityIOSlot("source_id", "string", required=True, raw={"minLength": 1, "maxLength": 512}),
            CapabilityIOSlot("output_format", "string", required=True, raw={"enum": tuple(FORMATS)}),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 120}),
            CapabilityIOSlot("bitrate", "string", raw={"maxLength": 20}),
            CapabilityIOSlot("sample_rate", "integer", raw={"minimum": 0}),
            CapabilityIOSlot("channels", "integer", raw={"enum": (0, 1, 2)}),
            CapabilityIOSlot("start_time", "string", raw={"maxLength": 40}),
            CapabilityIOSlot("end_time", "string", raw={"maxLength": 40}),
            CapabilityIOSlot("normalize_volume", "boolean"),
            CapabilityIOSlot("volume_gain_db", "number", raw={"minimum": -24, "maximum": 24}),
            CapabilityIOSlot("trim_silence", "boolean"),
            CapabilityIOSlot("fade_in_seconds", "number", raw={"minimum": 0, "maximum": 600}),
            CapabilityIOSlot("fade_out_seconds", "number", raw={"minimum": 0, "maximum": 600}),
            CapabilityIOSlot("speed_ratio", "number", raw={"minimum": 0.25, "maximum": 4}),
            CapabilityIOSlot(
                "send_to_user", "boolean", raw={"description": "默认 false，只登记产物；需要交付时使用返回的句柄。"}
            ),
        ),
        outputs=(
            CapabilityIOSlot("files", "file", required=True, max_bytes=MAX_OUTPUT_BYTES, delivery="generated_file"),
        ),
        raw={"execution_class": "long_task", "followup": "required", "memory_mode": "timeline"},
    )


class MediaConverter:
    provider_id = "provider.akane.media-convert"

    def __init__(self, resources):
        self.resources = resources
        self.runner = PluginProcessRunner()
        self._processes = self.runner.processes

    async def _run(self, argv, *, capture: bool = False):
        return await self.runner.run(argv, capture=capture)

    async def health(self):
        try:
            ffmpeg, ffprobe = dependencies()
            for label, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
                code, _ = await self._run([path, "-version"])
                if code:
                    return HealthStatus(False, "unavailable", f"{label}_not_executable")
        except ConversionError as exc:
            return HealthStatus(False, "unavailable", str(exc))
        except OSError:
            return HealthStatus(False, "unavailable", "media_dependency_unavailable")
        return HealthStatus(True, "ready")

    async def list_capabilities(self):
        return (descriptor(),)

    async def _probe(self, ffprobe, path):
        code, output = await self._run(
            [
                ffprobe,
                "-v",
                "error",
                *INPUT_OPTIONS,
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration:stream=codec_name,sample_rate,channels,duration",
                "-of",
                "json",
                str(path),
            ],
            capture=True,
        )
        if code:
            raise ConversionError("media_probe_failed")
        try:
            data = json.loads(output)
            stream = data["streams"][0]
            duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
            sample_rate, channels = int(stream["sample_rate"]), int(stream["channels"])
            if not math.isfinite(duration) or duration <= 0 or sample_rate <= 0 or channels <= 0:
                raise ValueError
            return dict(
                duration_seconds=duration,
                sample_rate=sample_rate,
                channels=channels,
                codec=str(stream.get("codec_name") or ""),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            raise ConversionError("audio_stream_unavailable") from None

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        output = None
        succeeded = False
        try:
            options = Options.from_args(args)
            ffmpeg, ffprobe = dependencies()
            source = await self.resources.open(args["source_id"])
            if not source.ok:
                return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
            if source.path.suffix.lower().lstrip(".") in PROTECTED_FORMATS:
                raise ConversionError("protected_media_format")
            source_info = await self._probe(ffprobe, source.path)
            if options.start >= source_info["duration_seconds"]:
                raise ConversionError("start_time_outside_source")
            title = str(args.get("output_title") or f"{Path(source.name).stem[:100]}_{options.output_format}").strip()
            if not title or len(title) > 120 or any(char in title for char in ("/", "\\", "\0")):
                raise ConversionError("invalid_output_title")
            output = source.path.parent / f"converted-{uuid.uuid4().hex}.{options.output_format}"
            code, _ = await self._run(
                command(ffmpeg, source.path, output, options, source_duration=source_info["duration_seconds"])
            )
            if code or not output.is_file() or output.stat().st_size == 0:
                raise ConversionError("media_conversion_failed")
            if output.stat().st_size > MAX_OUTPUT_BYTES:
                raise ConversionError("media_output_too_large")
            media_info = await self._probe(ffprobe, output)
            summary = f"已转换为 {options.output_format}，{media_info['duration_seconds']:.2f} 秒，{media_info['sample_rate']} Hz，{media_info['channels']} 声道。"
            succeeded = True
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=ManagedArtifactPayload(
                    content=PluginResultPayload(
                        content={"source_handle": source.handle, "media_info": media_info},
                        experience=PluginResultExperience(
                            summary=summary, facts=("使用当前材料的工作副本，原始文件未修改。",)
                        ),
                    ),
                    artifacts=(
                        ManagedArtifactDraft(
                            path=output,
                            title=title,
                            output_format=options.output_format,
                            mime_type=FORMATS[options.output_format][1],
                            summary=summary,
                            send_to_user=args.get("send_to_user", False),
                        ),
                    ),
                ),
            )
        except ConversionError as exc:
            return CapabilityResult(is_error=True, status="error", reason=str(exc))
        except asyncio.TimeoutError:
            return CapabilityResult(is_error=True, status="timeout", reason="media_execution_timeout")
        except (OSError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="media_execution_failed")
        finally:
            if output is not None and not succeeded:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    # The host invocation also owns cleanup of this directory.
                    logging.getLogger(__name__).warning("media_temporary_output_cleanup_failed")

    async def aclose(self):
        await self.runner.aclose()


class MediaConversionPlugin:
    manifest = PluginManifest(PLUGIN_ID, VERSION, AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(MediaConverter(registrar.get_resource_port()))


def create_plugin():
    return MediaConversionPlugin()
