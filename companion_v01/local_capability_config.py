from __future__ import annotations

import json
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse, urlunparse


CONFIG_SCHEMA_VERSION = 1
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
PROFILE_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PUBLIC_PROVIDER_FIELDS = {"enabled", "endpoint", "updatedAt", "lastHealth"}
PUBLIC_WORKFLOW_FIELDS = {"enabled", "workflowPath", "slotMapping", "updatedAt"}
WORKFLOW_PATH_MAX_LENGTH = 220
WORKFLOW_SLOT_MAX_LENGTH = 80
WORKFLOW_SLOT_VALUE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
WORKFLOW_ASSET_HANDLE_MAX_LENGTH = 120
WORKFLOW_ASSET_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


HealthChecker = Callable[[str, int, float], tuple[bool, str]]


@dataclass(frozen=True)
class ProviderConfigSpec:
    id: str
    name: str
    type: str
    adapter: str
    default_endpoint: str
    used_by: tuple[str, ...]
    risk: str = "medium"


@dataclass(frozen=True)
class WorkflowConfigSpec:
    id: str
    capability_id: str
    workflow_id: str
    name: str
    description: str
    type: str
    adapter: str
    provider_id: str
    group: str
    used_by: tuple[str, ...]
    risk: str
    target: str
    output: str
    default_workflow_path: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]


CONFIGURABLE_PROVIDER_SPECS: tuple[ProviderConfigSpec, ...] = (
    ProviderConfigSpec(
        id="provider.comfyui.local",
        name="本地 ComfyUI",
        type="asset_processor",
        adapter="comfyui",
        default_endpoint="http://127.0.0.1:8188",
        used_by=("workshop", "image", "desktop_pet"),
    ),
    ProviderConfigSpec(
        id="provider.tts.gpt_sovits.local",
        name="本地 GPT-SoVITS",
        type="tts_provider",
        adapter="gpt_sovits",
        default_endpoint="http://127.0.0.1:9880",
        used_by=("voice", "desktop_pet"),
    ),
)

CONFIGURABLE_PROVIDER_BY_ID = {spec.id: spec for spec in CONFIGURABLE_PROVIDER_SPECS}

CONFIGURABLE_WORKFLOW_SPECS: tuple[WorkflowConfigSpec, ...] = (
    WorkflowConfigSpec(
        id="workflow.workshop.portrait.cutout",
        capability_id="workshop.portrait.cutout",
        workflow_id="workflow.comfyui.portrait_cutout",
        name="透明背景处理",
        description="角色工坊的立绘透明背景处理流程，绑定本地 ComfyUI 工作流后可用于角色素材整理。",
        type="asset_processor",
        adapter="comfyui",
        provider_id="provider.comfyui.local",
        group="workshop",
        used_by=("workshop", "desktop_pet"),
        risk="medium",
        target="character_pack_assets",
        output="transparent_png",
        default_workflow_path="workflows/comfyui/portrait_cutout.json",
        required_slots=("input_image_handle", "output_image_handle"),
        optional_slots=("mask_output_handle", "background_color", "padding", "alpha_threshold"),
    ),
)

CONFIGURABLE_WORKFLOW_BY_ID = {spec.id: spec for spec in CONFIGURABLE_WORKFLOW_SPECS}


def list_provider_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    providers = [
        build_provider_config_entry(spec, config.get("providers", {}).get(spec.id))
        for spec in CONFIGURABLE_PROVIDER_SPECS
    ]
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "config-skeleton",
        "configStatus": config.get("configStatus") or "available",
        "warnings": list(config.get("warnings") or []),
        "configScope": _public_config_scope(profile_user_id),
        "providers": providers,
        "summary": _summarize_provider_entries(providers),
    }


def get_provider_config_entries(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> list[dict[str, Any]]:
    return list_provider_configs(base_dir=base_dir, profile_user_id=profile_user_id)["providers"]


def list_workflow_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflows = [
        build_workflow_config_entry(
            spec,
            config.get("workflows", {}).get(spec.id),
            providers_by_id.get(spec.provider_id),
        )
        for spec in CONFIGURABLE_WORKFLOW_SPECS
    ]
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "config-skeleton",
        "configStatus": config.get("configStatus") or "available",
        "warnings": list(config.get("warnings") or []),
        "configScope": _public_config_scope(profile_user_id),
        "workflows": workflows,
        "summary": _summarize_workflow_entries(workflows),
    }


