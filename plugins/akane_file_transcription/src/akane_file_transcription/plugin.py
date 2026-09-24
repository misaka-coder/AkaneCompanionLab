"""Public SDK batch transcription adapter; storage and Jobs remain host-owned."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResultExperience,
    PluginResultPayload,
)

from .local import MODELS, Options, TranscriptionError
from .render import FORMATS, render
from .runtime import Transcriber

PLUGIN_ID = "akane.file-transcription"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write", "network.read")


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="文件转写与字幕",
        short_hint="转写当前音频/视频材料，支持批量、部分失败、合并或独立 md/txt/srt/vtt/json 产物。字幕用 srt/vtt；后续总结可用 md/txt。本工具只转写、不总结；只要原文件时不要转写。模型 auto 沿用执行器配置，未缓存模型不会自动下载。远端不支持单次 device/compute_type 控制。只接收当前材料句柄，默认登记不发送。",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("filesystem", "network"),
        trigger=None,
        inputs=(
            CapabilityIOSlot(
                "source_ids",
                "array",
                required=True,
                raw={"items": {"type": "string", "minLength": 1, "maxLength": 512}, "minItems": 1, "maxItems": 20},
            ),
            CapabilityIOSlot("output_format", "string", raw={"enum": tuple(FORMATS)}),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 120}),
            CapabilityIOSlot("language", "string", raw={"pattern": "^(auto|[a-z]{2,3})$"}),
            CapabilityIOSlot("model_size", "string", raw={"enum": MODELS}),
            CapabilityIOSlot("device", "string", raw={"enum": ("auto", "cpu", "cuda")}),
            CapabilityIOSlot(
                "compute_type", "string", raw={"enum": ("auto", "float16", "float32", "int8", "int8_float16")}
            ),
            CapabilityIOSlot("vad_filter", "boolean"),
            CapabilityIOSlot("with_timestamps", "boolean"),
            CapabilityIOSlot("merge_outputs", "boolean"),
            CapabilityIOSlot("send_to_user", "boolean"),
        ),
        outputs=(
            CapabilityIOSlot("files", "file", required=True, max_bytes=8 * 1024 * 1024, delivery="generated_file"),
        ),
        raw={"execution_class": "long_task", "followup": "required", "memory_mode": "timeline"},
    )


class FileTranscription:
    provider_id = "provider.akane.file-transcription"

    def __init__(self, resources):
        self.resources, self.runtime, self.config_error = resources, None, ""
        try:
            self.runtime = Transcriber()
        except TranscriptionError as exc:
            self.config_error = str(exc)

    async def health(self):
        if self.config_error:
            return HealthStatus(False, "unavailable", self.config_error)
        try:
            await self.runtime.probe()
            return HealthStatus(True, "ready")
        except TranscriptionError as exc:
            return HealthStatus(False, "unavailable", str(exc))
        except (OSError, asyncio.TimeoutError):
            return HealthStatus(False, "unavailable", "asr_dependency_unavailable")

    async def list_capabilities(self):
        return (descriptor(),)

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        outputs, succeeded = [], False
        try:
            if self.config_error:
                raise TranscriptionError(self.config_error)
            targets = args.get("source_ids")
            if (
                not isinstance(targets, list)
                or not 1 <= len(targets) <= 20
                or not all(isinstance(s, str) and s.strip() for s in targets)
            ):
                raise TranscriptionError("asr_sources_invalid")
            for name in ("with_timestamps", "merge_outputs", "send_to_user"):
                if name in args and not isinstance(args[name], bool):
                    raise TranscriptionError("asr_boolean_option_invalid")
            fmt = args.get("output_format", "md")
            if fmt not in FORMATS:
                raise TranscriptionError("asr_output_format_invalid")
            title = args.get("output_title") or "转写稿"
            if (
                not isinstance(title, str)
                or len(title) > 120
                or not title.strip()
                or any(c in title for c in "/\\\0\r\n")
            ):
                raise TranscriptionError("asr_output_title_invalid")
            options = Options(
                **{k: args[k] for k in ("model_size", "device", "compute_type", "language", "vad_filter") if k in args}
            )
            transcripts, failures, seen, root = [], [], set(), None
            for target in targets:
                if target in seen:
                    continue
                seen.add(target)
                source = await self.resources.open(target)
                if not source.ok:
                    failures.append({"handle": target, "reason": source.reason or "resource_unavailable"})
                    continue
                root = source.path.parent if root is None else root
                try:
                    transcript = await self.runtime.transcribe(
                        source=source.path, options=options, work_root=source.path.parent
                    )
                except TranscriptionError as exc:
                    # Cancellation with unknown remote completion is fatal,
                    # even if earlier sources succeeded. Never mask it as a
                    # partially successful batch.
                    if str(exc) == "remote_completion_unconfirmed":
                        raise
                    failures.append({"handle": source.handle, "reason": str(exc)})
                    continue
                transcript["source"] = {"handle": source.handle, "title": source.name}
                transcripts.append(transcript)
            if not transcripts:
                return CapabilityResult(
                    is_error=True, status="error", reason="asr_no_transcripts_created", content={"sources": failures}
                )
            groups = [transcripts] if args.get("merge_outputs", True) else [[item] for item in transcripts]
            drafts = []
            for index, group in enumerate(groups, 1):
                output_title = title if len(groups) == 1 else f"{title[:110]}_{index}"
                path = root / f"transcript-{uuid4().hex}.{fmt}"
                outputs.append(path)
                text = render(
                    group, output_format=fmt, title=output_title, with_timestamps=args.get("with_timestamps", True)
                )
                if len(text.encode("utf-8")) > 8 * 1024 * 1024:
                    raise TranscriptionError("asr_output_too_large")
                path.write_text(text, encoding="utf-8")
                drafts.append(
                    ManagedArtifactDraft(
                        path=path,
                        title=output_title,
                        output_format=fmt,
                        mime_type=FORMATS[fmt],
                        summary=f"已转写 {len(group)} 份媒体材料。",
                        send_to_user=args.get("send_to_user", False),
                    )
                )
            summary = f"完成 {len(transcripts)} 份材料转写，生成 {len(drafts)} 件 {fmt} 产物。"
            facts = ["这是自动识别文字，可能有误识别；原件未修改，产物登记不等于已发送。"]
            if failures:
                facts.append(f"另有 {len(failures)} 份材料失败，未伪造文字或把失败材料包含在成功稿中。")
            metadata = [
                {
                    "handle": t["source"]["handle"],
                    "provider": t["provider"],
                    "model": t.get("model", ""),
                    "device": t.get("device", ""),
                    "duration_seconds": t["duration_seconds"],
                    "segment_count": t["segment_count"],
                    "fallback_reason": t.get("fallback_reason", ""),
                }
                for t in transcripts
            ]
            facts.extend(
                f"{m['handle']}：{m['provider']}，模型 {m['model'] or '服务未报告'}，{m['segment_count']} 段。"
                + (f"执行器降级：{m['fallback_reason']}。" if m["fallback_reason"] else "")
                for m in metadata
            )
            content = ManagedArtifactPayload(
                artifacts=tuple(drafts),
                content=PluginResultPayload(
                    content={"transcripts": metadata, "failed_sources": failures},
                    experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                ),
            )
            succeeded = True
            return CapabilityResult(is_error=False, status="ok", content=content)
        except TranscriptionError as exc:
            return CapabilityResult(is_error=True, status="error", reason=str(exc))
        except (OSError, ValueError, TypeError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="asr_execution_failed")
        finally:
            if not succeeded:
                for path in outputs:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logging.getLogger(__name__).warning("asr_output_cleanup_failed")

    async def aclose(self):
        if self.runtime is not None:
            await self.runtime.aclose()


class FileTranscriptionPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(FileTranscription(registrar.get_resource_port()))


def create_plugin():
    return FileTranscriptionPlugin()
