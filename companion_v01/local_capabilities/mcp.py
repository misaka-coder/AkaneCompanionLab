"""Host MCP registrations, profile overlays, discovery and migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .approval_policy import (
    project_capcore_catalog_fields,
    with_capability_approval_metadata,
)
from .common import (
    _host_mcp_registry_path,
    _now_iso,
    _public_mcp_config_scope,
    _safe_path_basename,
)
from .definitions import (
    CONFIG_SCHEMA_VERSION,
    MCP_PROFILE_OVERRIDE_FIELDS,
    MCP_SERVER_TEXT_MAX_LENGTH,
    MCP_TOOL_DESCRIPTION_MAX_LENGTH,
    PRIVATE_MCP_SERVER_FIELDS,
)
from .mcp_validation import (
    _apply_mcp_low_risk_allowlist,
    _infer_mcp_tool_risk,
    _mcp_server_endpoint_identity,
    _normalize_mcp_input_schema,
    _raw_mcp_tool_effects,
    _safe_mcp_server_id,
    _safe_mcp_tool_name,
    _safe_mcp_tool_name_list,
    _safe_public_mcp_text,
    _sanitize_mcp_server_configs,
    normalize_mcp_server_config_payload,
    normalize_mcp_tool_discovery_payload,
)
from .storage import (
    _atomic_write_json,
    _parse_capability_config_payload,
    load_capability_config,
    write_capability_config,
)


def load_host_mcp_server_configs(*, base_dir: Path | str | None) -> dict[str, Any]:
    """Load the Host-wide MCP registry.

    Server installation and discovery belong to the Host.  Profile-specific
    policy remains in each profile capability config and is applied separately
    by :func:`get_effective_mcp_server_configs`.
    """

    path = _host_mcp_registry_path(base_dir)
    if path is None or not path.exists():
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "missing",
            "mcpServers": {},
            "warnings": [],
        }
    try:
        raw = _parse_capability_config_payload(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "host_mcp_registry_invalid",
            "mcpServers": {},
            "warnings": [{"status": "invalid_config", "reason": "host_mcp_registry_invalid"}],
        }
    if not isinstance(raw, Mapping):
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "host_mcp_registry_root_must_be_object",
            "mcpServers": {},
            "warnings": [
                {"status": "invalid_config", "reason": "host_mcp_registry_root_must_be_object"}
            ],
        }
    servers, warnings = _sanitize_mcp_server_configs(raw.get("mcpServers"))
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "configStatus": "partial_invalid_config" if warnings else "available",
        "mcpServers": servers,
        "warnings": warnings,
    }


def write_host_mcp_server_configs(
    *,
    base_dir: Path | str | None,
    mcp_servers: Mapping[str, Any],
) -> None:
    path = _host_mcp_registry_path(base_dir)
    if path is None:
        return
    servers, _warnings = _sanitize_mcp_server_configs(mcp_servers)
    payload = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "mcpServers": {
            server_id: {
                key: value
                for key, value in server.items()
                if key in PRIVATE_MCP_SERVER_FIELDS and value not in (None, "", [], {})
            }
            for server_id, server in sorted(servers.items())
        },
    }
    _atomic_write_json(path, payload)


def get_effective_mcp_server_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    profile_config: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return Host registrations with the current profile's small overlay.

    Legacy profile-local registrations remain a read fallback only during the
    documented migration window.  They never override an existing Host entry.
    """

    host = load_host_mcp_server_configs(base_dir=base_dir)
    profile = (
        dict(profile_config)
        if isinstance(profile_config, Mapping)
        else load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    )
    servers = {
        str(server_id): dict(server)
        for server_id, server in (host.get("mcpServers") or {}).items()
        if isinstance(server, Mapping)
    }
    for server_id, server in (profile.get("mcpServers") or {}).items():
        if server_id not in servers and isinstance(server, Mapping):
            servers[str(server_id)] = dict(server)
    overrides = profile.get("mcpServerOverrides") or {}
    for server_id, override in overrides.items():
        if server_id in servers and isinstance(override, Mapping):
            servers[server_id] = {
                **servers[server_id],
                **{key: value for key, value in override.items() if key in MCP_PROFILE_OVERRIDE_FIELDS},
            }
    for server_id, server in list(servers.items()):
        allowlist = _safe_mcp_tool_name_list(server.get("lowRiskAllowlist"))
        tools = server.get("tools") if isinstance(server.get("tools"), list) else []
        if tools:
            servers[server_id] = {
                **server,
                "tools": [_apply_mcp_low_risk_allowlist(tool, allowlist) for tool in tools],
            }
    return servers