def save_provider_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    provider_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_PROVIDER_BY_ID.get(str(provider_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_provider", "providerId": str(provider_id or "").strip()}

    normalized = normalize_provider_config_payload(spec, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "providerId": spec.id,
            "reason": normalized.get("reason") or "invalid_provider_config",
        }

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    providers = dict(config.get("providers") or {})
    existing = providers.get(spec.id) if isinstance(providers.get(spec.id), dict) else {}
    next_provider = {
        "enabled": bool(normalized["enabled"]),
        "endpoint": normalized["endpoint"],
        "updatedAt": _now_iso(),
    }
    if (
        existing.get("endpoint") == normalized["endpoint"]
        and isinstance(existing.get("lastHealth"), Mapping)
    ):
        next_provider["lastHealth"] = existing["lastHealth"]
    providers[spec.id] = next_provider
    config = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "providers": providers,
        "workflows": config.get("workflows", {}),
    }
    write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id, config=config)
    return {
        "ok": True,
        "status": "saved",
        "providerId": spec.id,
        "autoEnable": False,
        "configScope": _public_config_scope(profile_user_id),
        "provider": build_provider_config_entry(spec, next_provider),
    }


def check_provider_health(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    provider_id: str,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float = 0.35,
    health_checker: HealthChecker | None = None,
) -> dict[str, Any]:
    spec = CONFIGURABLE_PROVIDER_BY_ID.get(str(provider_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_provider", "providerId": str(provider_id or "").strip()}

    payload = payload or {}
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": False,
            "reason": config.get("reason") or "provider_config_file_invalid",
        }

    saved = config.get("providers", {}).get(spec.id)
    endpoint_value = payload.get("endpoint") if "endpoint" in payload else None
    endpoint = str(endpoint_value or (saved or {}).get("endpoint") or "").strip()
    if not endpoint:
        return {
            "ok": False,
            "status": "missing_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": bool((saved or {}).get("enabled")),
            "reason": "provider_endpoint_missing",
        }

    normalized_endpoint = normalize_local_http_endpoint(endpoint)
    if not normalized_endpoint["ok"]:
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": spec.id,
            "autoEnable": False,
            "enabled": bool((saved or {}).get("enabled")),
            "reason": normalized_endpoint.get("reason") or "invalid_endpoint",
        }

    parsed = urlparse(normalized_endpoint["endpoint"])
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    checker = health_checker or _socket_health_check
    try:
        ready, reason = checker(parsed.hostname or "", port, max(0.05, float(timeout_seconds)))
    except Exception as exc:
        ready = False
        reason = _safe_reason(str(exc) or "health_check_failed")

    status = "ready" if ready else "unreachable"
    last_health = {
        "status": status,
        "checkedAt": _now_iso(),
        "endpoint": normalized_endpoint["endpoint"],
        "reason": "" if ready else _safe_reason(reason or "connection_failed"),
    }
    if saved and endpoint_value is None:
        providers = dict(config.get("providers") or {})
        providers[spec.id] = {**saved, "lastHealth": last_health}
        write_capability_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            config={
                "schemaVersion": CONFIG_SCHEMA_VERSION,
                "providers": providers,
                "workflows": config.get("workflows", {}),
            },
        )

    return {
        "ok": ready,
        "status": status,
        "providerId": spec.id,
        "autoEnable": False,
        "enabled": bool((saved or {}).get("enabled")),
        "endpoint": normalized_endpoint["endpoint"],
        "reason": "" if ready else last_health["reason"],
    }


