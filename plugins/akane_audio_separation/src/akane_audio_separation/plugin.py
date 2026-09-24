"""Public SDK adapter for real local/remote two-stem separation."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
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

from .local import LocalDemucs, LocalSeparationError
from .media import FORMATS, PROTECTED, MediaTools, SeparationError, executable
from .remote import MAX_BYTES, RemoteSeparation, RemoteSeparationError


PLUGIN_ID = "akane.audio-separation"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
PERMISSIONS = ("capability.prompt.invoke", "resource.read", "artifact.write", "network.read")


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="人声／伴奏分轨",
        short_hint="将当前附件或生成媒体分离为人声、伴奏两条真实音轨，输出 wav/flac/mp3。输入用已有材料句柄；不能恢复受保护缓存，不保证完全消除串音。默认只登记两件产物，登记不等于已发送。",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("filesystem", "network"),
        trigger=None,
        inputs=(
            CapabilityIOSlot("source_id", "string", required=True, raw={"minLength": 1, "maxLength": 512}),
            CapabilityIOSlot("mode", "string", raw={"enum": ("vocals_instrumental",)}),
            CapabilityIOSlot("output_format", "string", raw={"enum": tuple(FORMATS)}),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 110}),
            CapabilityIOSlot(
                "send_to_user", "boolean", raw={"description": "默认 false；返回两个产物句柄供后续交付。"}
            ),
        ),
        outputs=(CapabilityIOSlot("files", "file", required=True, max_bytes=MAX_BYTES, delivery="generated_file"),),
        raw={"execution_class": "long_task", "followup": "required", "memory_mode": "timeline"},
    )


class AudioSeparation(MediaTools):
    provider_id = "provider.akane.audio-separation"

    def __init__(self, resources):
        self.resources = resources
        super().__init__()
        self.local = None
        self.remote = None
        self.selected = None
        self.config_error = ""
        self.backend = os.environ.get("AKANE_SEPARATION_BACKEND", "auto").strip()
        try:
            if self.backend not in ("auto", "local", "remote"):
                raise SeparationError("separation_backend_invalid")
            self.local = LocalDemucs(
                model=os.environ.get("AKANE_SEPARATION_MODEL", "htdemucs").strip(),
                device=os.environ.get("AKANE_SEPARATION_DEVICE", "auto").strip(),
            )
            url = os.environ.get("AKANE_SEPARATION_REMOTE_URL", "").strip()
            if url:
                self.remote = RemoteSeparation(
                    url, uvr_model=os.environ.get("AKANE_SEPARATION_UVR_MODEL", "HP5_only_main_vocal").strip()
                )
        except (SeparationError, LocalSeparationError, RemoteSeparationError) as exc:
            self.config_error = str(exc)

    async def health(self):
        self.selected = None
        if self.config_error:
            return HealthStatus(False, "unavailable", self.config_error)
        try:
            for name in ("ffmpeg", "ffprobe"):
                code, _ = await self.runner.run([executable(name), "-version"], timeout=5)
                if code:
                    raise SeparationError(f"{name}_not_executable")
            remote_reason = ""
            if self.backend != "local" and self.remote is not None:
                try:
                    self.selected = await self.remote.probe()
                except RemoteSeparationError as exc:
                    remote_reason = str(exc)
            if self.selected is None:
                if self.backend == "remote":
                    raise SeparationError(remote_reason or "separation_remote_not_configured")
                info = await self.local.probe()
                self.selected = {**info, "backend": "local_demucs", "fallback_reason": remote_reason}
            return HealthStatus(True, "ready")
        except (SeparationError, LocalSeparationError) as exc:
            return HealthStatus(False, "unavailable", str(exc))
        except asyncio.TimeoutError:
            return HealthStatus(False, "unavailable", "separation_dependency_timeout")
        except OSError:
            return HealthStatus(False, "unavailable", "separation_dependency_unavailable")

    async def list_capabilities(self):
        return (descriptor(),)

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id != CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        work = None
        succeeded = False
        try:
            if args.get("mode", "vocals_instrumental") != "vocals_instrumental":
                raise SeparationError("unsupported_separation_mode")
            fmt = args.get("output_format", "mp3")
            if fmt not in FORMATS:
                raise SeparationError("unsupported_separation_output_format")
            if not isinstance(args.get("send_to_user", False), bool):
                raise SeparationError("separation_delivery_invalid")
            source = await self.resources.open(args.get("source_id", ""))
            if not source.ok:
                return CapabilityResult(is_error=True, status=source.status, reason=source.reason)
            if source.path.suffix.lower().lstrip(".") in PROTECTED:
                raise SeparationError("protected_media_format")
            title = str(args.get("output_title") or Path(source.name).stem[:100]).strip()
            if not title or len(title) > 110 or any(c in title for c in ("/", "\\", "\0")):
                raise SeparationError("invalid_output_title")
            source_info = await self.probe_audio(source.path)
            # Preserve remote-first/local fallback when availability changes
            # after installation. The model descriptor stays unchanged.
            health = await self.health()
            if not health.ok:
                raise SeparationError(health.reason)
            selected = dict(self.selected)
            work = source.path.parent / ("separation-" + uuid.uuid4().hex)
            work.mkdir()
            if selected["backend"] == "local_demucs":
                result = await self.local.separate_media(
                    source=source.path, output_root=work / "raw", model_info=selected
                )
                source_info = result["input_media"]
                paths = {role: work / "raw" / f"{role}.wav" for role in ("vocals", "instrumental")}
                device = result["device_used"]
            else:
                remote_input = source.path
                if source.path.suffix.lower() in {
                    ".mp4",
                    ".avi",
                    ".mkv",
                    ".mov",
                    ".webm",
                    ".flv",
                    ".wmv",
                    ".mpeg",
                    ".mpg",
                    ".m4v",
                    ".ts",
                }:
                    remote_input = work / "input.wav"
                    await self.encode(source.path, remote_input, "wav", sample_rate=44100, channels=2)
                    source_info = await self.probe_audio(remote_input)
                paths = await self.remote.separate(
                    source=remote_input,
                    output_root=work / "raw",
                    backend=selected["backend"],
                    model=selected["model"],
                    output_format=fmt,
                )
                device = "remote"
            artifacts, infos = [], []
            for role, label in (("vocals", "人声"), ("instrumental", "伴奏")):
                target = work / f"{role}.{fmt}"
                if paths[role].suffix == f".{fmt}":
                    paths[role].replace(target)
                else:
                    await self.encode(paths[role], target, fmt)
                info = await self.probe_audio(target)
                if abs(info["duration_seconds"] - source_info["duration_seconds"]) > max(
                    0.25, source_info["duration_seconds"] * 0.02
                ):
                    raise SeparationError("separation_output_duration_mismatch")
                infos.append({"role": role, **info})
                artifacts.append(
                    ManagedArtifactDraft(
                        path=target,
                        title=f"{title}_{label}",
                        output_format=fmt,
                        mime_type=FORMATS[fmt][1],
                        summary=f"从 {source.handle} 分离的{label}轨。",
                        send_to_user=args.get("send_to_user", False),
                    )
                )
            if sum(item.path.stat().st_size for item in artifacts) > MAX_BYTES:
                raise SeparationError("separation_output_too_large")
            facts = ["原始文件未修改。", "分轨可能残留串音，不保证完全隔离。"]
            if selected.get("fallback_reason"):
                facts.append("媒体服务不可用，本次使用已验证的本地 Demucs。")
            content = ManagedArtifactPayload(
                artifacts=tuple(artifacts),
                content=PluginResultPayload(
                    content={
                        "source_handle": source.handle,
                        "backend_used": selected["backend"],
                        "device_used": device,
                        "stems": infos,
                    },
                    experience=PluginResultExperience(summary="已生成人声、伴奏两条音轨。", facts=tuple(facts)),
                ),
            )
            succeeded = True
            return CapabilityResult(is_error=False, status="ok", content=content)
        except (SeparationError, LocalSeparationError, RemoteSeparationError) as exc:
            return CapabilityResult(is_error=True, status="error", reason=str(exc))
        except asyncio.TimeoutError:
            return CapabilityResult(is_error=True, status="error", reason="separation_timeout")
        except (OSError, KeyError, ValueError, TypeError):
            return CapabilityResult(is_error=True, status="error", reason="separation_execution_failed")
        finally:
            if work is not None and not succeeded:
                try:
                    shutil.rmtree(work)
                except OSError:
                    logging.getLogger(__name__).warning("separation_temporary_cleanup_failed")

    async def aclose(self):
        await self.runner.aclose()
        if self.local is not None:
            await self.local.aclose()
        if self.remote is not None:
            await self.remote.aclose()


class AudioSeparationPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.1.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS)

    def register(self, registrar):
        registrar.add_capability_adapter(AudioSeparation(registrar.get_resource_port()))
