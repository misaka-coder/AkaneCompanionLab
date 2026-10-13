"""Shared capability storage, single write lock and atomic JSON updates."""

from __future__ import annotations

import json
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from .approval_policy import normalize_approval_policy_config
from .common import (
    _profile_config_path,
    _safe_profile_id,
)
from .definitions import (
    CONFIG_SCHEMA_VERSION,
    PRIVATE_MCP_SERVER_FIELDS,
    PRIVATE_VOICE_PROFILE_FIELDS,
    PUBLIC_APPROVAL_POLICY_FIELDS,
    PUBLIC_PROVIDER_FIELDS,
    PUBLIC_WORKFLOW_FIELDS,
)
from .mcp_validation import (
    _sanitize_mcp_server_configs,
    _sanitize_mcp_server_overrides,
)
from .provider_validation import _sanitize_provider_configs
from .voice_validation import _sanitize_voice_profile_configs
from .workflow_validation import _sanitize_workflow_configs


_CONFIG_WRITE_LOCK = threading.RLock()


def load_capability_config(*, base_dir: Path | str | None, profile_user_id: str) -> dict[str, Any]:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None or not path.exists():
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "missing",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "mcpServerOverrides": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [],
        }
    try:
        raw = path.read_text(encoding="utf-8")
        data = _parse_capability_config_payload(raw)
    except Exception:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_file_invalid_json",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "mcpServerOverrides": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [{"status": "invalid_config", "reason": "provider_config_file_invalid_json"}],
        }
    if not isinstance(data, dict):
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_root_must_be_object",
            "providers": {},
            "workflows": {},
            "voiceProfiles": {},
            "mcpServers": {},
            "mcpServerOverrides": {},
            "approvalPolicy": normalize_approval_policy_config({}),
            "warnings": [{"status": "invalid_config", "reason": "provider_config_root_must_be_object"}],
        }
    providers, provider_warnings = _sanitize_provider_configs(data.get("providers"))
    workflows, workflow_warnings = _sanitize_workflow_configs(data.get("workflows"))
    voice_profiles, voice_profile_warnings = _sanitize_voice_profile_configs(data.get("voiceProfiles"))
    mcp_servers, mcp_server_warnings = _sanitize_mcp_server_configs(data.get("mcpServers"))
    mcp_server_overrides, mcp_override_warnings = _sanitize_mcp_server_overrides(
        data.get("mcpServerOverrides")
    )
    approval_policy = normalize_approval_policy_config(data.get("approvalPolicy"))
    warnings = [
        *provider_warnings,
        *workflow_warnings,
        *voice_profile_warnings,
        *mcp_server_warnings,
        *mcp_override_warnings,
    ]
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "configStatus": "partial_invalid_config" if warnings else "available",
        "providers": providers,
        "workflows": workflows,
        "voiceProfiles": voice_profiles,
        "mcpServers": mcp_servers,
        "mcpServerOverrides": mcp_server_overrides,
        "approvalPolicy": approval_policy,
        "toolExposure": deepcopy(data.get("toolExposure", {})),
        "warnings": warnings,
    }


def write_capability_config(*, base_dir: Path | str | None, profile_user_id: str, config: Mapping[str, Any]) -> None:
    with _CONFIG_WRITE_LOCK:
        _write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id, config=config)


