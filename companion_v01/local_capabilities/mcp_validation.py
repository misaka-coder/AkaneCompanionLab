"""MCP endpoint, placeholder, tool-schema and public-field validation."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from .approval_policy import project_capcore_catalog_fields
from .common import (
    _safe_optional_bool,
    _safe_reason,
    _safe_short_text,
)
from .definitions import (
    MCP_ENV_KEY_RE,
    MCP_ENV_PLACEHOLDER_RE,
    MCP_HEADER_NAME_RE,
    MCP_SECRET_MARKERS,
    MCP_SERVER_ARG_MAX_COUNT,
    MCP_SERVER_ARG_MAX_LENGTH,
    MCP_SERVER_ENV_MAX_COUNT,
    MCP_SERVER_HEADER_MAX_COUNT,
    MCP_SERVER_ID_MAX_LENGTH,
    MCP_SERVER_PATH_MAX_LENGTH,
    MCP_SERVER_TEXT_MAX_LENGTH,
    MCP_TOOL_DESCRIPTION_MAX_LENGTH,
    MCP_TOOL_NAME_MAX_LENGTH,
    PROFILE_ID_SAFE_CHARS,
)


def normalize_mcp_server_config_payload(server_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    activation_mode = str(
        payload.get("activationMode") or payload.get("activation_mode") or "on_demand"
    ).strip().lower()
    if activation_mode not in {"on_demand", "pinned"}:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_activation_mode_invalid"}
    transport = str(payload.get("transport") or "stdio").strip().lower().replace("-", "_")
    if transport in {"http", "streamablehttp"}:
        transport = "streamable_http"
    if transport not in {"stdio", "streamable_http"}:
        return {"ok": False, "status": "unsupported_transport", "reason": "mcp_transport_not_supported"}
    command = _safe_private_mcp_path(payload.get("command")) if transport == "stdio" else ""
    if transport == "stdio" and not command:
        return {"ok": False, "status": "missing_config", "reason": "mcp_server_command_required"}
    args = _safe_mcp_args(payload.get("args")) if transport == "stdio" else []
    if args is None:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_server_args_invalid"}
    env = _safe_mcp_env(payload.get("env")) if transport == "stdio" else {}
    if env is None:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_server_env_invalid"}
    cwd = _safe_private_mcp_path(payload.get("cwd")) if transport == "stdio" else ""
    url = _safe_mcp_http_url(payload.get("url") or payload.get("endpoint")) if transport == "streamable_http" else ""
    if transport == "streamable_http" and not url:
        return {"ok": False, "status": "missing_config", "reason": "mcp_server_url_required"}
    headers = _safe_mcp_headers(payload.get("headers")) if transport == "streamable_http" else {}
    if headers is None:
        return {"ok": False, "status": "invalid_config", "reason": "mcp_server_headers_invalid"}
    return {
        "ok": True,
        "status": "valid",
        "enabled": bool(payload.get("enabled", True)),
        "displayName": _safe_short_text(payload.get("displayName") or payload.get("name") or server_id, limit=80)
        or server_id,
        "catalogDescription": _safe_short_text(
            payload.get("catalogDescription") or payload.get("catalog_description"),
            limit=MCP_SERVER_TEXT_MAX_LENGTH,
        ),
        "activationMode": activation_mode,
        "pinnedTools": _safe_mcp_tool_name_list(payload.get("pinnedTools") or payload.get("pinned_tools")),
        "transport": transport,
        "command": command,
        "args": args,
        "cwd": cwd,
        "env": env,
        "url": url,
        "headers": headers,
        "lowRiskAllowlist": _safe_mcp_tool_name_list(
            payload.get("lowRiskAllowlist") or payload.get("low_risk_allowlist")
        ),
    }


def normalize_mcp_tool_discovery_payload(server_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    raw_tools = payload.get("tools")
    if raw_tools is None and isinstance(payload.get("capabilities"), Mapping):
        raw_tools = payload.get("capabilities", {}).get("tools")
    if not isinstance(raw_tools, list):
        return {"ok": False, "status": "invalid_discovery", "reason": "mcp_tools_must_be_list"}
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_tool in raw_tools:
        tool = _normalize_mcp_tool_config(server_id, raw_tool)
        if not tool:
            continue
        name = tool["name"]
        if name in seen:
            suffix = 2
            unique = f"{name}_{suffix}"
            while unique in seen:
                suffix += 1
                unique = f"{name}_{suffix}"
            tool["name"] = unique[:MCP_TOOL_NAME_MAX_LENGTH]
            name = tool["name"]
        seen.add(name)
        tools.append(tool)
    return {"ok": True, "status": "valid", "tools": tools}


def _sanitize_mcp_server_configs(raw_servers: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    servers: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    if raw_servers in (None, ""):
        return servers, warnings
    if not isinstance(raw_servers, Mapping):
        return servers, [{"status": "invalid_config", "reason": "mcp_servers_must_be_object"}]

    for raw_server_id, raw_config in raw_servers.items():
        server_id = _safe_mcp_server_id(raw_server_id)
        if not server_id:
            warnings.append({"status": "invalid_config", "reason": "mcp_server_id_invalid"})
            continue
        normalized = normalize_mcp_server_config_payload(
            server_id, raw_config if isinstance(raw_config, Mapping) else {}
        )
        if not normalized.get("ok"):
            warnings.append(
                {
                    "serverId": server_id,
                    "status": normalized.get("status") or "invalid_config",
                    "reason": normalized.get("reason") or "invalid_mcp_server_config",
                }
            )
            continue
        server: dict[str, Any] = {
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
        }
        low_risk_allowlist = _safe_mcp_tool_name_list(
            (raw_config or {}).get("lowRiskAllowlist") or (raw_config or {}).get("low_risk_allowlist")
        )
        if low_risk_allowlist:
            server["lowRiskAllowlist"] = low_risk_allowlist
        updated_at = _safe_short_text((raw_config or {}).get("updatedAt")) if isinstance(raw_config, Mapping) else ""
        if updated_at:
            server["updatedAt"] = updated_at
        raw_tools = (raw_config or {}).get("tools") if isinstance(raw_config, Mapping) else None
        normalized_tools = normalize_mcp_tool_discovery_payload(server_id, {"tools": raw_tools or []})
        if normalized_tools.get("ok") and normalized_tools.get("tools"):
            server["tools"] = [
                _apply_mcp_low_risk_allowlist(tool, low_risk_allowlist) for tool in normalized_tools["tools"]
            ]
        last_discovery = _sanitize_mcp_last_discovery(
            (raw_config or {}).get("lastDiscovery") if isinstance(raw_config, Mapping) else None
        )
        if last_discovery:
            server["lastDiscovery"] = last_discovery
        servers[server_id] = server
    return servers, warnings


def _sanitize_mcp_server_overrides(
    raw_overrides: Any,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if raw_overrides in (None, ""):
        return {}, []
    if not isinstance(raw_overrides, Mapping):
        return {}, [{"status": "invalid_config", "reason": "mcp_server_overrides_must_be_object"}]
    overrides: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    for raw_server_id, raw_override in raw_overrides.items():
        server_id = _safe_mcp_server_id(raw_server_id)
        if not server_id or not isinstance(raw_override, Mapping):
            warnings.append({"status": "invalid_config", "reason": "mcp_server_override_invalid"})
            continue
        override: dict[str, Any] = {}
        if "enabled" in raw_override:
            override["enabled"] = bool(raw_override.get("enabled"))
        if "activationMode" in raw_override:
            activation_mode = str(raw_override.get("activationMode") or "on_demand").strip().lower()
            override["activationMode"] = activation_mode if activation_mode in {"on_demand", "pinned"} else "on_demand"
        if "pinnedTools" in raw_override:
            override["pinnedTools"] = _safe_mcp_tool_name_list(raw_override.get("pinnedTools"))
        if "lowRiskAllowlist" in raw_override:
            override["lowRiskAllowlist"] = _safe_mcp_tool_name_list(raw_override.get("lowRiskAllowlist"))
        if override:
            overrides[server_id] = override
    return overrides, warnings


def _sanitize_mcp_last_discovery(raw_last_discovery: Any) -> dict[str, Any]:
    if not isinstance(raw_last_discovery, Mapping):
        return {}
    status = str(raw_last_discovery.get("status") or "").strip()
    if status not in {"ready", "unavailable", "failed"}:
        return {}
    result: dict[str, Any] = {"status": status}
    discovered_at = _safe_short_text(raw_last_discovery.get("discoveredAt"))
    if discovered_at:
        result["discoveredAt"] = discovered_at
    try:
        result["toolCount"] = max(0, int(raw_last_discovery.get("toolCount") or 0))
    except Exception:
        result["toolCount"] = 0
    if status != "ready":
        result["reason"] = _safe_reason(raw_last_discovery.get("reason") or "mcp_discovery_failed")
    return result


def _normalize_mcp_tool_config(server_id: str, raw_tool: Any) -> dict[str, Any]:
    if not isinstance(raw_tool, Mapping):
        return {}
    tool_name = _safe_mcp_tool_name(raw_tool.get("name"))
    if not tool_name:
        return {}
    description = _safe_public_mcp_text(raw_tool.get("description"), limit=MCP_TOOL_DESCRIPTION_MAX_LENGTH)
    inferred_risk = _infer_mcp_tool_risk(tool_name, description)
    prompt_exposed = _safe_optional_bool(
        raw_tool.get("promptExposed") if "promptExposed" in raw_tool else raw_tool.get("prompt_exposed"),
        default=False,
    )
    projection_entry = {
        "id": f"mcp.{_safe_mcp_server_id(server_id)}.{tool_name}",
        "name": tool_name,
        "description": description,
        "risk": raw_tool.get("risk"),
        "confirm": raw_tool.get("confirm"),
    }
    effects = _raw_mcp_tool_effects(raw_tool)
    if effects is not None:
        projection_entry["effects"] = effects
    normalized = project_capcore_catalog_fields(
        projection_entry,
        default_risk=inferred_risk,
        default_confirm="first_time",
    )
    result = {
        "name": tool_name,
        "description": description,
        "inputSchema": _normalize_mcp_input_schema(raw_tool.get("inputSchema") or raw_tool.get("input_schema")),
        "risk": normalized["risk"],
        "confirm": normalized["confirm"],
        "promptExposed": bool(prompt_exposed),
    }
    annotations = raw_tool.get("annotations")
    if isinstance(annotations, Mapping):
        result["annotations"] = {key:annotations[key] for key in
            ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            if key in annotations and (annotations[key] is None or isinstance(annotations[key], bool))}
        if "title" in annotations:
            result["annotations"]["title"] = (_safe_public_mcp_text(annotations["title"], limit=2048)
                if annotations["title"] is not None else None)
    output_schema = raw_tool.get("outputSchema") or raw_tool.get("output_schema")
    if isinstance(output_schema, Mapping):
        result["outputSchema"] = _normalize_mcp_input_schema(output_schema)
    if normalized.get("effects"):
        result["effects"] = list(normalized["effects"])
    return result


def _apply_mcp_low_risk_allowlist(tool: Mapping[str, Any], allowlist: list[str]) -> dict[str, Any]:
    normalized = dict(tool)
    if str(normalized.get("risk") or "").strip().lower() == "low":
        tool_name = str(normalized.get("name") or "").strip()
        if tool_name not in set(allowlist):
            normalized["risk"] = "medium"
    return normalized


def _safe_mcp_tool_name_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        tool_name = _safe_mcp_tool_name(item)
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        result.append(tool_name)
    return result


def _raw_mcp_tool_effects(tool: Mapping[str, Any]) -> Any | None:
    value = tool.get("effects") if "effects" in tool else tool.get("effect")
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (list, tuple, set)):
        return value if value else None
    return None


def _normalize_mcp_input_schema(raw_schema: Any) -> dict[str, Any]:
    if not isinstance(raw_schema, Mapping):
        return {"type": "object", "properties": {}, "required": []}
    try:
        normalized = json.loads(json.dumps(raw_schema, ensure_ascii=False, default=str))
    except Exception:
        return {"type": "object", "properties": {}, "required": []}
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}, "required": []}
    normalized = _sanitize_mcp_schema_node(normalized)
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}, "required": []}
    normalized.setdefault("type", "object")
    return normalized


def _sanitize_mcp_schema_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_mcp_schema_node(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    removed_properties: set[str] = set()
    for key, item in value.items():
        clean_key = str(key)
        if clean_key == "properties" and isinstance(item, dict):
            properties: dict[str, Any] = {}
            for raw_name, raw_property in item.items():
                name = str(raw_name)
                if any(marker in name.lower() for marker in MCP_SECRET_MARKERS):
                    removed_properties.add(name)
                    continue
                properties[name] = _sanitize_mcp_schema_node(raw_property)
            result[clean_key] = properties
        elif clean_key in {"description", "title"}:
            result[clean_key] = _safe_public_mcp_text(item, limit=2048)
        else:
            result[clean_key] = _sanitize_mcp_schema_node(item)
    if removed_properties and isinstance(result.get("required"), list):
        result["required"] = [item for item in result["required"] if str(item) not in removed_properties]
    return result


def _safe_mcp_server_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:MCP_SERVER_ID_MAX_LENGTH] if safe else ""


def _safe_mcp_tool_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    safe = "".join(ch if ch in PROFILE_ID_SAFE_CHARS else "_" for ch in raw)
    safe = safe.strip("._-")
    return safe[:MCP_TOOL_NAME_MAX_LENGTH] if safe else ""


def _safe_public_mcp_text(value: Any, *, limit: int = MCP_SERVER_TEXT_MAX_LENGTH) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    if any(marker in lowered for marker in ("api_key", "password", "secret", "token")):
        return ""
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text[:limit]


def _safe_private_mcp_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in MCP_SECRET_MARKERS):
        return ""
    return text[:MCP_SERVER_PATH_MAX_LENGTH]


def _safe_mcp_http_url(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text or len(text) > MCP_SERVER_PATH_MAX_LENGTH or _mcp_text_has_secret_literal(text):
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def _safe_mcp_headers(value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        return None
    headers: dict[str, str] = {}
    for raw_key, raw_value in list(value.items())[:MCP_SERVER_HEADER_MAX_COUNT]:
        key = str(raw_key or "").strip()
        text = str(raw_value or "").strip().replace("\r", "").replace("\n", "")
        if not key or not MCP_HEADER_NAME_RE.fullmatch(key) or not text:
            return None
        if _mcp_text_has_secret_literal(text):
            return None
        headers[key] = text[:MCP_SERVER_PATH_MAX_LENGTH]
    return headers


def _mcp_server_endpoint_identity(server: Mapping[str, Any]) -> tuple[Any, ...]:
    transport = str(server.get("transport") or "stdio").strip().lower().replace("-", "_")
    if transport in {"http", "streamablehttp"}:
        transport = "streamable_http"
    if transport == "streamable_http":
        headers = server.get("headers") if isinstance(server.get("headers"), Mapping) else {}
        return (
            transport,
            str(server.get("url") or ""),
            tuple(sorted((str(key), str(value)) for key, value in headers.items())),
        )
    return (
        "stdio",
        str(server.get("command") or ""),
        tuple(str(item) for item in server.get("args") or []),
        str(server.get("cwd") or ""),
    )


def _mcp_text_has_secret_literal(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    without_placeholders = MCP_ENV_PLACEHOLDER_RE.sub("", text)
    lowered = without_placeholders.lower()
    if any(marker in lowered for marker in MCP_SECRET_MARKERS):
        return True
    if re.search(r"(?i)\bbearer\s+[^\s$][^\s]*", without_placeholders):
        return True
    if re.search(r"(?i)\b(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|xox[baprs]-[A-Za-z0-9])", without_placeholders):
        return True
    return False


def _safe_mcp_args(value: Any) -> list[str] | None:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        return None
    args: list[str] = []
    for item in value[:MCP_SERVER_ARG_MAX_COUNT]:
        text = str(item or "").strip().replace("\r", "").replace("\n", "")
        lowered = text.lower()
        if not text:
            continue
        if any(marker in lowered for marker in MCP_SECRET_MARKERS) and not MCP_ENV_PLACEHOLDER_RE.search(text):
            return None
        if _mcp_text_has_secret_literal(text):
            return None
        args.append(text[:MCP_SERVER_ARG_MAX_LENGTH])
    return args


def _safe_mcp_env(value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        return None
    env: dict[str, str] = {}
    for raw_key, raw_value in list(value.items())[:MCP_SERVER_ENV_MAX_COUNT]:
        key = str(raw_key or "").strip()
        if not key or not MCP_ENV_KEY_RE.fullmatch(key):
            return None
        text = str(raw_value or "").strip().replace("\r", "").replace("\n", "")
        lowered_value = text.lower()
        is_placeholder = bool(MCP_ENV_PLACEHOLDER_RE.fullmatch(text))
        # Secret-shaped variable names are normal MCP configuration. Persist
        # only a Host-side placeholder, never a literal credential from a UI
        # or model tool call.
        if any(marker in key.lower() for marker in MCP_SECRET_MARKERS) and not is_placeholder:
            return None
        if not is_placeholder and (
            any(marker in lowered_value for marker in MCP_SECRET_MARKERS) or _mcp_text_has_secret_literal(text)
        ):
            return None
        env[key] = text[:MCP_SERVER_TEXT_MAX_LENGTH]
    return env


def _infer_mcp_tool_risk(tool_name: str, description: str) -> str:
    text = f"{tool_name} {description}".lower()
    if re.search(
        r"\b(delete|remove|write|edit|shell|terminal|exec|command|click|open_url|navigate|download|upload)\b", text
    ):
        return "high"
    if re.search(r"\b(file|read|http|fetch|search|web|workspace|local)\b", text):
        return "medium"
    return "medium"
