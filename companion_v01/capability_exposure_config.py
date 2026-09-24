"""Per-tool presentation preferences; never execution permissions."""
from __future__ import annotations

from typing import Mapping

import config

from .local_capability_config import tool_exposure_config


MODES = frozenset({"resident", "on_demand"})


def normalize_preferences(raw):
    raw = raw if isinstance(raw, Mapping) else {}
    # The old switch is a migration input only. It cannot select the retired
    # search/load/schema-injection engine.
    old_g1 = bool(getattr(config, "ENABLE_PROGRESSIVE_CAPABILITY_DISCOVERY", False))
    default = raw.get("defaultMode", "on_demand" if old_g1 else "resident")
    if default not in MODES:
        raise ValueError("tool_exposure_mode_invalid")
    modes = raw.get("toolModes", {})
    if not isinstance(modes, Mapping) or any(
        not isinstance(key, str) or not key.strip() or value not in MODES
        for key, value in modes.items()
    ):
        raise ValueError("tool_exposure_modes_invalid")
    search = raw.get("searchEnabled", True)
    revision = raw.get("revision", 0)
    if not isinstance(search, bool) or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("tool_exposure_config_invalid")
    return {"schemaVersion": 1, "revision": revision, "defaultMode": default,
            "searchEnabled": search, "toolModes": dict(sorted(modes.items())),
            "defaultSource": "saved_preferences" if "defaultMode" in raw else (
                "legacy_g1" if old_g1 else "resident_default")}


def read_preferences(*, base_dir=None, profile_user_id=""):
    if not profile_user_id or base_dir is None:
        return normalize_preferences({})
    return normalize_preferences(tool_exposure_config(base_dir=base_dir, profile_user_id=profile_user_id))


def save_preferences(*, base_dir, profile_user_id, payload):
    if not isinstance(payload, Mapping):
        return {"ok": False, "status": "rejected", "reason": "tool_exposure_config_invalid"}

    def update(section):
        current = normalize_preferences(section)
        if payload.get("revision") != current["revision"]:
            return {"ok": False, "status": "conflict", "reason": "tool_exposure_revision_conflict", "preferences": current}
        try:
            candidate = normalize_preferences({**current, **{key: payload[key] for key in ("toolModes", "defaultMode", "searchEnabled") if key in payload}})
        except ValueError as exc:
            return {"ok": False, "status": "rejected", "reason": str(exc)}
        if any(candidate[key] != current[key] for key in ("toolModes", "defaultMode", "searchEnabled")):
            candidate["revision"] += 1
        section.clear()
        section.update(candidate)
        return {"ok": True, "status": "saved", "preferences": candidate}

    try:
        return tool_exposure_config(base_dir=base_dir, profile_user_id=profile_user_id, update=update)
    except (ValueError, OSError):
        return {"ok": False, "status": "failed", "reason": "tool_exposure_save_failed"}


def exposure_mode(capability_id, handler, preferences):
    explicit = preferences["toolModes"].get(capability_id)
    if explicit:
        return explicit
    preferred = getattr(handler, "default_exposure_mode", "")
    if preferred in MODES:
        return preferred
    adapter = getattr(handler, "adapter", None)
    if getattr(adapter, "type", "") == "mcp_stdio":
        server = adapter.server_config
        name = adapter.tool_name_for_capability(capability_id)
        return "resident" if server.get("activationMode") == "pinned" and name in server.get("pinnedTools", ()) else "on_demand"
    return preferences["defaultMode"]