def save_workflow_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    normalized = normalize_workflow_config_payload(spec, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "workflowId": spec.id,
            "reason": normalized.get("reason") or "invalid_workflow_config",
        }

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }

    workflows = dict(config.get("workflows") or {})
    workflows[spec.id] = {
        "enabled": bool(normalized["enabled"]),
        "workflowPath": normalized["workflowPath"],
        "slotMapping": normalized["slotMapping"],
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "providers": config.get("providers", {}),
            "workflows": workflows,
        },
    )
    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflow = build_workflow_config_entry(spec, workflows[spec.id], providers_by_id.get(spec.provider_id))
    return {
        "ok": True,
        "status": "saved",
        "workflowId": spec.id,
        "executionReady": False,
        "autoEnable": False,
        "configScope": _public_config_scope(profile_user_id),
        "workflow": workflow,
    }


def validate_workflow_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "executionReady": False,
        }

    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    workflow = build_workflow_config_entry(
        spec,
        config.get("workflows", {}).get(spec.id),
        providers_by_id.get(spec.provider_id),
    )
    checks = {
        "providerConfigured": bool(providers_by_id.get(spec.provider_id, {}).get("configured")),
        "workflowConfigured": bool(workflow.get("configured")),
        "requiredSlots": _required_slots_present(spec, workflow.get("slotMapping")),
        "executionReady": False,
    }
    if not checks["workflowConfigured"]:
        return {
            "ok": False,
            "status": workflow.get("status") or "missing_workflow",
            "workflowId": spec.id,
            "reason": workflow.get("reason") or "workflow_binding_missing",
            "executionReady": False,
            "checks": checks,
            "workflow": workflow,
        }
    return {
        "ok": True,
        "status": "validated_config",
        "workflowId": spec.id,
        "reason": "workflow_runtime_not_bound",
        "executionReady": False,
        "checks": checks,
        "workflow": workflow,
    }


