"""Workflow binding, scoped file updates and execution preflight."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .approval_policy import (
    project_capcore_catalog_fields,
    with_capability_approval_metadata,
)
from .common import (
    _now_iso,
    _profile_config_path,
    _public_config_scope,
    _safe_reason,
)
from .definitions import (
    CONFIGURABLE_WORKFLOW_BY_ID,
    CONFIGURABLE_WORKFLOW_SPECS,
    CONFIG_SCHEMA_VERSION,
    WORKFLOW_CONFIG_FILE_MAX_BYTES,
    WorkflowConfigSpec,
)
from .providers import get_provider_config_entries
from .storage import (
    _atomic_write_json,
    load_capability_config,
    write_capability_config,
)
from .workflow_validation import (
    _required_slots_present,
    _safe_slot_mapping_for_output,
    _safe_workflow_path_for_output,
    normalize_workflow_asset_handle,
    normalize_workflow_config_payload,
    normalize_workflow_path,
    validate_comfyui_input_slot_path,
)


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
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": config.get("mcpServers", {}),
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


def save_workflow_file(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    workflow_path_value = payload.get("workflowPath")
    if workflow_path_value is None:
        workflow_path_value = payload.get("workflowRef")
    if workflow_path_value is None:
        workflow_path_value = spec.default_workflow_path
    normalized_path = normalize_workflow_path(str(workflow_path_value or "").strip())
    if not normalized_path["ok"]:
        return {
            "ok": False,
            "status": normalized_path["status"],
            "workflowId": spec.id,
            "reason": normalized_path.get("reason") or "invalid_workflow_config",
        }

    workflow_text = payload.get("workflowJson")
    if workflow_text is None:
        workflow_text = payload.get("workflowText")
    workflow_text = str(workflow_text or "")
    if not workflow_text.strip():
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_required",
        }
    if len(workflow_text.encode("utf-8")) > WORKFLOW_CONFIG_FILE_MAX_BYTES:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_too_large",
        }

    try:
        workflow_json = json.loads(workflow_text)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_file_invalid_json",
        }
    if not isinstance(workflow_json, Mapping) or not workflow_json:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_json_invalid",
        }

    target_path = resolve_workflow_config_file_path(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_path=str(normalized_path["workflowPath"]),
    )
    if target_path is None:
        return {
            "ok": False,
            "status": "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": "workflow_path_must_be_safe_relative_json",
        }

    _atomic_write_json(target_path, workflow_json)

    return {
        "ok": True,
        "status": "workflow_file_saved",
        "workflowId": spec.id,
        "workflowPath": str(normalized_path["workflowPath"]),
        "configScope": _public_config_scope(profile_user_id),
        "executionReady": False,
        "autoEnable": False,
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
        "workflowFile": False,
        "slotPaths": False,
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
    runtime = validate_workflow_runtime_binding(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_id=spec.id,
    )
    runtime_checks = runtime.get("checks") if isinstance(runtime.get("checks"), Mapping) else {}
    checks["workflowFile"] = bool(runtime_checks.get("workflowFile"))
    checks["slotPaths"] = bool(runtime_checks.get("slotPaths"))
    if not runtime.get("ok"):
        return {
            "ok": False,
            "status": runtime.get("status") or "invalid_workflow_config",
            "workflowId": spec.id,
            "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
            "executionReady": False,
            "checks": checks,
            "workflow": {
                **workflow,
                "status": runtime.get("status") or "invalid_workflow_config",
                "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
                "executionReady": False,
            },
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
        "workflowFile": False,
        "slotPaths": False,
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

    runtime = validate_workflow_runtime_binding(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_id=spec.id,
    )
    runtime_checks = runtime.get("checks") if isinstance(runtime.get("checks"), Mapping) else {}
    checks["workflowFile"] = bool(runtime_checks.get("workflowFile"))
    checks["slotPaths"] = bool(runtime_checks.get("slotPaths"))
    if not runtime.get("ok"):
        return {
            "ok": False,
            "status": runtime.get("status") or "invalid_workflow_config",
            "workflowId": spec.id,
            "capabilityId": spec.capability_id,
            "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
            "executionReady": False,
            "canRun": False,
            "checks": checks,
            "workflow": {
                **workflow,
                "status": runtime.get("status") or "invalid_workflow_config",
                "reason": runtime.get("reason") or "workflow_runtime_config_invalid",
                "executionReady": False,
            },
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
    entry = {
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
    projected = project_capcore_catalog_fields(entry, default_risk=spec.risk, default_confirm="never")
    return with_capability_approval_metadata(projected)


def resolve_workflow_config_file_path(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_path: str,
) -> Path | None:
    config_path = _profile_config_path(base_dir, profile_user_id)
    if config_path is None:
        return None
    normalized = normalize_workflow_path(workflow_path)
    if not normalized.get("ok"):
        return None
    root = config_path.parent.resolve()
    target = (root / str(normalized["workflowPath"])).resolve()
    if root == target or root not in target.parents:
        return None
    return target


def validate_workflow_runtime_binding(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    workflow_id: str,
) -> dict[str, Any]:
    spec = CONFIGURABLE_WORKFLOW_BY_ID.get(str(workflow_id or "").strip())
    if spec is None:
        return {"ok": False, "status": "unknown_workflow", "workflowId": str(workflow_id or "").strip()}

    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    workflow = config.get("workflows", {}).get(spec.id)
    if not isinstance(workflow, Mapping) or not workflow.get("enabled"):
        return _runtime_binding_result(spec, "missing_workflow", "workflow_binding_missing")

    workflow_path = str(workflow.get("workflowPath") or "").strip()
    slot_mapping = workflow.get("slotMapping") if isinstance(workflow.get("slotMapping"), Mapping) else {}
    if not workflow_path:
        return _runtime_binding_result(spec, "missing_workflow", "workflow_path_required")
    if not _required_slots_present(spec, slot_mapping):
        return _runtime_binding_result(spec, "missing_slot_mapping", "required_slot_mapping_missing")

    resolved_path = resolve_workflow_config_file_path(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        workflow_path=workflow_path,
    )
    if resolved_path is None:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_path_must_be_safe_relative_json")
    if not resolved_path.is_file():
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_missing")
    try:
        if resolved_path.stat().st_size > WORKFLOW_CONFIG_FILE_MAX_BYTES:
            return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_too_large")
        workflow_json = json.loads(resolved_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_invalid_encoding")
    except json.JSONDecodeError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_invalid_json")
    except OSError:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_file_unreadable")

    if not isinstance(workflow_json, Mapping) or not workflow_json:
        return _runtime_binding_result(spec, "invalid_workflow_config", "workflow_json_invalid")

    for slot_name in spec.required_slots:
        slot_path = str(slot_mapping.get(slot_name) or "").strip()
        slot_check = validate_comfyui_input_slot_path(workflow_json, slot_path)
        if not slot_check.get("ok"):
            return _runtime_binding_result(
                spec,
                "invalid_workflow_config",
                str(slot_check.get("reason") or "slot_mapping_target_missing"),
                workflow_file=True,
            )

    return {
        "ok": True,
        "status": "ready",
        "workflowId": spec.id,
        "reason": "",
        "executionReady": True,
        "checks": {
            "workflowFile": True,
            "slotPaths": True,
        },
    }


def _runtime_binding_result(
    spec: WorkflowConfigSpec,
    status: str,
    reason: str,
    *,
    workflow_file: bool = False,
    slot_paths: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "workflowId": spec.id,
        "reason": _safe_reason(reason),
        "executionReady": False,
        "checks": {
            "workflowFile": workflow_file,
            "slotPaths": slot_paths,
        },
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
