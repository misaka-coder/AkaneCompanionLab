"""Dataset business binding to public scoped resource/composition/artifact ports."""

from __future__ import annotations

import asyncio
from dataclasses import fields
import logging
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

from .options import DatasetError, MAX_ZIP_BYTES, Options, PRESETS
from .runtime import DatasetRuntime, Source

PLUGIN_ID = "akane.voice-dataset"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write", "capability.invoke")


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="语音训练素材整理",
        short_hint="把当前音频/视频材料按静音切片，生成含 WAV、来源/时间/质量 manifest 和 README 的 ZIP；支持 GPT-SoVITS、RVC、archive 预设、重采样、双声道、音量标准化和部分失败。长度上下限是质量标记而非硬切。clean_first 需已启用并获准的净化插件，执行基础人声滤波；不是训练、标注或保证可用的数据集。只接收材料句柄，默认登记不发送。",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("filesystem",),
        trigger=None,
        inputs=(
            CapabilityIOSlot(
                "source_ids",
                "array",
                required=True,
                raw={
                    "items": {"type": "string", "minLength": 1, "maxLength": 512},
                    "minItems": 1,
                    "maxItems": 20,
                },
            ),
            CapabilityIOSlot("profile", "string", raw={"enum": tuple(PRESETS)}),
            CapabilityIOSlot("target_sr", "integer", raw={"minimum": 8000, "maximum": 96000}),
            CapabilityIOSlot("mono", "boolean"),
            CapabilityIOSlot("min_clip_seconds", "number", raw={"minimum": 0.5, "maximum": 60}),
            CapabilityIOSlot("max_clip_seconds", "number", raw={"minimum": 1, "maximum": 120}),
            CapabilityIOSlot("silence_threshold_db", "number", raw={"minimum": -80, "maximum": -10}),
            CapabilityIOSlot("min_silence_ms", "integer", raw={"minimum": 80, "maximum": 3000}),
            CapabilityIOSlot("max_silence_kept_ms", "integer", raw={"minimum": 0, "maximum": 2000}),
            CapabilityIOSlot("clean_first", "boolean"),
            CapabilityIOSlot("normalize_volume", "boolean"),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 120}),
            CapabilityIOSlot("send_to_user", "boolean"),
        ),
        outputs=(CapabilityIOSlot("files", "file", required=True, max_bytes=MAX_ZIP_BYTES, delivery="generated_file"),),
        raw={"execution_class": "long_task", "followup": "required", "memory_mode": "timeline"},
    )


def safe_reason(value):
    return (
        value
        if isinstance(value, str) and len(value) <= 100 and value.replace("_", "").isalnum()
        else "dataset_source_failed"
    )


