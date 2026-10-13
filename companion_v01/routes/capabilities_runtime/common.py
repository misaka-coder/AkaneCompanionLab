"""Capability HTTP common responsibilities."""
from __future__ import annotations
import asyncio
import time
from pathlib import Path
from typing import Any, Callable
from fastapi import Request
from .definitions import LogEvent


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

def _safe_bool_query(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "include", "resolved"}

def _safe_positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(maximum, number))

async def _maybe_thread(callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    value = await asyncio.to_thread(callback)
    if hasattr(value, "__await__"):
        return await value
    return value

def _resolve_identity(
    request: Request,
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> tuple[str, str]:
    if resolve_identity_from_query is not None:
        return resolve_identity_from_query(request)
    session_id = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "default_session")
    profile_user_id = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or session_id)
    return session_id, profile_user_id

def _resolve_character_voice_preference(engine: Any, request: Request) -> dict[str, Any]:
    pack_id = str(
        request.query_params.get("character_pack_id")
        or request.query_params.get("characterPackId")
        or request.query_params.get("character_pack")
        or ""
    ).strip()
    if not pack_id:
        return {}
    service = getattr(engine, "desktop_pet_character_resources", None)
    builder = getattr(service, "build_character_voice_preference", None)
    if not callable(builder):
        return {}
    try:
        result = builder(pack_id)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}

def _resolve_identity_with_payload(
    request: Request,
    payload: dict[str, Any],
    resolve_identity_from_query: Callable[[Request], tuple[str, str]] | None,
) -> tuple[str, str]:
    base_session_id, base_profile_user_id = _resolve_identity(request, resolve_identity_from_query)
    query_session = str(request.query_params.get("user_id") or request.query_params.get("session_id") or "").strip()
    query_profile = str(request.query_params.get("real_user_id") or request.query_params.get("profileUserId") or "").strip()
    body = payload if isinstance(payload, dict) else {}
    body_session = str(body.get("user_id") or body.get("session_id") or "").strip()
    body_profile = str(
        body.get("real_user_id")
        or body.get("profileUserId")
        or body.get("profile_user_id")
        or ""
    ).strip()
    session_id = str(query_session or body_session or base_session_id or "default_session").strip() or "default_session"
    profile_user_id = (
        str(query_profile or body_profile or base_profile_user_id or session_id)
        .strip()
        or session_id
    )
    return session_id, profile_user_id

def _resolve_provider_config_base_dir(
    *,
    capability_config_base_dir: str | Path | None,
    config_module: Any = None,
) -> Path | None:
    if capability_config_base_dir is not None:
        return Path(capability_config_base_dir)
    data_dir = getattr(config_module, "DATA_DIR", None)
    if data_dir:
        return Path(data_dir)
    return None

def _observe_request(runtime_metrics: Any, name: str, started_at: float, ok: bool) -> None:
    if runtime_metrics is None:
        return
    try:
        runtime_metrics.observe_request(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
    except Exception:
        pass

def _log_best_effort(log_event: LogEvent | None, event: str, **fields: Any) -> None:
    if log_event is None:
        return
    try:
        log_event(event, **fields)
    except Exception:
        pass
