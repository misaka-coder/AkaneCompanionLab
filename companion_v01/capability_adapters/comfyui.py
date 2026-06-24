from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from companion_v01.local_workflow_execution import WorkflowExecutionAsset, detect_workflow_image_extension
from companion_v01.local_workflow_runners.comfyui import (
    ComfyUiClient,
    ComfyUiClientError,
    ComfyUiSlotMappingError,
    apply_comfyui_input_slots,
    extract_comfyui_output_images,
)

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityManifest,
    CapabilityProtocolError,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
)


@dataclass(frozen=True)
class ComfyUiWorkflowCapability:
    capability_id: str
    display_name: str
    workflow_path: Path
    slot_mapping: Mapping[str, Any]
    input_image_arg: str = "image"
    output_handle_arg: str = "output_handle"
    input_slot: str = "input_image_handle"
    output_slot: str = "output_image_handle"
    risk: str = "medium"
    confirm: str = "first_time"
    prompt_exposed: bool = False
    short_hint: str = ""
    raw: Mapping[str, Any] | None = None


class ComfyUiCapabilityAdapter:
    type = "comfyui"

    def __init__(
        self,
        *,
        provider_id: str,
        endpoint: str,
        capabilities: tuple[ComfyUiWorkflowCapability, ...],
        client_factory: Any = None,
        sleep: Any = None,
        poll_interval_seconds: float = 1.0,
        max_poll_seconds: float = 60.0,
    ) -> None:
        self.provider_id = str(provider_id or "comfyui").strip() or "comfyui"
        self.endpoint = str(endpoint or "").strip()
        self.capabilities = tuple(capabilities)
        self.client_factory = client_factory or ComfyUiClient
        self.sleep = sleep or time.sleep
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds or 1.0))
        self.max_poll_seconds = max(self.poll_interval_seconds, float(max_poll_seconds or 60.0))
        self._capability_by_id = {item.capability_id: item for item in self.capabilities}

    async def health(self) -> HealthStatus:
        if not self.endpoint:
            return HealthStatus(ok=False, status="missing_config", reason="comfyui_endpoint_missing")
        try:
            self.client_factory(self.endpoint)
        except Exception:
            return HealthStatus(ok=False, status="invalid_config", reason="comfyui_endpoint_invalid")
        return HealthStatus(ok=True, status="configured")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._descriptor(item) for item in self.capabilities)

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        capability = self._capability_by_id.get(str(capability_id or "").strip())
        if capability is None:
            raise CapabilityProtocolError("unknown_capability")
        try:
            output_asset = self._run_workflow(capability, args)
        except ComfyUiSlotMappingError as exc:
            raise CapabilityProtocolError("workflow_slot_mapping_invalid") from exc
        except ComfyUiClientError as exc:
            raise CapabilityProtocolError("comfyui_request_failed") from exc
        except ValueError as exc:
            raise CapabilityProtocolError(str(exc) or "workflow_runtime_config_invalid") from exc
        except Exception as exc:
            raise CapabilityProtocolError("workflow_runner_failed") from exc
        return CapabilityResult(
            is_error=False,
            status="ok",
            content={
                "outputs": [
                    {
                        "handle": output_asset.handle,
                        "kind": "image",
                        "contentType": output_asset.content_type,
                    }
                ],
                "outputAssets": [output_asset],
            },
        )

    async def aclose(self) -> None:
        return None

    def _run_workflow(
        self,
        capability: ComfyUiWorkflowCapability,
        args: Mapping[str, Any],
    ) -> WorkflowExecutionAsset:
        image_bytes = args.get(capability.input_image_arg)
        if isinstance(image_bytes, bytearray):
            image_bytes = bytes(image_bytes)
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ValueError("input_image_bytes_required")
        output_handle = str(args.get(capability.output_handle_arg) or "comfyui_output").strip()
        if not output_handle:
            raise ValueError("output_handle_required")

        workflow_json = self._read_workflow_json(capability.workflow_path)
        client = self.client_factory(self.endpoint)
        uploaded_image = client.upload_image(
            image_bytes,
            filename=str(args.get("upload_filename") or "") or self._upload_filename(capability.capability_id, image_bytes),
            subfolder="akane",
            image_type="input",
            overwrite=True,
            content_type=str(args.get("content_type") or args.get("mime_type") or ""),
        )
        patched_workflow = apply_comfyui_input_slots(
            workflow_json,
            capability.slot_mapping,
            {
                capability.input_slot: uploaded_image.filename,
                capability.output_slot: output_handle,
            },
        )
        prompt_id = client.queue_prompt(
            patched_workflow,
            client_id=str(args.get("client_id") or "") or self._client_id(capability.capability_id),
        )
        output_ref = self._wait_for_first_output_image(client, prompt_id)
        image = client.get_image(output_ref)
        return WorkflowExecutionAsset(handle=output_handle, data=image.data, content_type=image.content_type)

    def _descriptor(self, capability: ComfyUiWorkflowCapability) -> CapabilityDescriptor:
        risk = capability.risk if capability.risk in {"low", "medium", "high"} else "medium"
        confirm = capability.confirm if capability.confirm in {"never", "first_time", "always"} else "first_time"
        if risk == "high":
            confirm = "always"
        return CapabilityDescriptor(
            id=capability.capability_id,
            display_name=capability.display_name or capability.capability_id,
            short_hint=capability.short_hint,
            visible_in=("desktop", "web"),
            prompt_exposed=bool(capability.prompt_exposed),
            risk=risk,
            confirm=confirm,
            effects=("media_generation",),
            trigger=None,
            inputs=(
                CapabilityIOSlot(name=capability.input_image_arg, kind="image_bytes", required=True),
                CapabilityIOSlot(name=capability.output_handle_arg, kind="string", required=False),
            ),
            outputs=(CapabilityIOSlot(name="image", kind="image_bytes", delivery="generated_file"),),
            raw={
                **dict(capability.raw or {}),
                "workflowPath": str(capability.workflow_path),
                "slotMapping": dict(capability.slot_mapping),
            },
        )

    def _read_workflow_json(self, workflow_path: Path) -> dict[str, Any]:
        path = Path(workflow_path)
        if not path.is_file():
            raise ValueError("workflow_file_missing")
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("workflow_file_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("workflow_json_invalid")
        return dict(payload)

    def _wait_for_first_output_image(self, client: ComfyUiClient, prompt_id: str) -> Any:
        deadline = time.monotonic() + self.max_poll_seconds
        last_history: dict[str, Any] = {}
        while time.monotonic() <= deadline:
            last_history = client.get_history(prompt_id)
            images = extract_comfyui_output_images(last_history, prompt_id=prompt_id)
            if images:
                return images[0]
            self.sleep(self.poll_interval_seconds)
        images = extract_comfyui_output_images(last_history, prompt_id=prompt_id) if last_history else []
        if images:
            return images[0]
        raise ComfyUiClientError("comfyui_output_timeout")

    @staticmethod
    def _upload_filename(capability_id: str, image_bytes: bytes) -> str:
        extension = detect_workflow_image_extension(image_bytes) or "png"
        safe_suffix = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in capability_id)[-40:] or "workflow"
        return f"akane_{safe_suffix}_input.{extension}"

    @staticmethod
    def _client_id(capability_id: str) -> str:
        safe_suffix = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in capability_id)[-48:] or "workflow"
        return f"akane-{safe_suffix}"


