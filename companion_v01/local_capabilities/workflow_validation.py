"""Pure workflow path, slot mapping and asset-handle validation."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .common import (
    _safe_reason,
    _safe_short_text,
)
from .definitions import (
    CONFIGURABLE_WORKFLOW_SPECS,
    WORKFLOW_ASSET_HANDLE_MAX_LENGTH,
    WORKFLOW_ASSET_HANDLE_RE,
    WORKFLOW_COMFYUI_SLOT_PATH_RE,
    WORKFLOW_PATH_MAX_LENGTH,
    WORKFLOW_SLOT_VALUE_RE,
    WorkflowConfigSpec,
)


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


def validate_comfyui_input_slot_path(workflow_json: Mapping[str, Any], slot_path: str) -> dict[str, Any]:
    path = str(slot_path or "").strip()
    if not WORKFLOW_COMFYUI_SLOT_PATH_RE.match(path):
        return {"ok": False, "reason": "slot_mapping_path_invalid"}
    parts = path.split(".")
    node_id = parts[0]
    node = workflow_json.get(node_id)
    if not isinstance(node, Mapping):
        return {"ok": False, "reason": "slot_mapping_node_missing"}
    current: Any = node.get("inputs")
    if not isinstance(current, Mapping):
        return {"ok": False, "reason": "slot_mapping_inputs_missing"}
    for part in parts[2:]:
        if not isinstance(current, Mapping) or part not in current:
            return {"ok": False, "reason": "slot_mapping_target_missing"}
        current = current[part]
    return {"ok": True, "reason": ""}


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


def _invalid_workflow_config(spec: WorkflowConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "workflowId": spec.id}


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
