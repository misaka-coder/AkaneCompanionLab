"""Control-center view over preferences, current eligibility and published context."""
from __future__ import annotations

import config

from .capability_exposure_config import read_preferences, save_preferences
from .capability_exposure import native_exposure_scope


def read_exposure_state(engine, *, base_dir, profile_user_id, session_id, character_pack_id="", config_module=None):
    preferences = read_preferences(base_dir=base_dir, profile_user_id=profile_user_id)
    scope = dict(profile_user_id=profile_user_id, session_id=session_id, character_pack_id=character_pack_id)
    public_scope = {"profileUserId": profile_user_id, "sessionId": session_id, "characterPackId": character_pack_id}
    try:
        client = engine._resolve_client_protocol_context({"client_mode": "desktop_pet"})
        selection = engine._resolve_capability_selection(client_context=client, **scope)
        catalog = selection.capability_catalog
        if catalog is None:
            raise ValueError("catalog_unavailable")
        preferences = dict(selection.exposure_preferences)
    except Exception:
        return {"ok": False, "status": "unavailable", "reason": "tool_exposure_catalog_unavailable",
                "scope": public_scope, "preferences": preferences, "tools": []}
    backend = str(getattr(config_module or config, "MEMORY_BACKEND", "legacy")).lower()
    manager = getattr(engine, "memcore_manager", None)
    supported = backend == "memcore" and bool(getattr(manager, "available", False))
    baseline = None
    status = "immediate" if backend != "memcore" else "unavailable"
    reason = "memcore_authority_required" if backend != "memcore" else "memcore_projection_unavailable"
    generation = None
    if supported:
        try:
            projection = manager.build_context_projection(provider_profile="openai", **scope)
            if not projection.get("ok") or "compaction_generation" not in projection:
                raise ValueError("projection_unavailable")
            generation = int(projection["compaction_generation"])
            published = manager.resolve_tool_exposure_snapshot(snapshot={},
                scope=native_exposure_scope(catalog.scope, selection.exposure_preferences),
                compaction_generation=generation, create=False, **scope)
            if not published.get("ok"):
                raise ValueError("snapshot_unavailable")
            baseline = published["snapshot"]
            status, reason = published["status"], ""
        except Exception:
            supported = False
            reason = "memcore_projection_unavailable"
    preference_pending = supported and baseline is None
    contracts = baseline.get("contracts", {}) if baseline is not None else {}
    ids = set(catalog.capability_ids) | (set(contracts) - {"capability_list", "capability_load", "capability_invoke", "capability_search"})
    rows = []
    for name in sorted(ids):
        handler = catalog.handler(name)
        snapshot = catalog.snapshot(name)
        spec = (snapshot or contracts.get(name) or {}).get("spec", {})
        adapter = getattr(handler, "adapter", None)
        server = str(getattr(adapter, "server_id", "") or "")
        provider = str(getattr(adapter, "provider_id", "") or "")
        plugin = str(getattr(handler, "plugin_id", "") or "")
        source = f"MCP · {server}" if server else (f"插件 · {plugin or provider}" if plugin or provider else "宿主工具")
        target = selection.exposure_modes.get(name, preferences["toolModes"].get(name, preferences["defaultMode"]))
        current = ("resident" if name in contracts else "on_demand") if baseline is not None else (
            target if backend != "memcore" else "not_published")
        contract_pending = baseline is not None and name in contracts and (
            snapshot is None or snapshot["contract_ref"] != contracts[name]["contract_ref"])
        rows.append({"id": name, "name": spec.get("display_name") or name,
            "description": spec.get("description", ""), "source": source, "targetMode": target,
            "currentMode": current, "pending": preference_pending or (baseline is not None and (target != current or contract_pending)),
            "pendingReason": "next_request" if preference_pending else ("compaction" if baseline is not None and (target != current or contract_pending) else ""),
            "contractPending": contract_pending,
            "executionAvailable": snapshot is not None,
            "nativeContractValid": bool(snapshot and name in contracts and
                snapshot["contract_ref"] == contracts[name]["contract_ref"]) if baseline is not None else None})
    return {"ok": True, "status": status, "reason": reason, "scope": public_scope,
        "preferences": preferences, "tools": rows, "backend": backend, "boundarySupported": supported,
        "compactionGeneration": generation,
        "preferencePending": preference_pending,
        "searchPending": preference_pending or (baseline is not None and preferences["searchEnabled"] != ("capability_search" in contracts)),
        "currentSearchEnabled": ("capability_search" in contracts) if baseline is not None else None}


def save_exposure_state(engine, *, payload, **kwargs):
    result = save_preferences(base_dir=kwargs["base_dir"], profile_user_id=kwargs["profile_user_id"], payload=payload)
    if result.get("ok"):
        result["state"] = read_exposure_state(engine, **kwargs)
    return result