def save_mcp_server_profile_enabled(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
    enabled: bool,
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    host_servers = load_host_mcp_server_configs(base_dir=base_dir).get("mcpServers") or {}
    profile = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    if safe_server_id not in host_servers and safe_server_id not in (profile.get("mcpServers") or {}):
        return {
            "ok": False,
            "status": "not_found",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
        }
    overrides = dict(profile.get("mcpServerOverrides") or {})
    overrides[safe_server_id] = {
        **dict(overrides.get(safe_server_id) or {}),
        "enabled": bool(enabled),
    }
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={**profile, "mcpServerOverrides": overrides},
    )
    effective = get_effective_mcp_server_configs(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
    ).get(safe_server_id, {})
    return {
        "ok": True,
        "status": "ready" if bool(enabled) else "disabled",
        "serverId": safe_server_id,
        "scope": "profile",
        "configScope": _public_mcp_config_scope(profile_user_id),
        "mcpServer": build_mcp_server_config_entry(safe_server_id, effective),
        "refresh": True,
    }


def clear_mcp_server_profile_override(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
) -> None:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return
    profile = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    overrides = dict(profile.get("mcpServerOverrides") or {})
    if overrides.pop(safe_server_id, None) is None:
        return
    write_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        config={**profile, "mcpServerOverrides": overrides},
    )


def migrate_legacy_profile_mcp_servers(*, base_dir: Path | str | None) -> dict[str, Any]:
    """Promote unambiguous legacy per-profile MCP registrations once.

    Equal endpoint definitions collapse into one Host entry.  A server id that
    points at different endpoints is deliberately left profile-local for manual
    resolution instead of silently choosing one credential or executable.
    """

    root = Path(base_dir).resolve() if base_dir is not None else None
    if root is None or not root.exists():
        return {"ok": True, "status": "no_config_root", "promoted": 0, "conflicts": []}
    host_payload = load_host_mcp_server_configs(base_dir=root)
    if host_payload.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "reason": host_payload.get("reason") or "host_mcp_registry_invalid",
        }
    host_servers = dict(host_payload.get("mcpServers") or {})
    candidates: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    profile_configs: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*/capabilities/capabilities.yaml")):
        profile_id = path.parent.parent.name
        config = load_capability_config(base_dir=root, profile_user_id=profile_id)
        if config.get("configStatus") == "invalid_config":
            continue
        legacy = config.get("mcpServers") or {}
        if not legacy:
            continue
        profile_configs[profile_id] = config
        for server_id, server in legacy.items():
            if isinstance(server, Mapping):
                candidates.setdefault(str(server_id), []).append((profile_id, dict(server)))

    promoted_ids: set[str] = set()
    conflicts: list[str] = []
    for server_id, rows in sorted(candidates.items()):
        endpoint_groups = {_mcp_server_endpoint_identity(server) for _profile, server in rows}
        existing = host_servers.get(server_id)
        if existing is not None:
            endpoint_groups.add(_mcp_server_endpoint_identity(existing))
        if len(endpoint_groups) != 1:
            conflicts.append(server_id)
            continue
        chosen = max(rows, key=lambda row: str(row[1].get("updatedAt") or ""))[1]
        if existing is None:
            host_servers[server_id] = chosen
        promoted_ids.add(server_id)

    if promoted_ids:
        write_host_mcp_server_configs(base_dir=root, mcp_servers=host_servers)
        for profile_id, config in profile_configs.items():
            legacy = dict(config.get("mcpServers") or {})
            overrides = dict(config.get("mcpServerOverrides") or {})
            changed = False
            for server_id in sorted(promoted_ids.intersection(legacy)):
                old = dict(legacy.pop(server_id))
                shared = host_servers.get(server_id) or {}
                differing = {
                    key: old.get(key)
                    for key in MCP_PROFILE_OVERRIDE_FIELDS
                    if key in old and old.get(key) != shared.get(key)
                }
                if differing:
                    overrides[server_id] = {**dict(overrides.get(server_id) or {}), **differing}
                changed = True
            if changed:
                write_capability_config(
                    base_dir=root,
                    profile_user_id=profile_id,
                    config={**config, "mcpServers": legacy, "mcpServerOverrides": overrides},
                )
    return {
        "ok": True,
        "status": "migrated" if promoted_ids else "unchanged",
        "promoted": len(promoted_ids),
        "conflicts": conflicts,
    }