def preflight_workflow_execution(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": config.get("reason") or "provider_config_file_invalid",
            "executionReady": False,
            "canRun": False,
        }

    provider_entries = get_provider_config_entries(base_dir=base_dir, profile_user_id=profile_user_id)
    providers_by_id = {entry["id"]: entry for entry in provider_entries}
    provider = providers_by_id.get(spec.provider_id, {})
    workflow = build_workflow_config_entry(
        spec,
        config.get("workflows", {}).get(spec.id),
        provider,
    )
    checks = {
        "providerConfigured": bool(provider.get("configured")),
        "providerEnabled": bool(provider.get("enabled")),
        "providerAvailable": str(provider.get("status") or "") in {"configured", "ready"},
        "workflowConfigured": bool(workflow.get("configured")),
        "workflowEnabled": bool(workflow.get("enabled")),
        "requiredSlots": _required_slots_present(spec, workflow.get("slotMapping")),
        "inputImageHandle": False,
        "outputImageHandle": False,
        "runnerBound": False,
    }
    if not (checks["providerConfigured"] and checks["workflowConfigured"] and checks["workflowEnabled"]):
        return {
            "ok": False,
            "status": workflow.get("status") or "missing_workflow",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": workflow.get("reason") or "workflow_binding_missing",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }
    if not checks["providerAvailable"]:
        return {
            "ok": False,
            "status": workflow.get("status") or provider.get("status") or "unavailable",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": workflow.get("reason") or provider.get("reason") or "provider_unavailable",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }

    input_handle = normalize_workflow_asset_handle(
        payload.get("inputImageHandle") or payload.get("inputAssetHandle") or payload.get("sourceAssetHandle")
    )
    output_handle = normalize_workflow_asset_handle(
        payload.get("outputImageHandle") or payload.get("outputAssetHandle") or payload.get("targetAssetHandle")
    )
    checks["inputImageHandle"] = bool(input_handle.get("ok"))
    checks["outputImageHandle"] = bool(output_handle.get("ok"))
    if not input_handle.get("ok") or not output_handle.get("ok"):
        return {
            "ok": False,
            "status": "invalid_request",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": input_handle.get("reason") if not input_handle.get("ok") else output_handle.get("reason"),
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": workflow,
        }

    return {
        "ok": False,
        "status": "not-implemented",
        "workflowId": spec.id,
        "capabilityId": spec.capability_id,
        "reason": "workflow_runner_not_bound",
        "executionReady": False,
        "canRun": False,
        "checks": checks,
        "acceptedInputs": {
            "inputImageHandle": input_handle["handle"],
            "outputImageHandle": output_handle["handle"],
        },
        "workflow": workflow,
    }


def build_provider_config_entry(spec: ProviderConfigSpec, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    config_status = str(config.get("status") or "").strip()
    endpoint = _safe_endpoint_for_output(config.get("endpoint"))
    enabled = bool(config.get("enabled")) if config_status != "invalid_config" else False
    last_health = config.get("lastHealth") if isinstance(config.get("lastHealth"), Mapping) else {}
    configured = bool(endpoint)
    status = "invalid_config" if config_status == "invalid_config" else _provider_status(
        configured=configured,
        enabled=enabled,
        last_health=last_health,
    )
    return {
        "id": spec.id,
        "kind": "provider",
        "type": spec.type,
        "source": "external_executor",
        "adapter": spec.adapter,
        "executionMode": "external",
        "name": spec.name,
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "reason": _provider_reason(status, last_health, config),
        "risk": spec.risk,
        "requiresConfirmation": False,
        "usedBy": list(spec.used_by),
        "endpoint": endpoint,
        "defaultEndpoint": spec.default_endpoint,
        "autoEnabled": False,
        "configurable": True,
    }


def build_workflow_config_entry(
    spec: WorkflowConfigSpec,
    config: Mapping[str, Any] | None,
    provider_entry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    config_status = str(config.get("status") or "").strip()
    workflow_path = _safe_workflow_path_for_output(config.get("workflowPath"))
    slot_mapping = _safe_slot_mapping_for_output(spec, config.get("slotMapping"))
    enabled = bool(config.get("enabled")) if config_status != "invalid_config" else False
    configured = bool(workflow_path and _required_slots_present(spec, slot_mapping))
    status = _workflow_status(
        spec=spec,
        config=config,
        config_status=config_status,
        provider_entry=provider_entry,
        workflow_path=workflow_path,
        slot_mapping=slot_mapping,
        enabled=enabled,
        configured=configured,
    )
    return {
        "id": spec.id,
        "kind": "workflow",
        "type": spec.type,
        "source": "external_executor",
        "adapter": spec.adapter,
        "executionMode": "external",
        "capabilityId": spec.capability_id,
        "workflowId": spec.workflow_id,
        "providerId": spec.provider_id,
        "name": spec.name,
        "description": spec.description,
        "group": spec.group,
        "enabled": enabled,
        "configured": configured,
        "configurable": True,
        "executionReady": False,
        "status": status,
        "reason": _workflow_reason(status, config, provider_entry),
        "risk": spec.risk,
        "requiresConfirmation": False,
        "usedBy": list(spec.used_by),
        "target": spec.target,
        "output": spec.output,
        "workflowPath": workflow_path,
        "defaultWorkflowPath": spec.default_workflow_path,
        "slotMapping": slot_mapping,
        "slots": {
            "required": list(spec.required_slots),
            "optional": list(spec.optional_slots),
        },
        "inputSchema": {
            "inputImage": "asset_handle",
            "outputImage": "asset_handle",
            "pathPolicy": "safe-handle-only",
        },
    }


def normalize_provider_config_payload(spec: ProviderConfigSpec, payload: Mapping[str, Any]) -> dict[str, Any]:
    endpoint_value = payload.get("endpoint")
    if endpoint_value is None:
        endpoint_value = spec.default_endpoint
    normalized_endpoint = normalize_local_http_endpoint(str(endpoint_value or "").strip())
    if not normalized_endpoint["ok"]:
        return normalized_endpoint
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled")),
        "endpoint": normalized_endpoint["endpoint"],
    }


def normalize_workflow_config_payload(spec: WorkflowConfigSpec, payload: Mapping[str, Any]) -> dict[str, Any]:
    workflow_path_value = payload.get("workflowPath")
    if workflow_path_value is None:
        workflow_path_value = payload.get("workflowRef")
    if workflow_path_value is None:
        workflow_path_value = spec.default_workflow_path
    normalized_path = normalize_workflow_path(str(workflow_path_value or "").strip())
    if not normalized_path["ok"]:
        return normalized_path

    slot_payload = payload.get("slotMapping")
    if slot_payload is None:
        slot_payload = payload.get("slots")
    normalized_slots = normalize_workflow_slot_mapping(spec, slot_payload)
    if not normalized_slots["ok"]:
        return normalized_slots
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled")),
        "workflowPath": normalized_path["workflowPath"],
        "slotMapping": normalized_slots["slotMapping"],
    }


def normalize_local_http_endpoint(endpoint: str) -> dict[str, Any]:
    raw = str(endpoint or "").strip()
    if not raw:
        return {"ok": False, "status": "missing_config", "reason": "endpoint_required"}
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_must_be_http_localhost"}
    if parsed.username or parsed.password:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_credentials_not_allowed"}
    host = (parsed.hostname or "").strip().lower()
    if host not in LOOPBACK_HOSTS:
        return {"ok": False, "status": "invalid_config", "reason": "endpoint_must_be_loopback"}
    try:
        port = parsed.port
    except ValueError:
        return {"ok": False, "status": "invalid_config", "reason": "invalid_endpoint_port"}
    netloc_host = "127.0.0.1" if host in {"127.0.0.1", "localhost"} else "[::1]"
    netloc = f"{netloc_host}:{port}" if port else netloc_host
    return {
        "ok": True,
        "status": "valid",
        "endpoint": urlunparse((parsed.scheme, netloc, "", "", "", "")),
    }


def normalize_workflow_path(workflow_path: str) -> dict[str, Any]:
    raw = str(workflow_path or "").strip().replace("\\", "/")
    if not raw:
        return {"ok": False, "status": "missing_workflow", "reason": "workflow_path_required"}
    if len(raw) > WORKFLOW_PATH_MAX_LENGTH:
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_too_long"}
    lowered = raw.lower()
    if "://" in raw or "token" in lowered or "secret" in lowered or "password" in lowered or "api_key" in lowered:
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_safe_relative_json"}
    if raw.startswith("/") or raw.startswith("//") or re.match(r"^[A-Za-z]:/", raw):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_relative"}
    path = PurePosixPath(raw)
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_not_escape_scope"}
    if any(part.strip() != part or not part.strip() for part in parts):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_segment_invalid"}
    if not parts[-1].lower().endswith(".json"):
        return {"ok": False, "status": "invalid_workflow_config", "reason": "workflow_path_must_be_json"}
    return {
        "ok": True,
        "status": "valid",
        "workflowPath": "/".join(parts),
    }


def normalize_workflow_slot_mapping(spec: WorkflowConfigSpec, slot_mapping: Any) -> dict[str, Any]:
    if slot_mapping in (None, ""):
        slot_mapping = {slot: slot for slot in spec.required_slots}
    if not isinstance(slot_mapping, Mapping):
        return {"ok": False, "status": "missing_slot_mapping", "reason": "slot_mapping_must_be_object"}
    allowed = set(spec.required_slots) | set(spec.optional_slots)
    normalized: dict[str, str] = {}
    for slot_name, raw_value in slot_mapping.items():
        slot = str(slot_name or "").strip()
        if slot not in allowed:
            continue
        value = str(raw_value or "").strip()
        if not value:
            continue
        if not WORKFLOW_SLOT_VALUE_RE.match(value):
            return {"ok": False, "status": "invalid_workflow_config", "reason": "slot_mapping_value_invalid"}
        normalized[slot] = value
    missing = [slot for slot in spec.required_slots if not normalized.get(slot)]
    if missing:
        return {
            "ok": False,
            "status": "missing_slot_mapping",
            "reason": "required_slot_mapping_missing",
            "missingSlots": missing,
        }
    return {"ok": True, "status": "valid", "slotMapping": normalized}


def normalize_workflow_asset_handle(value: Any) -> dict[str, Any]:
    handle = str(value or "").strip()
    if not handle:
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_required"}
    lowered = handle.lower()
    if (
        len(handle) > WORKFLOW_ASSET_HANDLE_MAX_LENGTH
        or "://" in handle
        or "/" in handle
        or "\\" in handle
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_must_be_safe_opaque_id"}
    if not WORKFLOW_ASSET_HANDLE_RE.match(handle):
        return {"ok": False, "status": "invalid_request", "reason": "asset_handle_must_be_safe_opaque_id"}
    return {"ok": True, "status": "valid", "handle": handle}


def load_capability_config(*, base_dir: Path | str | None, profile_user_id: str) -> dict[str, Any]:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None or not path.exists():
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "missing",
            "providers": {},
            "workflows": {},
            "warnings": [],
        }
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_file_invalid_json",
            "providers": {},
            "workflows": {},
            "warnings": [{"status": "invalid_config", "reason": "provider_config_file_invalid_json"}],
        }
    if not isinstance(data, dict):
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_root_must_be_object",
            "providers": {},
            "workflows": {},
            "warnings": [{"status": "invalid_config", "reason": "provider_config_root_must_be_object"}],
        }
    providers, provider_warnings = _sanitize_provider_configs(data.get("providers"))
    workflows, workflow_warnings = _sanitize_workflow_configs(data.get("workflows"))
    warnings = [*provider_warnings, *workflow_warnings]
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "configStatus": "partial_invalid_config" if warnings else "available",
        "providers": providers,
        "workflows": workflows,
        "warnings": warnings,
    }


