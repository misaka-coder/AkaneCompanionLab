"""Pure approval-policy normalization and Capcore catalog projection."""

from __future__ import annotations

import re
from typing import Any, Mapping, cast

from capcore import ApprovalPolicy as CapcoreApprovalPolicy, ConfirmPolicy as CapcoreConfirmPolicy, PermissionDecision as CapcorePermissionDecision, RiskLevel as CapcoreRiskLevel, permission_request_from_mapping as capcore_permission_request_from_mapping, project_mapping_fields as capcore_project_mapping_fields, resolve_permission as capcore_resolve_permission

from .common import _safe_short_text
from .definitions import (
    APPROVAL_DISABLED_STATUSES,
    APPROVAL_FAMILY_EXTENSIONS,
    APPROVAL_FAMILY_OPERATIONS,
    APPROVAL_MODES,
    APPROVAL_MODE_ASK_EACH_TIME,
    APPROVAL_MODE_DISABLED,
    APPROVAL_MODE_TRUSTED_AUTO_ALLOW,
    APPROVAL_POLICY_DEFAULT_MODE,
    PROFILE_ID_SAFE_CHARS,
)


def capability_approval_mode(
    *,
    enabled: bool = True,
    status: str = "",
    risk: str = "",
    requires_confirmation: bool = False,
) -> str:
    normalized_status = str(status or "").strip().lower()
    entry = {
        "id": "capability",
        "name": "Capability",
        "risk": risk,
        "requiresConfirmation": requires_confirmation,
    }
    policy_mode = (
        APPROVAL_MODE_DISABLED
        if not enabled or normalized_status in APPROVAL_DISABLED_STATUSES
        else APPROVAL_MODE_ASK_EACH_TIME
    )
    decision = _capcore_permission_decision_from_entry(entry, policy_mode=policy_mode)
    return _approval_mode_from_capcore_decision(decision)


def _capcore_permission_decision_from_entry(entry: Mapping[str, Any], *, policy_mode: str) -> CapcorePermissionDecision:
    request = capcore_permission_request_from_mapping(entry, default_confirm="never")
    return capcore_resolve_permission(request, CapcoreApprovalPolicy(default_mode=policy_mode))


def _approval_mode_from_capcore_decision(decision: CapcorePermissionDecision) -> str:
    if not decision.allowed and not decision.requires_user_decision:
        return APPROVAL_MODE_DISABLED
    if decision.requires_user_decision:
        return APPROVAL_MODE_ASK_EACH_TIME
    return APPROVAL_MODE_TRUSTED_AUTO_ALLOW


def project_capcore_catalog_fields(
    entry: Mapping[str, Any],
    *,
    default_risk: str = "medium",
    default_confirm: str = "never",
) -> dict[str, Any]:
    public_entry = dict(entry)
    return capcore_project_mapping_fields(
        public_entry,
        default_id=str(public_entry.get("id") or "capability"),
        default_display_name=str(public_entry.get("name") or public_entry.get("displayName") or "Capability"),
        default_risk=cast(CapcoreRiskLevel, default_risk),
        default_confirm=cast(CapcoreConfirmPolicy, default_confirm),
    )


def with_capability_approval_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    public_entry = dict(entry)
    raw_mode = str(public_entry.get("approvalMode") or "").strip()
    if raw_mode in APPROVAL_MODES:
        mode = raw_mode
    else:
        status = str(public_entry.get("status") or "").strip().lower()
        policy_mode = (
            APPROVAL_MODE_DISABLED
            if public_entry.get("enabled") is False or status in APPROVAL_DISABLED_STATUSES
            else APPROVAL_MODE_ASK_EACH_TIME
        )
        decision = _capcore_permission_decision_from_entry(public_entry, policy_mode=policy_mode)
        mode = _approval_mode_from_capcore_decision(decision)
    public_entry["approvalMode"] = mode
    if not str(public_entry.get("approvalReason") or "").strip():
        public_entry["approvalReason"] = _approval_reason(public_entry, mode)
    return public_entry


def apply_approval_policy_to_entry(
    entry: Mapping[str, Any], approval_policy: Mapping[str, Any] | None
) -> dict[str, Any]:
    public_entry = with_capability_approval_metadata(entry)
    policy = normalize_approval_policy_config(approval_policy)
    if public_entry.get("approvalMode") == APPROVAL_MODE_DISABLED:
        return public_entry
    permission_request = capcore_permission_request_from_mapping(public_entry, default_confirm="never")
    policy_mode = approval_mode_for_capability(
        policy,
        str(public_entry.get("id") or public_entry.get("capabilityId") or ""),
        family_id=APPROVAL_FAMILY_OPERATIONS if permission_request.required else "",
    )
    decision = _capcore_permission_decision_from_entry(public_entry, policy_mode=policy_mode)
    request = decision.request
    next_mode = _approval_mode_from_capcore_decision(decision)
    if request is None or not request.required:
        return public_entry
    return {
        **public_entry,
        "approvalMode": next_mode,
        "approvalReason": str(decision.reason or _approval_reason(public_entry, next_mode)),
        "requiresConfirmation": next_mode == APPROVAL_MODE_ASK_EACH_TIME,
    }