def _remove_mcp_profile_state(*, base_dir: Path | str | None, server_id: str) -> None:
    root = Path(base_dir).resolve() if base_dir is not None else None
    if root is None or not root.exists():
        return
    for path in sorted(root.glob("*/capabilities/capabilities.yaml")):
        profile_id = path.parent.parent.name
        config = load_capability_config(base_dir=root, profile_user_id=profile_id)
        if config.get("configStatus") == "invalid_config":
            continue
        legacy = dict(config.get("mcpServers") or {})
        overrides = dict(config.get("mcpServerOverrides") or {})
        changed = legacy.pop(server_id, None) is not None
        changed = overrides.pop(server_id, None) is not None or changed
        if changed:
            write_capability_config(
                base_dir=root,
                profile_user_id=profile_id,
                config={**config, "mcpServers": legacy, "mcpServerOverrides": overrides},
            )


def list_mcp_server_configs(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
) -> dict[str, Any]:
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    host = load_host_mcp_server_configs(base_dir=base_dir)
    effective_servers = get_effective_mcp_server_configs(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        profile_config=config,
    )
    servers = [
        build_mcp_server_config_entry(server_id, server_config)
        for server_id, server_config in sorted(effective_servers.items())
    ]
    return {
        "ok": True,
        "status": "available",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "execution": "config-skeleton",
        "configStatus": host.get("configStatus") or "available",
        "warnings": [*list(host.get("warnings") or []), *list(config.get("warnings") or [])],
        "configScope": _public_mcp_config_scope(profile_user_id),
        "mcpServers": servers,
        "summary": _summarize_mcp_server_entries(servers),
    }


def save_mcp_server_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
    payload: Mapping[str, Any],
    discovered_tools: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    normalized = normalize_mcp_server_config_payload(safe_server_id, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "serverId": safe_server_id,
            "reason": normalized.get("reason") or "invalid_mcp_server_config",
        }

    host = load_host_mcp_server_configs(base_dir=base_dir)
    if host.get("configStatus") == "invalid_config":
        return {
            "ok": False,
            "status": "invalid_config",
            "serverId": safe_server_id,
            "reason": host.get("reason") or "host_mcp_registry_invalid",
            "configScope": _public_mcp_config_scope(profile_user_id),
        }

    servers = dict(host.get("mcpServers") or {})
    existing = servers.get(safe_server_id) if isinstance(servers.get(safe_server_id), Mapping) else {}
    low_risk_allowlist = _safe_mcp_tool_name_list(normalized.get("lowRiskAllowlist"))
    next_server = {
        "enabled": bool(normalized["enabled"]),
        "displayName": normalized["displayName"],
        "catalogDescription": normalized["catalogDescription"],
        "activationMode": normalized["activationMode"],
        "pinnedTools": normalized["pinnedTools"],
        "transport": normalized["transport"],
        "command": normalized["command"],
        "args": normalized["args"],
        "cwd": normalized["cwd"],
        "env": normalized["env"],
        "url": normalized["url"],
        "headers": normalized["headers"],
        "updatedAt": _now_iso(),
    }
    if low_risk_allowlist:
        next_server["lowRiskAllowlist"] = low_risk_allowlist
    same_endpoint = _mcp_server_endpoint_identity(existing) == _mcp_server_endpoint_identity(next_server)
    normalized_discovery = (
        normalize_mcp_tool_discovery_payload(safe_server_id, {"tools": discovered_tools})
        if discovered_tools is not None
        else None
    )
    if normalized_discovery is not None and not normalized_discovery.get("ok"):
        return {
            "ok": False,
            "status": normalized_discovery.get("status") or "invalid_discovery",
            "serverId": safe_server_id,
            "reason": normalized_discovery.get("reason") or "invalid_mcp_discovery_payload",
        }
    tools = (
        list(normalized_discovery.get("tools") or [])
        if normalized_discovery is not None
        else list(existing.get("tools") or [])
        if same_endpoint and isinstance(existing.get("tools"), list)
        else []
    )
    if tools:
        next_server["tools"] = [
            {
                **_apply_mcp_low_risk_allowlist(tool, low_risk_allowlist),
                "promptExposed": False,
            }
            for tool in tools
        ]
    if normalized_discovery is not None:
        next_server["lastDiscovery"] = {
            "status": "ready",
            "discoveredAt": _now_iso(),
            "toolCount": len(tools),
        }
    elif same_endpoint and isinstance(existing.get("lastDiscovery"), Mapping):
        next_server["lastDiscovery"] = dict(existing.get("lastDiscovery") or {})
    servers[safe_server_id] = next_server
    write_host_mcp_server_configs(base_dir=base_dir, mcp_servers=servers)
    return {
        "ok": True,
        "status": "saved",
        "serverId": safe_server_id,
        "autoEnable": False,
        "configScope": _public_mcp_config_scope(profile_user_id),
        "mcpServer": build_mcp_server_config_entry(safe_server_id, next_server),
    }


