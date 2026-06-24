from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

from companion_v01.local_capability_config import (
    LOOPBACK_HOSTS,
    MCP_SAFE_TYPE_RE,
    MCP_SECRET_MARKERS,
)

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityManifest,
    ConfirmPolicy,
    EndpointConfig,
    HealthConfig,
    InvalidManifest,
    RiskLevel,
    SourceLayer,
    TierConfig,
    TriggerConfig,
)


SCHEMA = "capability_adapter/v1"
ALLOWED_ADAPTER_TYPES = frozenset(
    {
        "mcp_stdio",
        "comfyui",
        "openai_compat_tts",
        "openai_compat_asr",
        "python_plugin",
    }
)
VISIBLE_IN_VALUES = frozenset({"base", "web", "desktop", "qq"})
RISK_VALUES = frozenset({"low", "medium", "high"})
CONFIRM_VALUES = frozenset({"never", "first_time", "always"})
EFFECT_VALUES = frozenset(
    {
        "file_read",
        "file_write",
        "command_exec",
        "network_outbound",
        "browser_action",
        "media_generation",
        "state_mutation",
    }
)
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SECRET_VALUE_RE = re.compile(
    r"(?i)(\bbearer\s+\S+|\bsk-[A-Za-z0-9]|\bghp_[A-Za-z0-9]|\bxox[baprs]-[A-Za-z0-9]|=|:)"
)


def load_manifest(path: Path, *, source_layer: SourceLayer) -> CapabilityManifest | InvalidManifest:
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
        loaded = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return _invalid(path, source_layer, "yaml_parse_error", str(exc))
    except OSError as exc:
        return _invalid(path, source_layer, "read_error", str(exc))

    if not isinstance(loaded, Mapping):
        return _invalid(path, source_layer, "manifest_must_be_mapping", "top-level yaml must be a mapping")

    raw: Mapping[str, Any] = loaded
    schema = str(raw.get("schema") or "").strip()
    if schema != SCHEMA:
        return _invalid(path, source_layer, "schema_mismatch", f"expected {SCHEMA}")

    provider = raw.get("provider")
    if not isinstance(provider, Mapping):
        return _invalid(path, source_layer, "missing_provider", "provider must be a mapping")

    provider_id = str(provider.get("id") or "").strip()
    if not provider_id:
        return _invalid(path, source_layer, "missing_provider_id", "provider.id is required")
    if not PROVIDER_ID_RE.fullmatch(provider_id):
        return _invalid(path, source_layer, "invalid_provider_id", "provider.id contains unsupported characters", provider_id)

    provider_type = str(provider.get("type") or "").strip()
    if not provider_type:
        return _invalid(path, source_layer, "missing_provider_type", "provider.type is required", provider_id)
    if not MCP_SAFE_TYPE_RE.fullmatch(provider_type):
        return _invalid(path, source_layer, "provider_type_invalid", "provider.type contains unsupported characters", provider_id)
    if provider_type not in ALLOWED_ADAPTER_TYPES:
        return _invalid(path, source_layer, "provider_type_not_allowed", provider_type, provider_id)

    endpoint_result = _parse_endpoint(provider.get("endpoint"), path, source_layer, provider_id)
    if isinstance(endpoint_result, InvalidManifest):
        return endpoint_result

    secrets_result = _parse_secrets(provider.get("secrets"), path, source_layer, provider_id)
    if isinstance(secrets_result, InvalidManifest):
        return secrets_result

    tiers_result = _parse_tiers(provider.get("tiers"), path, source_layer, provider_id)
    if isinstance(tiers_result, InvalidManifest):
        return tiers_result

    capabilities_result = _parse_capabilities(raw.get("capabilities"), path, source_layer, provider_id)
    if isinstance(capabilities_result, InvalidManifest):
        return capabilities_result

    health = _parse_health(provider.get("health"))
    display_name = str(provider.get("display_name") or provider_id).strip() or provider_id
    return CapabilityManifest(
        schema=schema,
        provider_id=provider_id,
        provider_type=provider_type,
        display_name=display_name,
        endpoint=endpoint_result,
        health=health,
        tiers=tiers_result,
        capabilities=capabilities_result,
        secrets=secrets_result,
        source_path=path,
        source_layer=source_layer,
        raw=raw,
    )