def _safe_capability_approval_id(value: Any) -> str:
    capability_id = str(value or "").strip().lower()
    if capability_id.startswith("tool."):
        capability_id = capability_id[5:]
    if not capability_id or len(capability_id) > 80:
        return ""
    if any(character not in PROFILE_ID_SAFE_CHARS for character in capability_id):
        return ""
    return capability_id


def _normalize_capability_approval_modes(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_capability_id, raw_mode in list(value.items())[:128]:
        capability_id = _safe_capability_approval_id(raw_capability_id)
        mode = str(raw_mode or "").strip().lower()
        if capability_id and mode in APPROVAL_MODES:
            result[capability_id] = mode
    return result


def normalize_approval_policy_config(raw_policy: Any) -> dict[str, Any]:
    raw = raw_policy if isinstance(raw_policy, Mapping) else {}
    capability_modes = _normalize_capability_approval_modes(
        raw.get("capabilityModes") or raw.get("capability_modes")
    )
    # Version-1 profiles used one global default and a synthetic ``mcp``
    # override.  Translate that persisted data at the storage boundary; the
    # runtime itself has only concrete capability and family policy now.
    legacy_default = str(raw.get("defaultMode") or raw.get("default_mode") or "").strip()
    if legacy_default not in APPROVAL_MODES:
        legacy_default = APPROVAL_POLICY_DEFAULT_MODE
    capability_modes.pop("mcp", None)
    capability_modes.setdefault(APPROVAL_FAMILY_OPERATIONS, legacy_default)
    capability_modes.setdefault(APPROVAL_FAMILY_EXTENSIONS, legacy_default)
    updated_at = _safe_short_text(raw.get("updatedAt") or raw.get("updated_at"))
    return {
        "capabilityModes": capability_modes,
        "updatedAt": updated_at,
    }


def approval_mode_override_for_capability(
    approval_policy: Mapping[str, Any] | None,
    capability_id: str,
) -> str:
    normalized = normalize_approval_policy_config(approval_policy)
    safe_id = _safe_capability_approval_id(capability_id)
    if not safe_id:
        return ""
    return str(normalized["capabilityModes"].get(safe_id) or "")


def approval_mode_for_capability(
    approval_policy: Mapping[str, Any] | None,
    capability_id: str,
    *,
    family_id: str = "",
) -> str:
    normalized = normalize_approval_policy_config(approval_policy)
    exact_mode = approval_mode_override_for_capability(normalized, capability_id)
    if exact_mode:
        return exact_mode
    # Family membership is supplied by the call site after it has classified
    # the concrete action.  Inferring it from a capability-name prefix would
    # incorrectly put read-only MCP/plugin actions behind the ops switch.
    safe_family_id = _safe_capability_approval_id(family_id)
    if safe_family_id:
        family_mode = approval_mode_override_for_capability(normalized, safe_family_id)
        if family_mode:
            return family_mode
    return APPROVAL_POLICY_DEFAULT_MODE


def build_approval_policy_entry(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_approval_policy_config(policy)
    family_modes = (
        (APPROVAL_FAMILY_OPERATIONS, "本机与外部操作", "Shell、浏览器交互和会产生外部影响的 MCP/插件工具。"),
        (APPROVAL_FAMILY_EXTENSIONS, "扩展管理", "安装、发布、启停、更新和移除 Skill、MCP 与插件。"),
    )
    family_options = [
        {"id": APPROVAL_MODE_TRUSTED_AUTO_ALLOW, "label": "直接允许", "summary": "无需逐次确认。"},
        {"id": APPROVAL_MODE_ASK_EACH_TIME, "label": "每次询问", "summary": "创建审批请求后再执行。"},
        {"id": APPROVAL_MODE_DISABLED, "label": "关闭", "summary": "不允许执行这类动作。"},
    ]
    return {
        "families": [
            {
                "id": family_id,
                "label": label,
                "summary": summary,
                "mode": approval_mode_for_capability(normalized, "", family_id=family_id),
                "availableModes": [dict(item) for item in family_options],
            }
            for family_id, label, summary in family_modes
        ],
        "updatedAt": normalized["updatedAt"],
    }


def _approval_reason(entry: Mapping[str, Any], mode: str) -> str:
    if mode == APPROVAL_MODE_DISABLED:
        if entry.get("enabled") is False:
            return "capability_disabled"
        status = re.sub(r"[^a-z0-9_.-]+", "_", str(entry.get("status") or "").strip().lower())[:80]
        return f"status_{status}" if status else "capability_not_ready"
    if mode == APPROVAL_MODE_ASK_EACH_TIME:
        return "requires_confirmation"
    return "trusted_runtime_boundary"