class VoiceDataset:
    provider_id = "provider.akane.voice-dataset"

    def __init__(self, resources, capabilities):
        self.resources, self.capabilities, self.runtime = resources, capabilities, DatasetRuntime()

    async def health(self):
        try:
            await self.runtime.probe()
            return HealthStatus(True, "ready")
        except DatasetError as exc:
            return HealthStatus(False, "unavailable", str(exc))

    async def list_capabilities(self):
        return (descriptor(),)

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        output, succeeded = None, False
        try:
            targets = args.get("source_ids")
            if (
                not isinstance(targets, list)
                or not 1 <= len(targets) <= 20
                or not all(
                    isinstance(t, str) and 1 <= len(t) <= 512 and not any(c in t for c in "/\\\r\n\0") for t in targets
                )
            ):
                raise DatasetError("dataset_sources_invalid")
            title = args.get("output_title") or "语音训练素材"
            if (
                not isinstance(title, str)
                or not 1 <= len(title) <= 120
                or not title.strip()
                or any(c in title for c in "/\\\r\n\0")
            ):
                raise DatasetError("dataset_title_invalid")
            if not isinstance(args.get("send_to_user", False), bool):
                raise DatasetError("dataset_delivery_invalid")
            options = Options(**{f.name: args[f.name] for f in fields(Options) if f.name in args})
            sources, failures, root = [], [], None
            for index, target in enumerate(dict.fromkeys(targets), 1):
                source = await self.resources.open(target)
                handle, name = (source.handle, source.name) if source.ok else (target, f"来源 {index}")
                reason = source.reason if not source.ok else ""
                path = source.path if source.ok else None
                if source.ok:
                    root = root or path.parent
                    if options.clean_first:
                        cleaned = await self.capabilities.invoke(
                            "akane.voice-clean.run.v1",
                            {
                                "source_id": source.handle,
                                "quality": "basic",
                                "mode": "voice_focus",
                                "output_format": "wav",
                                "post_filter": False,
                                "send_to_user": False,
                            },
                        )
                        if cleaned.is_error:
                            # An uncertain dependency terminal is never hidden
                            # behind the success of an earlier batch source.
                            if (
                                cleaned.reason
                                in ("remote_completion_unconfirmed", "capability_dependency_execution_unconfirmed")
                                or asyncio.current_task().cancelling()
                            ):
                                return cleaned
                            reason = cleaned.reason or "dataset_cleaning_failed"
                        else:
                            artifacts = (
                                cleaned.content.get("artifacts", ()) if isinstance(cleaned.content, dict) else ()
                            )
                            if len(artifacts) != 1:
                                raise DatasetError("dataset_cleaning_artifact_invalid")
                            material = await self.resources.open(artifacts[0])
                            if not material.ok:
                                reason = material.reason or "dataset_cleaning_artifact_missing"
                            else:
                                path = material.path
                if reason or path is None:
                    failures.append(
                        {
                            "source_index": index,
                            "handle": handle,
                            "title": name,
                            "status": "failed",
                            "slice_count": 0,
                            "reason": safe_reason(reason),
                        }
                    )
                    continue
                sources.append(Source(path, handle, name, index, options.clean_first))
            if not sources:
                return CapabilityResult(
                    is_error=True, status="error", reason="dataset_no_usable_sources", content={"sources": failures}
                )
            output = root / f"dataset-{uuid4().hex}.zip"
            result = await self.runtime.build(
                sources=sources, output=output, title=title, options=options, failures=failures, work_root=root
            )
            if not result["ok"]:
                return CapabilityResult(
                    is_error=True, status="error", reason=result["reason"], content={"sources": result["sources"]}
                )
            stats = result["stats"]
            summary = f"生成 {stats['slice_count']} 个语音切片，推荐 {stats['recommended_count']} 个、需复核 {stats['flagged_count']} 个，打包为 ZIP。"
            facts = [
                "包含 WAV、来源/时间/质量 manifest 和 README；原件未修改，登记不等于已发送。",
                "长度上下限用于质量标记，不强制切断长片段；没有执行标注或模型训练。",
            ]
            if stats["failed_source_count"]:
                facts.append(f"{stats['failed_source_count']} 个来源失败，详情见清单；失败来源没有伪造切片。")
            if options.clean_first:
                facts.append("已通过净化插件完成基础人声滤波；净化中间音频也已登记，未发送或播放。")
            succeeded = True
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=ManagedArtifactPayload(
                    artifacts=(
                        ManagedArtifactDraft(
                            path=output,
                            title=title,
                            output_format="zip",
                            mime_type="application/zip",
                            summary=summary,
                            send_to_user=args.get("send_to_user", False),
                        ),
                    ),
                    content=PluginResultPayload(
                        content={
                            "profile": options.profile,
                            "stats": stats,
                            "issues": result["issues"],
                            "failed_sources": result["sources_failed"],
                        },
                        experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                    ),
                ),
            )
        except DatasetError as exc:
            return CapabilityResult(is_error=True, status="error", reason=str(exc))
        except (OSError, ValueError, TypeError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="dataset_execution_failed")
        finally:
            if output is not None and not succeeded:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning("dataset_output_cleanup_failed")

    async def aclose(self):
        await self.runtime.aclose()


class VoiceDatasetPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(VoiceDataset(registrar.get_resource_port(), registrar.get_capability_port()))


def create_plugin():
    return VoiceDatasetPlugin()