def _invalid(
    path: Path,
    source_layer: SourceLayer,
    reason: str,
    detail: str = "",
    provider_id: str = "",
) -> InvalidManifest:
    return InvalidManifest(
        source_path=path,
        source_layer=source_layer,
        reason=reason,
        detail=detail,
        provider_id=provider_id,
    )


def _parse_endpoint(
    raw_endpoint: Any,
    path: Path,
    source_layer: SourceLayer,
    provider_id: str,
) -> EndpointConfig | None | InvalidManifest:
    if raw_endpoint in (None, ""):
        return None
    if not isinstance(raw_endpoint, Mapping):
        return _invalid(path, source_layer, "endpoint_must_be_mapping", "provider.endpoint must be a mapping", provider_id)
    url = str(raw_endpoint.get("url") or "").strip()
    loopback_only = bool(raw_endpoint.get("loopback_only"))
    if loopback_only:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        if host not in LOOPBACK_HOSTS:
            return _invalid(path, source_layer, "endpoint_not_loopback", f"host={host or '<empty>'}", provider_id)
    return EndpointConfig(url=url, loopback_only=loopback_only, raw=raw_endpoint)


def _parse_health(raw_health: Any) -> HealthConfig | None:
    if not isinstance(raw_health, Mapping):
        return None
    statuses: list[int] = []
    raw_statuses = raw_health.get("expect_status")
    if isinstance(raw_statuses, list):
        for item in raw_statuses:
            try:
                statuses.append(int(item))
            except (TypeError, ValueError):
                continue
    try:
        timeout_seconds = float(raw_health.get("timeout_seconds") or 3)
    except (TypeError, ValueError):
        timeout_seconds = 3.0
    return HealthConfig(
        method=str(raw_health.get("method") or "GET").strip().upper() or "GET",
        path=str(raw_health.get("path") or "").strip(),
        timeout_seconds=timeout_seconds,
        expect_status=tuple(statuses or [200]),
        raw=raw_health,
    )


def _parse_tiers(
    raw_tiers: Any,
    path: Path,
    source_layer: SourceLayer,
    provider_id: str,
) -> tuple[TierConfig, ...] | InvalidManifest:
    if raw_tiers in (None, ""):
        return ()
    if not isinstance(raw_tiers, list):
        return _invalid(path, source_layer, "tiers_must_be_list", "provider.tiers must be a list", provider_id)
    seen: set[str] = set()
    tiers: list[TierConfig] = []
    for raw_tier in raw_tiers:
        if not isinstance(raw_tier, Mapping):
            return _invalid(path, source_layer, "tier_must_be_mapping", "tier entry must be a mapping", provider_id)
        tier_id = str(raw_tier.get("id") or "").strip()
        if not tier_id or tier_id in seen:
            return _invalid(path, source_layer, "tier_id_not_unique", tier_id or "<empty>", provider_id)
        seen.add(tier_id)
        preset = raw_tier.get("preset") if isinstance(raw_tier.get("preset"), Mapping) else {}
        tiers.append(
            TierConfig(
                id=tier_id,
                label=str(raw_tier.get("label") or tier_id).strip() or tier_id,
                preset=preset,
                raw=raw_tier,
            )
        )
    return tuple(tiers)


def _parse_secrets(
    raw_secrets: Any,
    path: Path,
    source_layer: SourceLayer,
    provider_id: str,
) -> tuple[str, ...] | InvalidManifest:
    if raw_secrets in (None, ""):
        return ()
    if not isinstance(raw_secrets, list):
        return _invalid(path, source_layer, "secrets_must_be_key_names", "provider.secrets must be a list of key names", provider_id)
    secrets: list[str] = []
    for item in raw_secrets:
        if not isinstance(item, str):
            return _invalid(path, source_layer, "secrets_must_be_key_names", "secret entries must be strings", provider_id)
        value = item.strip()
        lowered = value.lower()
        if not value or len(value) > 120 or re.search(r"\s", value) or SECRET_VALUE_RE.search(value):
            return _invalid(path, source_layer, "secrets_must_be_key_names", "secret entry looks like a value", provider_id)
        if any(marker == lowered for marker in MCP_SECRET_MARKERS):
            return _invalid(path, source_layer, "secrets_must_be_key_names", "secret entry is too generic", provider_id)
        secrets.append(value)
    return tuple(secrets)