def build_comfyui_adapter_from_manifest(
    manifest: CapabilityManifest,
    *,
    client_factory: Any = None,
    sleep: Any = None,
    poll_interval_seconds: float = 1.0,
    max_poll_seconds: float = 60.0,
) -> ComfyUiCapabilityAdapter:
    if manifest.provider_type != "comfyui":
        raise ValueError("manifest_provider_type_not_comfyui")
    if manifest.endpoint is None or not manifest.endpoint.url:
        raise ValueError("comfyui_endpoint_missing")
    capabilities: list[ComfyUiWorkflowCapability] = []
    for descriptor in manifest.capabilities:
        raw = descriptor.raw if isinstance(descriptor.raw, Mapping) else {}
        workflow_value = raw.get("workflow_template") or raw.get("workflowPath") or raw.get("workflow_path")
        slot_mapping = raw.get("slot_mapping") or raw.get("slotMapping")
        if not workflow_value or not isinstance(slot_mapping, Mapping):
            raise ValueError("comfyui_workflow_binding_missing")
        capabilities.append(
            ComfyUiWorkflowCapability(
                capability_id=descriptor.id,
                display_name=descriptor.display_name,
                workflow_path=_resolve_manifest_child(manifest.source_path.parent, workflow_value),
                slot_mapping=dict(slot_mapping),
                risk=descriptor.risk,
                confirm=descriptor.confirm,
                prompt_exposed=descriptor.prompt_exposed,
                short_hint=descriptor.short_hint,
                raw=raw,
            )
        )
    return ComfyUiCapabilityAdapter(
        provider_id=manifest.provider_id,
        endpoint=manifest.endpoint.url,
        capabilities=tuple(capabilities),
        client_factory=client_factory,
        sleep=sleep,
        poll_interval_seconds=poll_interval_seconds,
        max_poll_seconds=max_poll_seconds,
    )


def _resolve_manifest_child(base_dir: Path, value: Any) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "://" in raw:
        raise ValueError("workflow_template_must_be_relative")
    candidate = (Path(base_dir) / raw).resolve()
    root = Path(base_dir).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("workflow_template_outside_manifest_dir")
    return candidate
