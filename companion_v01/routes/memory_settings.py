"""Owner-only settings UI; no dialogue reads, writes, or QQ command delivery."""
from __future__ import annotations

import asyncio
import os
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..settings_catalog import MEMORY_RESTART_KEYS
from ..settings_overrides import coerce_value
from ..store import normalize_character_pack_id

_GROUP = re.compile(r"[1-9][0-9]{0,19}\Z")


def build_memory_settings_router(*, runtime):
    router = APIRouter()
    gateway = runtime.qq_gateway
    engine = runtime.engine
    group_names = {}
    group_names_at = 0.0
    group_names_attempt_at = 0.0
    names_lock = asyncio.Lock()

    def response(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    def authorize(request):
        auth = runtime.admin_write_auth.authorize(request)
        return None if auth.ok else response({"ok": False, "reason": auth.reason}, auth.status_code)

    async def known_groups(character):
        nonlocal group_names, group_names_at, group_names_attempt_at
        names_available = False
        if gateway is not None:
            async with names_lock:
                if time.monotonic() - group_names_at >= 60 and time.monotonic() - group_names_attempt_at >= 10:
                    group_names_attempt_at = time.monotonic()
                    try:
                        result = await asyncio.to_thread(gateway._onebot_transport.call, "get_group_list", timeout=3)
                        items = result.data.get("items") if isinstance(result.data, dict) else None
                        if result.ok and isinstance(items, list):
                            group_names = {str(row.get("group_id")): str(row.get("group_name") or "")[:160]
                                           for row in items if isinstance(row, dict)
                                           and _GROUP.fullmatch(str(row.get("group_id") or ""))}
                            group_names_at = time.monotonic()
                    except Exception:
                        pass  # Persisted consent and trusted history remain usable offline.
                names_available = time.monotonic() - group_names_at < 60
        sessions = {}
        after = ""
        # Indexed metadata only, bounded pages; never scan or render dialogue bodies.
        for _ in range(100):
            rows = await asyncio.to_thread(engine.store.list_trusted_qq_group_sessions,
                                           character_pack_id=character, after_session_id=after, limit=100)
            for row in rows:
                group = row["session_id"].removeprefix("qq_group_shared_")
                if _GROUP.fullmatch(group):
                    sessions[group] = True
            if len(rows) < 100:
                break
            after = rows[-1]["session_id"]
        preferences = gateway._preferences if gateway is not None else None
        receivers = preferences.private_memory_receivers() if preferences is not None else {}
        groups = set(sessions) | set(group_names) | set(receivers)
        return [{"groupId": group, "name": group_names.get(group, ""),
                 "enabled": preferences.private_memory_policy(group)[0] if preferences is not None else False,
                 "hasCharacterMemory": group in sessions}
                for group in sorted(groups, key=int)], names_available

    @router.get("/control-center/memory-settings")
    async def read(request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        character = normalize_character_pack_id(request.query_params.get("character_pack_id")
                                               or getattr(runtime.config_module, "QQ_CHARACTER_PACK_ID", ""))
        try:
            saved = await asyncio.to_thread(runtime.settings_override_store.load)
            # Validate persisted values with the same startup policy, without hiding read failures.
            overrides = {key: coerce_value(key, saved[key]) for key in MEMORY_RESTART_KEYS if key in saved}
            settings = [{"key": key, "current": bool(getattr(runtime.config_module, key, False)),
                         "desired": overrides.get(key, bool(getattr(runtime.config_module, key, False))),
                         "pendingRestart": overrides.get(key, getattr(runtime.config_module, key, False))
                         != getattr(runtime.config_module, key, False)} for key in sorted(MEMORY_RESTART_KEYS)]
            groups, names_available = await known_groups(character)
        except Exception:
            return response({"ok": False, "reason": "memory_settings_read_failed"}, 503)
        policy = getattr(engine, "owner_private_policy", None)
        mirror = getattr(engine, "owner_private_group_memory", None)
        manager = getattr(engine, "memcore_manager", None)
        return response({"ok": True, "status": "available", "botId": runtime.bot_id,
                         "characterPackId": character, "processId": os.getpid(), "settings": settings,
                         "groups": groups, "groupNamesAvailable": names_available,
                         "qqAvailable": gateway is not None,
                         "privateMemoryReady": bool(policy and policy.enabled and policy.master_qq
                                                    and manager and manager.available and manager.backend == "memcore"),
                         "groupMemoryRunning": bool(mirror and mirror.enabled)})

    @router.post("/control-center/memory-settings/groups/{group_id}")
    async def save_group(group_id: str, request: Request):
        denied = authorize(request)
        if denied is not None:
            return denied
        if gateway is None:
            return response({"ok": False, "reason": "qq_not_configured"}, 503)
        try:
            payload = await request.json()
        except Exception:
            payload = None
        if not _GROUP.fullmatch(group_id) or not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            return response({"ok": False, "reason": "invalid_group_setting"}, 400)
        character = normalize_character_pack_id(payload.get("characterPackId")
                                               or getattr(runtime.config_module, "QQ_CHARACTER_PACK_ID", ""))
        try:
            groups, _ = await known_groups(character)
        except Exception:
            return response({"ok": False, "reason": "memory_settings_read_failed"}, 503)
        if not any(group["groupId"] == group_id for group in groups):
            return response({"ok": False, "reason": "unknown_qq_group"}, 404)
        try:
            ok = await asyncio.to_thread(gateway._preferences.set_private_memory_enabled, group_id, payload["enabled"])
        except Exception:
            ok = False
        if not ok:
            return response({"ok": False, "reason": "private_memory_preference_save_failed"}, 500)
        enabled, generation = gateway._preferences.private_memory_policy(group_id)
        return response({"ok": True, "status": "applied", "groupId": group_id,
                         "enabled": enabled, "generation": generation})

    return router