def remove_mcp_server_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    host = load_host_mcp_server_configs(base_dir=base_dir)
    servers = dict(host.get("mcpServers") or {})
    if safe_server_id not in servers:
        return {
            "ok": False,
            "status": "not_found",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
        }
    servers.pop(safe_server_id, None)
    write_host_mcp_server_configs(base_dir=base_dir, mcp_servers=servers)
    _remove_mcp_profile_state(base_dir=base_dir, server_id=safe_server_id)
    return {
        "ok": True,
        "status": "removed",
        "serverId": safe_server_id,
        "removedExternalPackage": False,
        "refresh": True,
    }


def get_mcp_server_runtime_config(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {}
    server = get_effective_mcp_server_configs(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
    ).get(safe_server_id)
    if not isinstance(server, Mapping):
        return {}
    return {
        "serverId": safe_server_id,
        "enabled": bool(server.get("enabled")),
        "displayName": str(server.get("displayName") or safe_server_id),
        "catalogDescription": str(server.get("catalogDescription") or ""),
        "activationMode": str(server.get("activationMode") or "on_demand"),
        "pinnedTools": list(server.get("pinnedTools") or []) if isinstance(server.get("pinnedTools"), list) else [],
        "transport": str(server.get("transport") or "stdio"),
        "command": str(server.get("command") or ""),
        "args": list(server.get("args") or []) if isinstance(server.get("args"), list) else [],
        "cwd": str(server.get("cwd") or ""),
        "env": dict(server.get("env") or {}) if isinstance(server.get("env"), Mapping) else {},
        "url": str(server.get("url") or ""),
        "headers": dict(server.get("headers") or {}) if isinstance(server.get("headers"), Mapping) else {},
        "tools": list(server.get("tools") or []) if isinstance(server.get("tools"), list) else [],
        "lowRiskAllowlist": list(server.get("lowRiskAllowlist") or [])
        if isinstance(server.get("lowRiskAllowlist"), list)
        else [],
    }


def save_mcp_server_discovery(
    *,
    base_dir: Path | str | None,
    profile_user_id: str,
    server_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    safe_server_id = _safe_mcp_server_id(server_id)
    if not safe_server_id:
        return {"ok": False, "status": "invalid_mcp_server", "reason": "mcp_server_id_invalid"}
    host = load_host_mcp_server_configs(base_dir=base_dir)
    server = (host.get("mcpServers") or {}).get(safe_server_id)
    if not isinstance(server, Mapping):
        return {
            "ok": False,
            "status": "missing_config",
            "serverId": safe_server_id,
            "reason": "mcp_server_config_missing",
        }
    normalized = normalize_mcp_tool_discovery_payload(safe_server_id, payload)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": normalized["status"],
            "serverId": safe_server_id,
            "reason": normalized.get("reason") or "invalid_mcp_discovery_payload",
        }

    last_discovery = {
        "status": "ready",
        "discoveredAt": _now_iso(),
        "toolCount": len(normalized["tools"]),
    }
    servers = dict(host.get("mcpServers") or {})
    servers[safe_server_id] = {
        **server,
        "tools": normalized["tools"],
        "lastDiscovery": last_discovery,
    }
    write_host_mcp_server_configs(base_dir=base_dir, mcp_servers=servers)
    return {
        "ok": True,
        "status": "discovered",
        "serverId": safe_server_id,
        "toolCount": len(normalized["tools"]),
        "mcpServer": build_mcp_server_config_entry(safe_server_id, servers[safe_server_id]),
        "tools": [
            build_mcp_tool_config_entry(safe_server_id, tool, transport=str(server.get("transport") or "stdio"))
            for tool in normalized["tools"]
        ],
        "refresh": True,
    }


def mcp_execution_location_for_transport(transport: Any) -> str:
    """Describe where an MCP server executes relative to the authoritative Akane Host."""
    return "remote" if str(transport or "").strip() == "streamable_http" else "host"


def build_mcp_server_config_entry(server_id: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    safe_id = _safe_mcp_server_id(server_id)
    enabled = bool(config.get("enabled"))
    command = str(config.get("command") or "").strip()
    url = str(config.get("url") or "").strip()
    transport = str(config.get("transport") or "stdio").strip() or "stdio"
    tools = config.get("tools") if isinstance(config.get("tools"), list) else []
    last_discovery = config.get("lastDiscovery") if isinstance(config.get("lastDiscovery"), Mapping) else {}
    configured = bool((transport == "stdio" and command) or (transport == "streamable_http" and url))
    discovered = bool(last_discovery.get("status") == "ready")
    status = (
        "ready"
        if enabled and configured and discovered
        else "configured"
        if enabled and configured
        else "missing_config"
        if enabled
        else "disabled"
    )
    reason = ""
    if status == "missing_config":
        reason = "mcp_server_url_missing" if transport == "streamable_http" else "mcp_server_command_missing"
    elif status == "configured":
        reason = "mcp_tools_not_discovered"
    elif status == "disabled":
        reason = "mcp_server_disabled"
    entry = {
        "id": f"provider.mcp.{safe_id}",
        "serverId": safe_id,
        "kind": "provider",
        "type": "mcp_provider",
        "source": "mcp",
        "adapter": "mcp_streamable_http" if transport == "streamable_http" else "mcp_stdio",
        "executionMode": "external",
        "executionLocation": mcp_execution_location_for_transport(transport),
        "name": str(config.get("displayName") or safe_id or "MCP Server").strip()[:80],
        "catalogDescription": str(config.get("catalogDescription") or "").strip()[:MCP_SERVER_TEXT_MAX_LENGTH],
        "activationMode": str(config.get("activationMode") or "on_demand"),
        "pinnedTools": list(config.get("pinnedTools") or []) if isinstance(config.get("pinnedTools"), list) else [],
        "enabled": enabled,
        "configured": configured,
        "status": status,
        "reason": reason,
        "transport": transport,
        "commandName": _safe_path_basename(command),
        "argsCount": len(config.get("args") or []) if isinstance(config.get("args"), list) else 0,
        "envCount": len(config.get("env") or {}) if isinstance(config.get("env"), Mapping) else 0,
        "headerCount": len(config.get("headers") or {}) if isinstance(config.get("headers"), Mapping) else 0,
        "toolCount": len(tools),
        "lastDiscovery": {
            "status": str(last_discovery.get("status") or "")[:80],
            "discoveredAt": str(last_discovery.get("discoveredAt") or "")[:80],
            "toolCount": int(last_discovery.get("toolCount") or 0),
        }
        if last_discovery
        else {},
        "risk": "medium",
        "requiresConfirmation": False,
        "usedBy": ["agent_prompt", "external_tools"],
        "configurable": True,
    }
    projected = project_capcore_catalog_fields(entry, default_risk="medium", default_confirm="never")
    return with_capability_approval_metadata(projected)


def build_mcp_tool_config_entry(
    server_id: str,
    tool: Mapping[str, Any] | None,
    *,
    transport: str = "stdio",
) -> dict[str, Any]:
    tool = tool if isinstance(tool, Mapping) else {}
    safe_server_id = _safe_mcp_server_id(server_id)
    tool_name = _safe_mcp_tool_name(tool.get("name"))
    public_id = f"mcp.{safe_server_id}.{tool_name}" if safe_server_id and tool_name else ""
    description = _safe_public_mcp_text(tool.get("description"), limit=MCP_TOOL_DESCRIPTION_MAX_LENGTH)
    inferred_risk = _infer_mcp_tool_risk(tool_name, description)
    entry = {
        "id": public_id,
        "serverId": safe_server_id,
        "kind": "mcp_tool",
        "type": "tool",
        "source": "mcp",
        "adapter": "mcp_streamable_http" if transport == "streamable_http" else "mcp_stdio",
        "executionMode": "external",
        "executionLocation": mcp_execution_location_for_transport(transport),
        "toolType": tool_name,
        "name": tool_name,
        "description": description,
        "group": "mcp",
        "enabled": True,
        "status": "available",
        "reason": "",
        "risk": tool.get("risk"),
        "confirm": tool.get("confirm"),
        "usedBy": ["agent_prompt"],
        "providerId": f"provider.mcp.{safe_server_id}",
        "inputSchema": _normalize_mcp_input_schema(tool.get("inputSchema") or tool.get("input_schema")),
        "exposedToPrompt": bool(tool.get("promptExposed") or tool.get("prompt_exposed")),
    }
    effects = _raw_mcp_tool_effects(tool)
    if effects is not None:
        entry["effects"] = effects
    projected = project_capcore_catalog_fields(
        entry,
        default_risk=inferred_risk,
        default_confirm="first_time",
    )
    return with_capability_approval_metadata(projected)


def _summarize_mcp_server_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = int(by_status.get(status, 0)) + 1
    return {
        "total": len(entries),
        "configured": sum(1 for entry in entries if entry.get("configured")),
        "enabled": sum(1 for entry in entries if entry.get("enabled")),
        "discoveredTools": sum(int(entry.get("toolCount") or 0) for entry in entries),
        "byStatus": by_status,
    }
