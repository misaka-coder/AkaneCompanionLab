"""Local provider catalog, configuration and bounded health checks."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .approval_policy import (
    project_capcore_catalog_fields,
    with_capability_approval_metadata,
)
from .common import (
    _now_iso,
    _public_config_scope,
    _safe_reason,
)
from .definitions import (
    CONFIGURABLE_PROVIDER_BY_ID,
    CONFIGURABLE_PROVIDER_SPECS,
    CONFIG_SCHEMA_VERSION,
    HealthChecker,
    ProviderConfigSpec,
)
from .provider_validation import (
    _safe_endpoint_for_output,
    normalize_local_http_endpoint,
    normalize_provider_config_payload,
)
from .storage import (
    load_capability_config,
    write_capability_config,
)


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
    if existing.get("endpoint") == normalized["endpoint"] and isinstance(existing.get("lastHealth"), Mapping):
        next_provider["lastHealth"] = existing["lastHealth"]
    providers[spec.id] = next_provider
    config = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "providers": providers,
        "workflows": config.get("workflows", {}),
        "voiceProfiles": config.get("voiceProfiles", {}),
        "mcpServers": config.get("mcpServers", {}),
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
    checker = health_checker or (_irodori_health_check if spec.adapter == "irodori" else _socket_health_check)
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
                "voiceProfiles": config.get("voiceProfiles", {}),
                "mcpServers": config.get("mcpServers", {}),
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


def _irodori_health_check(host, port, timeout_seconds):
    import json
    import urllib.request
    host = "[::1]" if host == "::1" else host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/akane/status", timeout=max(timeout_seconds, 1.0)) as response:
            value = json.loads(response.read(65536))
        ready = value.get("ready") is True and value.get("loaded") is True
        return ready, "" if ready else "irodori_not_ready"
    except Exception:
        return False, "irodori_unreachable"


def build_provider_config_entry(spec: ProviderConfigSpec, config: Mapping[str, Any] | None) -> dict[str, Any]:
    config = config if isinstance(config, Mapping) else {}
    config_status = str(config.get("status") or "").strip()
    endpoint = _safe_endpoint_for_output(config.get("endpoint"))
    enabled = bool(config.get("enabled")) if config_status != "invalid_config" else False
    last_health = config.get("lastHealth") if isinstance(config.get("lastHealth"), Mapping) else {}
    configured = bool(endpoint)
    status = (
        "invalid_config"
        if config_status == "invalid_config"
        else _provider_status(
            configured=configured,
            enabled=enabled,
            last_health=last_health,
        )
    )
    entry = {
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
        "configurable": True,
    }
    projected = project_capcore_catalog_fields(entry, default_risk=spec.risk, default_confirm="never")
    return with_capability_approval_metadata(projected)


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


def _socket_health_check(host: str, port: int, timeout_seconds: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True, ""
    except OSError as exc:
        return False, _safe_reason(str(exc) or "connection_failed")
