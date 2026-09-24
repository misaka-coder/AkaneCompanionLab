"""Public SDK adapter; provider protocol stays in the independent client."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

from akane_plugin import (
    CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus, Services, ServiceDependency,
    AKANE_PLUGIN_API_VERSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
    PluginResultExperience,
    PluginResultPayload,
)

from .client import ImageClient
from .types import ImageError, inspect_image, runtime_health

PLUGIN_ID = "akane.image-generation"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
SERVICE_ID = "image_generation"
SERVICE_CAPABILITY_ID = PLUGIN_ID + ".service.image_generation.v1.generate"
PERMISSIONS = (
    "capability.prompt.invoke",
    "capability.invoke",
    "service.provide",
    "resource.read",
    "artifact.write",
    "network.read",
    "connection.image_generation.read",
)

OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["requested_count", "output_count", "images", "reference_handles", "mask_handle", "notices"],
    "properties": {
        "requested_count": {"type": "integer", "minimum": 1, "maximum": 4},
        "output_count": {"type": "integer", "minimum": 1, "maximum": 4},
        "images": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False, "required": ["width", "height", "format"],
            "properties": {"width": {"type": "integer", "minimum": 1}, "height": {"type": "integer", "minimum": 1},
                           "format": {"enum": ["png", "jpeg", "webp"]}},
        }},
        "reference_handles": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "mask_handle": {"type": "string"},
        "notices": {"type": "array", "items": {"type": "string"}},
    },
}


def descriptor():
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="图片生成与参考图编辑",
        short_hint="仅在用户明确要求生成或编辑图片时，使用已配置的图片服务生成 1–4 张图；可提供当前会话的 1–5 个参考图句柄和 PNG 透明通道蒙版，蒙版尺寸须匹配首参考图。支持 PNG/JPEG/WEBP、尺寸/质量/压缩参数；GIF 可作参考。会向图片服务发送提示及所选参考图，可能计费。默认登记不发送，可用原 load_material 查看或继续编辑精确生成句柄；取消需等待远端完成，连接中断不能证明推理已停止。",
        visible_in=("desktop", "qq"),
        prompt_exposed=True,
        risk="medium",
        confirm="first_time",
        effects=("network", "filesystem"),
        trigger=None,
        inputs=(
            CapabilityIOSlot("prompt", "string", required=True, raw={"minLength": 1, "maxLength": 4000}),
            CapabilityIOSlot(
                "reference_images",
                "array",
                raw={"items": {"type": "string", "minLength": 1, "maxLength": 512}, "maxItems": 5},
            ),
            CapabilityIOSlot("mask_image", "string", raw={"maxLength": 512}),
            CapabilityIOSlot("size", "string", raw={"pattern": r"^(auto|[0-9]{3,4}x[0-9]{3,4})$"}),
            CapabilityIOSlot("quality", "string", raw={"enum": ("auto", "low", "medium", "high")}),
            CapabilityIOSlot("background", "string", raw={"enum": ("auto", "opaque")}),
            CapabilityIOSlot("output_format", "string", raw={"enum": ("png", "jpeg", "webp")}),
            CapabilityIOSlot("compression", "integer", raw={"minimum": 0, "maximum": 100}),
            CapabilityIOSlot("input_fidelity", "string", raw={"enum": ("auto", "low", "high")}),
            CapabilityIOSlot("n", "integer", raw={"minimum": 1, "maximum": 4}),
            CapabilityIOSlot("output_title", "string", raw={"maxLength": 80}),
            CapabilityIOSlot("send_to_user", "boolean"),
        ),
        outputs=(
            CapabilityIOSlot("files", "file", required=True, max_bytes=100 * 1024 * 1024, delivery="generated_file"),
        ),
        output_schema=OUTPUT_SCHEMA,
        raw={
            "execution_class": "long_task",
            "followup": "required",
            "memory_mode": "timeline",
            "idempotency": "effectful",
        },
    )


def service_descriptor():
    tool = descriptor()
    return replace(tool, id=SERVICE_CAPABILITY_ID, prompt_exposed=False,
                   raw={**tool.raw, "followup": "none",
                        "service": {"service_id": SERVICE_ID, "version": 1, "method": "generate"}})


class ImageGeneration:
    provider_id = "provider.akane.image-generation"

    def __init__(self, resources, connections, capabilities):
        self.resources, self.connections = resources, connections
        self.services = Services(capabilities)
        self._clients = set()
        self._closed = False

    async def health(self):
        if not callable(getattr(self.resources, "work_directory", None)):
            return HealthStatus(False, "unavailable", "resource_work_directory_sdk_required")
        result = runtime_health()
        return HealthStatus(result["status"] == "runtime_ready", result["status"], result["reason"])

    async def list_capabilities(self):
        return (descriptor(), service_descriptor())

    async def _read(self, target, limit):
        if not isinstance(target, str) or not 1 <= len(target) <= 512 or any(c in target for c in "/\\\0\r\n"):
            raise ImageError("image_resource_handle_invalid")
        source = await self.resources.open(target)
        if not source.ok:
            raise ImageError("image_resource_unavailable")
        if source.file_size <= 0 or source.file_size > limit:
            raise ImageError("reference_image_size_limit")
        with source.path.open("rb") as stream:
            data = stream.read(limit + 1)
        return inspect_image(data, reference=True, max_bytes=limit), source.handle

    async def invoke(self, capability_id, args, context):
        del context
        if capability_id == CAPABILITY_ID:
            return await self.services.call_result(SERVICE_ID, "generate", dict(args))
        if capability_id != SERVICE_CAPABILITY_ID:
            return CapabilityResult(is_error=True, status="not_found", reason="unknown_capability")
        runtime = None
        try:
            if self._closed:
                raise ImageError("image_client_closed")
            targets = args.get("reference_images", [])
            if not isinstance(targets, list) or len(targets) > 5:
                raise ImageError("reference_image_limit")
            title = args.get("output_title") or "生成图片"
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 80
                or any(c in title for c in "/\\\0\r\n")
            ):
                raise ImageError("invalid_output_title")
            if not isinstance(args.get("send_to_user", False), bool):
                raise ImageError("invalid_image_delivery")
            connection = await self.connections.resolve("image_generation")
            if not connection.ok:
                return CapabilityResult(is_error=True, status=connection.status, reason=connection.reason)
            options = connection.options
            if args.get("n", 1) > min(4, int(options.get("max_output_images", 4))):
                raise ImageError("image_output_count_limit")
            limit = max(1024, min(8 * 1024 * 1024, int(options.get("max_image_bytes", 8 * 1024 * 1024))))
            refs, handles = [], []
            for target in targets:
                image, handle = await self._read(target, limit)
                refs.append(image)
                handles.append(handle)
            mask, mask_handle = None, ""
            if args.get("mask_image"):
                mask, mask_handle = await self._read(args["mask_image"], limit)
            runtime = ImageClient(
                base_url=connection.base_url,
                api_key=connection.api_key,
                model=connection.model,
                timeout_seconds=options.get("timeout_seconds", 300),
                max_output_bytes=options.get("max_output_bytes", 25 * 1024 * 1024),
                max_input_bytes=limit,
                max_input_images=options.get("max_input_images", 5),
                max_total_input_bytes=options.get("max_total_input_bytes", 20 * 1024 * 1024),
                transient_retry_count=options.get("transient_retry_count", 2),
                allow_loopback_http=options.get("allow_loopback_http", False) is True,
            )
            self._clients.add(runtime)
            result = await runtime.generate(
                **{
                    name: args[name]
                    for name in (
                        "prompt",
                        "n",
                        "size",
                        "quality",
                        "background",
                        "output_format",
                        "compression",
                        "input_fidelity",
                    )
                    if name in args
                },
                references=refs,
                mask=mask,
            )
            root = await self.resources.work_directory()
            drafts, metadata = [], []
            for index, image in enumerate(result.images, 1):
                output = root / f"image-{uuid4().hex}.{image.output_format}"
                output.write_bytes(image.data)
                item_title = title if len(result.images) == 1 else f"{title}_{index}"
                summary = f"生成图片 {index}，{image.width}×{image.height}，{image.output_format}。"
                drafts.append(
                    ManagedArtifactDraft(
                        path=output,
                        title=item_title,
                        output_format=image.output_format,
                        mime_type=image.media_type,
                        summary=summary,
                        send_to_user=args.get("send_to_user", False),
                    )
                )
                metadata.append({"width": image.width, "height": image.height, "format": image.output_format})
            summary = f"图片服务已返回 {len(result.images)} 张最终图片（请求 {result.requested_count} 张）。"
            facts = [
                "结果已交给原文件登记链路；登记不等于已发送。可用精确生成句柄查看或继续参考图编辑，原始材料未修改。"
            ]
            if "provider_returned_fewer_images" in result.notices:
                facts.append("实际返回少于请求张数；没有把预览图算作最终结果，也没有自动补发付费请求。")
            if "provider_uploaded_file_cleanup_failed" in result.notices:
                facts.append("图片已完成，但本次兼容上传的临时文件未全部确认删除；不能声称远端材料已清理。")
            if "provider_output_dimensions_changed" in result.notices:
                facts.append("服务返回的实际尺寸与请求不一致；已按真实像素尺寸登记，没有暗中缩放或声称尺寸要求已满足。")
            if "provider_output_format_changed" in result.notices:
                facts.append("服务返回的实际格式与请求不一致；文件扩展名和 MIME 以真实解码结果为准。")
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=ManagedArtifactPayload(
                    artifacts=tuple(drafts),
                    content=PluginResultPayload(
                        content={
                            "requested_count": result.requested_count,
                            "output_count": len(result.images),
                            "images": metadata,
                            "reference_handles": handles,
                            "mask_handle": mask_handle,
                            "notices": list(result.notices),
                        },
                        experience=PluginResultExperience(summary=summary, facts=tuple(facts)),
                    ),
                ),
            )
        except ImageError as exc:
            return CapabilityResult(
                is_error=True,
                status="error",
                reason=exc.code,
                content={
                    "retryable": exc.retryable,
                    "provider_http_status": exc.http_status,
                    "rejected_parameter": exc.rejected_parameter,
                    "notices": list(exc.notices),
                },
            )
        except (OSError, ValueError, TypeError, KeyError):
            return CapabilityResult(is_error=True, status="error", reason="image_execution_failed")
        finally:
            if runtime is not None:
                await runtime.aclose()
                self._clients.discard(runtime)

    async def aclose(self):
        self._closed = True
        closing = asyncio.gather(*(client.aclose() for client in tuple(self._clients)), return_exceptions=True)
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                continue
        closing.result()


class ImageGenerationPlugin:
    manifest = PluginManifest(PLUGIN_ID, "0.2.0", AKANE_PLUGIN_API_VERSION, PERMISSIONS,
                              requires_services=(ServiceDependency(SERVICE_ID),))

    def register(self, registrar):
        registrar.add_capability_adapter(
            ImageGeneration(registrar.get_resource_port(), registrar.get_connection_port(), registrar.get_capability_port())
        )


def create_plugin():
    return ImageGenerationPlugin()