def write_capability_config(*, base_dir: Path | str | None, profile_user_id: str, config: Mapping[str, Any]) -> None:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_config_for_write(config), ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(payload)
        handle.write("\n")
    tmp_path.replace(path)


def _profile_config_path(base_dir: Path | str | None, profile_user_id: str) -> Path | None:
    if base_dir is None:
        return None
    root = Path(base_dir).resolve()
    profile = _safe_profile_id(profile_user_id)
    path = (root / profile / "capabilities" / "capabilities.yaml").resolve()
    if root not in path.parents:
        return None
    return path


def _safe_profile_id(profile_user_id: str) -> str:
    raw = str(profile_user_id or "").strip() or "default"
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-") or "default"
    return safe[:120]


def _public_config_scope(profile_user_id: str) -> dict[str, Any]:
    return {
        "profileUserId": _safe_profile_id(profile_user_id),
        "explicitConfigPath": PROFILE_CONFIG_PATH_TEMPLATE,
    }


def _provider_status(*, configured: bool, enabled: bool, last_health: Mapping[str, Any]) -> str:
    if not configured:
        return "missing_config"
    if not enabled:
        return "disabled"
    health_status = str(last_health.get("status") or "").strip()
    if health_status in {"ready", "unreachable"}:
        return health_status
    return "configured"


