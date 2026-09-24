"""Public SDK binding for the single voice-cleaning implementation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import uuid

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResultExperience,
    PluginResultPayload,
)

from .cleaning import CleaningError, FORMATS, MODES, Options, VoiceCleaner


PLUGIN_ID = "akane.voice-clean"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write")
MAX_BYTES = 1024 * 1024 * 1024


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="人声降噪与净化",
        short_hint="对当前附件或生成媒体做人声降噪/增强，输出 wav/flac/mp3。basic 使用基础滤波；auto 按实际 AI 环境执行或明确降级；ai 失败不冒充基础结果。dereverb/deecho 是增强预设，不保证消除回声或混响。只接收材料句柄，默认登记不发送。",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("filesystem",),
        trigger=None,
        inputs=(
            CapabilityIOSlot("source_id", "string", required=True, raw={"minLength": 1, "maxLength": 512}),
            CapabilityIOSlot("mode", "string", raw={"enum": MODES}),
            CapabilityIOSlot("quality", "string", raw={"enum": ("auto", "basic", "ai")}),
            CapabilityIOSlot("output_format", "string", raw={"enum": tuple(FORMATS)}),
            CapabilityIOSlot("post_filter", "boolean"),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 120}),
            CapabilityIOSlot("send_to_user", "boolean", raw={"description": "默认 false，只登记一件净化产物。"}),
        ),
        outputs=(CapabilityIOSlot("files", "file", required=True, max_bytes=MAX_BYTES, delivery="generated_file"),),
        raw={"execution_class": "long_task", "followup": "required", "memory_mode": "timeline"},
    )


class VoiceCleaning:
    provider_id = "provider.akane.voice-clean"

    def __init__(self, resources):
        self.resources = resources
        self.cleaner = None
        self.config_error = ""
        try:
            self.cleaner = VoiceCleaner()
        except CleaningError as exc:
            self.config_error = str(exc)

    async def health(self):
        if self.config_error:
            return HealthStatus(False, "unavailable", self.config_error)
        try:
            await self.cleaner.health()
            return HealthStatus(True, "ready")
        except CleaningError as exc:
            return HealthStatus(False, "unavailable", str(exc))
        except (OSError, asyncio.TimeoutError):
            return HealthStatus(False, "unavailable", "cleaning_dependency_unavailable")

    async def list_capabilities(self):
        return (descriptor(),)

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        output, succeeded = None, False
        try:
            if self.config_error:
                raise CleaningError(self.config_error)
            options = Options(
                **{name: args[name] for name in ("mode", "quality", "output_format", "post_filter") if name in args}
            )
            if not isinstance(args.get("send_to_user", False), bool):
                raise CleaningError("cleaning_delivery_invalid")
            source = await self.resources.open(args.get("source_id", ""))
            if not source.ok:
                return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
            title = str(args.get("output_title") or f"{Path(source.name).stem[:100]}_净化").strip()
            if not title or len(title) > 120 or any(c in title for c in ("/", "\\", "\0")):
                raise CleaningError("invalid_output_title")
            output = source.path.parent / f"voice-clean-{uuid.uuid4().hex}.{options.output_format}"
            info = await self.cleaner.clean(source=source.path, output=output, options=options)
            if output.stat().st_size > MAX_BYTES:
                raise CleaningError("cleaning_output_too_large")
            backend = "AI 人声增强" if info["backend_used"] == "deepfilternet" else "基础滤波降噪"
            summary = f"已完成{backend}，生成 {options.output_format} 文件。"
            facts = ["原始文件未修改，产物登记不等于已发送或播放。", *info["notices"]]
            if info["fallback_reason"]:
                facts.append(f"本次 AI 不可用，已降级为基础处理：{info['fallback_reason']}。")
            content = ManagedArtifactPayload(
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
                content=PluginResultPayload(
                    content={"source_handle": source.handle, **info},
                    experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                ),
            )
            succeeded = True
            return CapabilityResult(is_error=False, status="ok", content=content)
        except CleaningError as exc:
            return CapabilityResult(is_error=True, status="error", reason=str(exc))
        except (OSError, KeyError, ValueError, TypeError):
            return CapabilityResult(is_error=True, status="error", reason="cleaning_execution_failed")
        finally:
            if output is not None and not succeeded:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning("cleaning_temporary_output_cleanup_failed")

    async def aclose(self):
        if self.cleaner is not None:
            await self.cleaner.aclose()


class VoiceCleaningPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(VoiceCleaning(registrar.get_resource_port()))


def create_plugin():
    return VoiceCleaningPlugin()
