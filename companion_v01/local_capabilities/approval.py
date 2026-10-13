"""Profile-scoped approval policy read and save operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .approval_policy import (
    _normalize_capability_approval_modes,
    _safe_capability_approval_id,
    build_approval_policy_entry,
    normalize_approval_policy_config,
)
from .common import (
    _now_iso,
    _public_config_scope,
)
from .definitions import (
    APPROVAL_MODES,
    CONFIG_SCHEMA_VERSION,
)
from .storage import (
    load_capability_config,
    write_capability_config,
)


def get_approval_policy_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "configStatus": config.get("configStatus") or "available",
        "warnings": list(config.get("warnings") or []),
        "configScope": _public_config_scope(profile_user_id),
        "approvalPolicy": build_approval_policy_entry(config.get("approvalPolicy")),
    }


def save_capability_approval_mode(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    capability_id: str,
    mode: str,
) -> dict[str, Any]:
    safe_capability_id = _safe_capability_approval_id(capability_id)
    normalized_mode = str(mode or "").strip().lower()
    if not safe_capability_id or normalized_mode not in APPROVAL_MODES:
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": "capability_approval_mode_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    approval_policy = normalize_approval_policy_config(config.get("approvalPolicy"))
    capability_modes = dict(approval_policy["capabilityModes"])
    capability_modes[safe_capability_id] = normalized_mode
    approval_policy = {
        "capabilityModes": capability_modes,
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "approvalPolicy": approval_policy,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": config.get("mcpServers", {}),
        },
    )
    return {
        "ok": True,
        "status": "saved",
        "capabilityId": safe_capability_id,
        "approvalMode": normalized_mode,
        "configScope": _public_config_scope(profile_user_id),
        "approvalPolicy": build_approval_policy_entry(approval_policy),
        "refresh": True,
    }


def save_capability_approval_modes(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    modes: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist several exact/family overrides in one config write."""

    raw_modes = dict(modes or {})
    normalized_updates = _normalize_capability_approval_modes(raw_modes)
    if not normalized_updates or len(normalized_updates) != len(raw_modes):
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": "capability_approval_modes_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if config.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": config.get("reason") or "provider_config_file_invalid",
            "configScope": _public_config_scope(profile_user_id),
        }
    approval_policy = normalize_approval_policy_config(config.get("approvalPolicy"))
    capability_modes = dict(approval_policy["capabilityModes"])
    capability_modes.update(normalized_updates)
    approval_policy = {
        "capabilityModes": capability_modes,
        "updatedAt": _now_iso(),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "approvalPolicy": approval_policy,
            "providers": config.get("providers", {}),
            "workflows": config.get("workflows", {}),
            "voiceProfiles": config.get("voiceProfiles", {}),
            "mcpServers": config.get("mcpServers", {}),
        },
    )
    return {
        "ok": True,
        "status": "saved",
        "approvalModes": normalized_updates,
        "configScope": _public_config_scope(profile_user_id),
        "approvalPolicy": build_approval_policy_entry(approval_policy),
        "refresh": True,
    }