def _provider_reason(status: str, last_health: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    config = config if isinstance(config, Mapping) else {}
    if status == "invalid_config":
        return _safe_reason(config.get("reason") or "invalid_provider_config")
    if status == "missing_config":
        return "provider_endpoint_missing"
    if status == "disabled":
        return "provider_disabled"
    if status == "unreachable":
        return _safe_reason(last_health.get("reason") or "connection_failed")
    return ""


def _summarize_provider_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "byStatus": by_status,
    }


def _summarize_workflow_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "executionReady": sum(1 for entry in entries if entry.get("executionReady")),
        "byStatus": by_status,
    }


def _socket_health_check(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, ""
    except OSError as exc:
        return False, _safe_reason(str(exc) or "connection_failed")


def _sanitize_provider_configs(raw_providers: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    providers: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_providers in (None, ""):
        return providers, warnings
    if not isinstance(raw_providers, Mapping):
        return providers, [{"status": "invalid_config", "reason": "providers_must_be_object"}]

    for spec in CONFIGURABLE_PROVIDER_SPECS:
        if spec.id not in raw_providers:
            continue
        sanitized, warning = _sanitize_provider_config_entry(spec, raw_providers.get(spec.id))
        if sanitized:
            providers[spec.id] = sanitized
        if warning:
            warnings.append(warning)
    return providers, warnings


def _sanitize_workflow_configs(raw_workflows: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    workflows: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_workflows in (None, ""):
        return workflows, warnings
    if not isinstance(raw_workflows, Mapping):
        return workflows, [{"status": "invalid_config", "reason": "workflows_must_be_object"}]

    for spec in CONFIGURABLE_WORKFLOW_SPECS:
        if spec.id not in raw_workflows:
            continue
        sanitized, warning = _sanitize_workflow_config_entry(spec, raw_workflows.get(spec.id))
        if sanitized:
            workflows[spec.id] = sanitized
        if warning:
            warnings.append(warning)
    return workflows, warnings


def _sanitize_provider_config_entry(
    spec: ProviderConfigSpec,
    raw_config: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(raw_config, Mapping):
        return _invalid_provider_config(spec, "provider_config_must_be_object"), {
            "providerId": spec.id,
            "status": "invalid_config",
            "reason": "provider_config_must_be_object",
        }

    sanitized: dict[str, Any] = {"enabled": bool(raw_config.get("enabled"))}
    endpoint_value = str(raw_config.get("endpoint") or "").strip()
    if endpoint_value:
        normalized = normalize_local_http_endpoint(endpoint_value)
        if not normalized["ok"]:
            reason = normalized.get("reason") or "invalid_endpoint"
            return _invalid_provider_config(spec, reason), {
                "providerId": spec.id,
                "status": "invalid_config",
                "reason": reason,
            }
        sanitized["endpoint"] = normalized["endpoint"]

    updated_at = _safe_short_text(raw_config.get("updatedAt"))
    if updated_at:
        sanitized["updatedAt"] = updated_at

    last_health = _sanitize_last_health(raw_config.get("lastHealth"))
    if last_health:
        sanitized["lastHealth"] = last_health

    return sanitized, None


def _sanitize_workflow_config_entry(
    spec: WorkflowConfigSpec,
    raw_config: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(raw_config, Mapping):
        return _invalid_workflow_config(spec, "workflow_config_must_be_object"), {
            "workflowId": spec.id,
            "status": "invalid_config",
            "reason": "workflow_config_must_be_object",
        }

    sanitized: dict[str, Any] = {"enabled": bool(raw_config.get("enabled"))}
    workflow_path_value = str(raw_config.get("workflowPath") or "").strip()
    if workflow_path_value:
        normalized_path = normalize_workflow_path(workflow_path_value)
        if not normalized_path["ok"]:
            reason = normalized_path.get("reason") or "invalid_workflow_path"
            return _invalid_workflow_config(spec, reason), {
                "workflowId": spec.id,
                "status": "invalid_config",
                "reason": reason,
            }
        sanitized["workflowPath"] = normalized_path["workflowPath"]

    normalized_slots = normalize_workflow_slot_mapping(spec, raw_config.get("slotMapping"))
    if not normalized_slots["ok"] and workflow_path_value:
        reason = normalized_slots.get("reason") or "invalid_slot_mapping"
        return _invalid_workflow_config(spec, reason), {
            "workflowId": spec.id,
            "status": normalized_slots.get("status") or "invalid_config",
            "reason": reason,
        }
    if normalized_slots.get("ok"):
        sanitized["slotMapping"] = normalized_slots["slotMapping"]

    updated_at = _safe_short_text(raw_config.get("updatedAt"))
    if updated_at:
        sanitized["updatedAt"] = updated_at

    return sanitized, None


def _invalid_provider_config(spec: ProviderConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "providerId": spec.id}


def _invalid_workflow_config(spec: WorkflowConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "workflowId": spec.id}


def _sanitize_last_health(raw_last_health: Any) -> dict[str, Any]:
    if not isinstance(raw_last_health, Mapping):
        return {}
    status = str(raw_last_health.get("status") or "").strip()
    if status not in {"ready", "unreachable"}:
        return {}
    sanitized: dict[str, Any] = {"status": status}
    checked_at = _safe_short_text(raw_last_health.get("checkedAt"))
    if checked_at:
        sanitized["checkedAt"] = checked_at
    endpoint = _safe_endpoint_for_output(raw_last_health.get("endpoint"))
    if endpoint:
        sanitized["endpoint"] = endpoint
    if status == "unreachable":
        sanitized["reason"] = _safe_reason(raw_last_health.get("reason") or "connection_failed")
    else:
        sanitized["reason"] = ""
    return sanitized


def _config_for_write(config: Mapping[str, Any]) -> dict[str, Any]:
    raw_providers = config.get("providers") if isinstance(config.get("providers"), Mapping) else {}
    providers, _warnings = _sanitize_provider_configs(raw_providers)
    write_providers: dict[str, dict[str, Any]] = {}
    for provider_id, provider_config in providers.items():
        if provider_config.get("status") == "invalid_config":
            continue
        write_providers[provider_id] = {
            key: value
            for key, value in provider_config.items()
            if key in PUBLIC_PROVIDER_FIELDS and value not in (None, "")
        }
    raw_workflows = config.get("workflows") if isinstance(config.get("workflows"), Mapping) else {}
    workflows, _workflow_warnings = _sanitize_workflow_configs(raw_workflows)
    write_workflows: dict[str, dict[str, Any]] = {}
    for workflow_id, workflow_config in workflows.items():
        if workflow_config.get("status") == "invalid_config":
            continue
        write_workflows[workflow_id] = {
            key: value
            for key, value in workflow_config.items()
            if key in PUBLIC_WORKFLOW_FIELDS and value not in (None, "")
        }
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "providers": write_providers,
        "workflows": write_workflows,
    }


def _safe_endpoint_for_output(value: Any) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    normalized = normalize_local_http_endpoint(endpoint)
    return str(normalized.get("endpoint") or "") if normalized.get("ok") else ""


def _safe_workflow_path_for_output(value: Any) -> str:
    workflow_path = str(value or "").strip()
    if not workflow_path:
        return ""
    normalized = normalize_workflow_path(workflow_path)
    return str(normalized.get("workflowPath") or "") if normalized.get("ok") else ""


def _safe_slot_mapping_for_output(spec: WorkflowConfigSpec, value: Any) -> dict[str, str]:
    normalized = normalize_workflow_slot_mapping(spec, value)
    return dict(normalized.get("slotMapping") or {}) if normalized.get("ok") else {}


def _required_slots_present(spec: WorkflowConfigSpec, slot_mapping: Any) -> bool:
    mapping = slot_mapping if isinstance(slot_mapping, Mapping) else {}
    return all(bool(mapping.get(slot)) for slot in spec.required_slots)


def _workflow_status(
    *,
    spec: WorkflowConfigSpec,
    config: Mapping[str, Any],
    config_status: str,
    provider_entry: Mapping[str, Any] | None,
    workflow_path: str,
    slot_mapping: Mapping[str, Any],
    enabled: bool,
    configured: bool,
) -> str:
    if config_status == "invalid_config":
        return "invalid_workflow_config"
    provider_status = str((provider_entry or {}).get("status") or "").strip()
    if provider_status in {"missing_config", "invalid_config", "unreachable", "disabled"}:
        return provider_status
    if not workflow_path:
        return "missing_workflow"
    if not _required_slots_present(spec, slot_mapping):
        return "missing_slot_mapping"
    if not enabled:
        return "disabled"
    if not configured:
        return "missing_workflow"
    return "configured"


def _workflow_reason(status: str, config: Mapping[str, Any], provider_entry: Mapping[str, Any] | None) -> str:
    if status == "invalid_workflow_config":
        return _safe_reason(config.get("reason") or "invalid_workflow_config")
    if status == "missing_workflow":
        return "workflow_binding_missing"
    if status == "missing_slot_mapping":
        return "required_slot_mapping_missing"
    if status == "disabled":
        return "workflow_disabled"
    if status == "configured":
        return "workflow_runtime_not_bound"
    if status == "missing_config":
        return "provider_endpoint_missing"
    if status == "invalid_config":
        return _safe_reason((provider_entry or {}).get("reason") or "invalid_provider_config")
    if status == "unreachable":
        return _safe_reason((provider_entry or {}).get("reason") or "connection_failed")
    return ""


def _safe_reason(value: Any) -> str:
    text = _safe_short_text(value, limit=160)
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)=([^\s&]+)", r"\1=redacted", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text


def _safe_short_text(value: Any, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
