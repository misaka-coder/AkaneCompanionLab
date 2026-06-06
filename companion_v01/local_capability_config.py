from __future__ import annotations

import json
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse, urlunparse


CONFIG_SCHEMA_VERSION = 1
PROFILE_CONFIG_PATH_TEMPLATE = "users_data/<profile_user_id>/capabilities/capabilities.yaml"
PROFILE_ID_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PUBLIC_PROVIDER_FIELDS = {"enabled", "endpoint", "updatedAt", "lastHealth"}


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
    if (
        existing.get("endpoint") == normalized["endpoint"]
        and isinstance(existing.get("lastHealth"), Mapping)
    ):
        next_provider["lastHealth"] = existing["lastHealth"]
    providers[spec.id] = next_provider
    config = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "providers": providers,
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
            config={"schemaVersion": CONFIG_SCHEMA_VERSION, "providers": providers},
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


def load_capability_config(*, base_dir: Path | str | None, profile_user_id: str) -> dict[str, Any]:
    path = _profile_config_path(base_dir, profile_user_id)
    if path is None or not path.exists():
        return {"schemaVersion": CONFIG_SCHEMA_VERSION, "configStatus": "missing", "providers": {}, "warnings": []}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_file_invalid_json",
            "providers": {},
            "warnings": [{"status": "invalid_config", "reason": "provider_config_file_invalid_json"}],
        }
    if not isinstance(data, dict):
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "configStatus": "invalid_config",
            "reason": "provider_config_root_must_be_object",
            "providers": {},
            "warnings": [{"status": "invalid_config", "reason": "provider_config_root_must_be_object"}],
        }
    providers, warnings = _sanitize_provider_configs(data.get("providers"))
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "configStatus": "partial_invalid_config" if warnings else "available",
        "providers": providers,
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
    return {"schemaVersion": CONFIG_SCHEMA_VERSION, "providers": write_providers}


def _safe_endpoint_for_output(value: Any) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    normalized = normalize_local_http_endpoint(endpoint)
    return str(normalized.get("endpoint") or "") if normalized.get("ok") else ""


def _safe_reason(value: Any) -> str:
    text = _safe_short_text(value, limit=160)
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)=([^\s&]+)", r"\1=redacted", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[local_path]", text)
    return text


def _safe_short_text(value: Any, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