def _parse_capabilities(
    raw_capabilities: Any,
    path: Path,
    source_layer: SourceLayer,
    provider_id: str,
) -> tuple[CapabilityDescriptor, ...] | InvalidManifest:
    if raw_capabilities in (None, ""):
        return ()
    if not isinstance(raw_capabilities, list):
        return _invalid(path, source_layer, "capabilities_must_be_list", "capabilities must be a list", provider_id)
    capabilities: list[CapabilityDescriptor] = []
    for raw_capability in raw_capabilities:
        if not isinstance(raw_capability, Mapping):
            return _invalid(path, source_layer, "capability_must_be_mapping", "capability entry must be a mapping", provider_id)
        capability_id = str(raw_capability.get("id") or "").strip()
        if not capability_id:
            return _invalid(path, source_layer, "missing_capability_id", "capability.id is required", provider_id)
        visible_in = _string_tuple(raw_capability.get("visible_in"))
        if any(item not in VISIBLE_IN_VALUES for item in visible_in):
            return _invalid(path, source_layer, "visible_in_invalid", ",".join(visible_in), provider_id)
        risk, confirm = _risk_and_confirm(raw_capability)
        effects = _string_tuple(raw_capability.get("effects"))
        unknown_effects = [item for item in effects if item not in EFFECT_VALUES]
        if unknown_effects:
            return _invalid(path, source_layer, "effects_invalid", ",".join(unknown_effects), provider_id)
        risk, confirm = _apply_effect_risk(risk, confirm, effects)
        trigger = _parse_trigger(raw_capability.get("trigger"))
        capabilities.append(
            CapabilityDescriptor(
                id=capability_id,
                display_name=str(raw_capability.get("display_name") or capability_id).strip() or capability_id,
                short_hint=str(raw_capability.get("short_hint") or "").strip(),
                visible_in=visible_in,
                prompt_exposed=bool(raw_capability.get("prompt_exposed", False)),
                risk=risk,
                confirm=confirm,
                effects=effects,
                trigger=trigger,
                inputs=_parse_io_slots(raw_capability.get("inputs")),
                outputs=_parse_io_slots(raw_capability.get("outputs")),
                raw=raw_capability,
            )
        )
    return tuple(capabilities)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return tuple(result)


def _risk_and_confirm(raw_capability: Mapping[str, Any]) -> tuple[RiskLevel, ConfirmPolicy]:
    risk = str(raw_capability.get("risk") or "medium").strip().lower()
    if risk not in RISK_VALUES:
        risk = "medium"
    confirm = str(raw_capability.get("confirm") or "first_time").strip().lower()
    if confirm not in CONFIRM_VALUES:
        confirm = "first_time"
    return risk, confirm  # type: ignore[return-value]


def _apply_effect_risk(
    risk: RiskLevel,
    confirm: ConfirmPolicy,
    effects: tuple[str, ...],
) -> tuple[RiskLevel, ConfirmPolicy]:
    effect_set = set(effects)
    if effect_set & {"command_exec", "browser_action"}:
        return "high", "always"
    if effect_set & {"file_write", "network_outbound"} and risk == "low":
        return "medium", "first_time" if confirm == "never" else confirm
    if risk == "high":
        return "high", "always"
    return risk, confirm


def _parse_trigger(raw_trigger: Any) -> TriggerConfig | None:
    if not isinstance(raw_trigger, Mapping):
        return None
    kind = str(raw_trigger.get("kind") or "").strip()
    if not kind:
        return None
    return TriggerConfig(kind=kind, raw=raw_trigger)


def _parse_io_slots(raw_slots: Any) -> tuple[CapabilityIOSlot, ...]:
    if not isinstance(raw_slots, list):
        return ()
    slots: list[CapabilityIOSlot] = []
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, Mapping):
            continue
        name = str(raw_slot.get("name") or "").strip()
        kind = str(raw_slot.get("kind") or "").strip()
        if not name or not kind:
            continue
        max_bytes: int | None = None
        raw_max_bytes = raw_slot.get("max_bytes")
        if raw_max_bytes not in (None, ""):
            try:
                max_bytes = int(str(raw_max_bytes).replace("_", ""))
            except (TypeError, ValueError):
                max_bytes = None
        slots.append(
            CapabilityIOSlot(
                name=name,
                kind=kind,
                required=bool(raw_slot.get("required")),
                max_bytes=max_bytes,
                delivery=str(raw_slot.get("delivery") or "").strip(),
                raw=raw_slot,
            )
        )
    return tuple(slots)