def _write_capability_config(*, base_dir, profile_user_id, config, plugin_connection_update=None, tool_exposure_update=None):
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None:
        return
    writable_config = dict(config)
    raw_existing = {}
    if path.exists():
        try:
            raw_existing = _parse_capability_config_payload(path.read_text(encoding="utf-8"))
        except Exception:
            raw_existing = {}
        if isinstance(raw_existing, Mapping):
            if "approvalPolicy" not in writable_config:
                writable_config["approvalPolicy"] = raw_existing.get("approvalPolicy")
            if "mcpServerOverrides" not in writable_config:
                writable_config["mcpServerOverrides"] = raw_existing.get("mcpServerOverrides")
    # Legacy settings writers must preserve the latest private connection section,
    # including when their input was read before a credential rotation.
    writable_config["pluginConnections"] = (
        plugin_connection_update if plugin_connection_update is not None else
        raw_existing.get("pluginConnections", {}) if isinstance(raw_existing, Mapping) else {}
    )
    # Older settings writers may hold a stale full profile. They must not undo
    # a more recent per-tool preference save.
    writable_config["toolExposure"] = (
        tool_exposure_update if tool_exposure_update is not None else
        raw_existing.get("toolExposure", {}) if isinstance(raw_existing, Mapping) else {}
    )
    _atomic_write_json(path, _config_for_write(writable_config))


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Replace a validated target and remove the temporary file on any failure."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def plugin_connection_config(*, base_dir, profile_user_id, update=None):
    """Read or atomically update the private section of the existing profile file.

    The update callback mutates its detached section and returns an admin result.
    Only successful updates are persisted. This section is deliberately absent
    from load_capability_config's broadly consumed settings projection.
    """
    if not isinstance(profile_user_id, str) or _safe_profile_id(profile_user_id) != profile_user_id:
        raise ValueError("connection_profile_invalid")
    with _CONFIG_WRITE_LOCK:
        path = _profile_config_path(base_dir, profile_user_id)
        if path is None:
            raise ValueError("connection_settings_unavailable")
        raw = _parse_capability_config_payload(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(raw, dict) or not isinstance(raw.get("pluginConnections", {}), dict):
            raise ValueError("connection_config_invalid")
        section = deepcopy(raw.get("pluginConnections", {}))
        if update is None:
            return section
        result = update(section)
        if result.get("ok"):
            _write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id,
                                     config=raw, plugin_connection_update=section)
        return result


def tool_exposure_config(*, base_dir, profile_user_id, update=None):
    """Atomic section update in the existing profile configuration authority."""
    if not isinstance(profile_user_id, str) or _safe_profile_id(profile_user_id) != profile_user_id:
        raise ValueError("tool_exposure_profile_invalid")
    with _CONFIG_WRITE_LOCK:
        path = _profile_config_path(base_dir, profile_user_id)
        if path is None:
            raise ValueError("tool_exposure_settings_unavailable")
        raw = _parse_capability_config_payload(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(raw, dict) or not isinstance(raw.get("toolExposure", {}), dict):
            raise ValueError("tool_exposure_config_invalid")
        section = deepcopy(raw.get("toolExposure", {}))
        if update is None:
            return section
        result = update(section)
        if result.get("ok"):
            _write_capability_config(base_dir=base_dir, profile_user_id=profile_user_id,
                                     config=raw, tool_exposure_update=section)
        return result


def _parse_capability_config_payload(raw: str) -> Any:
    text = str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except Exception:
        return yaml.safe_load(text)


def _config_for_write(config: Mapping[str, Any]) -> dict[str, Any]:
    approval_policy = normalize_approval_policy_config(config.get("approvalPolicy"))
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
    raw_voice_profiles = config.get("voiceProfiles") if isinstance(config.get("voiceProfiles"), Mapping) else {}
    voice_profiles, _voice_profile_warnings = _sanitize_voice_profile_configs(raw_voice_profiles)
    write_voice_profiles: dict[str, dict[str, Any]] = {}
    for profile_id, profile_config in voice_profiles.items():
        write_voice_profiles[profile_id] = {
            key: value
            for key, value in profile_config.items()
            if key in PRIVATE_VOICE_PROFILE_FIELDS and value not in (None, "", [], {})
        }
    raw_mcp_servers = config.get("mcpServers") if isinstance(config.get("mcpServers"), Mapping) else {}
    mcp_servers, _mcp_server_warnings = _sanitize_mcp_server_configs(raw_mcp_servers)
    write_mcp_servers: dict[str, dict[str, Any]] = {}
    for server_id, server_config in mcp_servers.items():
        write_mcp_servers[server_id] = {
            key: value
            for key, value in server_config.items()
            if key in PRIVATE_MCP_SERVER_FIELDS and value not in (None, "", [], {})
        }
    raw_mcp_overrides = (
        config.get("mcpServerOverrides")
        if isinstance(config.get("mcpServerOverrides"), Mapping)
        else {}
    )
    mcp_overrides, _mcp_override_warnings = _sanitize_mcp_server_overrides(raw_mcp_overrides)
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "approvalPolicy": {
            key: value
            for key, value in approval_policy.items()
            if key in PUBLIC_APPROVAL_POLICY_FIELDS and value not in (None, "")
        },
        "providers": write_providers,
        "workflows": write_workflows,
        "voiceProfiles": write_voice_profiles,
        "mcpServers": write_mcp_servers,
        "mcpServerOverrides": mcp_overrides,
        **({"pluginConnections": deepcopy(config["pluginConnections"])} if config.get("pluginConnections") else {}),
        **({"toolExposure": deepcopy(config["toolExposure"])} if config.get("toolExposure") else {}),
    }
