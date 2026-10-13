"""Pure loopback endpoint and persisted provider validation."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from .common import (
    _safe_reason,
    _safe_short_text,
)
from .definitions import (
    CONFIGURABLE_PROVIDER_SPECS,
    LOOPBACK_HOSTS,
    ProviderConfigSpec,
)


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


def _invalid_provider_config(spec: ProviderConfigSpec, reason: str) -> dict[str, Any]:
    return {"enabled": False, "status": "invalid_config", "reason": _safe_reason(reason), "providerId": spec.id}


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


def _safe_endpoint_for_output(value: Any) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    normalized = normalize_local_http_endpoint(endpoint)
    return str(normalized.get("endpoint") or "") if normalized.get("ok") else ""
