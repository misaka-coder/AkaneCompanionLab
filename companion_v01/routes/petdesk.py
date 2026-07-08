from __future__ import annotations

import asyncio
import copy
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..petdesk_bridge import (
    add_petdesk_audio_resource,
    attach_petdesk_tts_audio,
    build_akane_turn_payload,
    build_petdesk_display_envelope,
    build_petdesk_health_payload,
    build_petdesk_resource_bundle,
    clean_text,
    serialize_petdesk_sse,
)
from .voice import (
    EDGE_TTS_PROVIDER_ID,
    GPT_SOVITS_PROVIDER_ID,
    _invoke_tts_adapter,
    _resolve_provider_config_base_dir,
    _resolve_tts_runtime_provider,
    _safe_tts_reason,
)


LogEvent = Callable[..., None]


def build_petdesk_router(
    *,
    engine: Any,
    config_module: Any = None,
    tts_client: Any = None,
    character_resources: Any = None,
    runtime_metrics: Any = None,
    public_guard: Any = None,
    log_event: LogEvent | None = None,
    capability_config_base_dir: str | None = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> APIRouter:
    # Transitional bridge for petdesk-runtime; see docs/petdesk_akane_bridge_m32.md before extending it.
    router = APIRouter()
    provider_config_base_dir = _resolve_provider_config_base_dir(
        capability_config_base_dir=capability_config_base_dir,
        config_module=config_module,
    )
    audio_registry = _PetdeskAudioRegistry(
        ttl_seconds=_coerce_int_config(
            getattr(config_module, "PETDESK_AUDIO_TTL_SECONDS", None),
            default=600,
            minimum=30,
            maximum=3600,
        ),
        max_items=_coerce_int_config(
            getattr(config_module, "PETDESK_AUDIO_MAX_ITEMS", None),
            default=80,
            minimum=1,
            maximum=500,
        ),
    )

    def _character_resources() -> Any:
        return (
            character_resources
            if character_resources is not None
            else getattr(engine, "desktop_pet_character_resources", None)
        )

    @router.get("/pet/health")
    async def pet_health(request: Request) -> JSONResponse:
        character_pack_id = _character_pack_id_from_request(request)
        payload = build_petdesk_health_payload(_character_resources(), character_pack_id)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/pet/snapshot")
    async def pet_snapshot(request: Request) -> JSONResponse:
        character_pack_id = _character_pack_id_from_request(request)
        bundle = build_petdesk_resource_bundle(_character_resources(), character_pack_id)
        manifest = _manifest_for_bundle(bundle.character_pack_id)
        envelope = build_petdesk_display_envelope(
            {},
            bundle=bundle,
            resource_manifest=manifest,
            fallback_speech="我在，petdesk runtime 已连接。",
        )
        return JSONResponse(envelope, headers={"Cache-Control": "no-store"})

    @router.get("/pet/resource-manifest")
    async def pet_resource_manifest(request: Request) -> JSONResponse:
        character_pack_id = _character_pack_id_from_request(request)
        bundle = build_petdesk_resource_bundle(_character_resources(), character_pack_id)
        return JSONResponse(bundle.runtime_manifest, headers={"Cache-Control": "no-store"})

    @router.get("/audio/petdesk/{audio_token}")
    async def petdesk_audio_content(audio_token: str) -> Response:
        item = audio_registry.get(audio_token)
        if item is None:
            return JSONResponse(
                {"ok": False, "status": "error", "reason": "petdesk_audio_not_found"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        return Response(
            content=item.audio,
            media_type=item.media_type,
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/pet/turn")
    async def pet_turn(request: Request):
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception:
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "invalid_json",
                    "message": "/pet/turn payload must be valid JSON.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not isinstance(payload, dict):
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "invalid_payload",
                    "message": "/pet/turn payload must be a JSON object.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        if not clean_text(payload.get("text")):
            _observe("pet_turn", started_at=started_at, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": "empty_text",
                    "message": "/pet/turn text is required.",
                },
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )

        guard_decision = _try_acquire_guard()
        if not bool(getattr(guard_decision, "allowed", True)):
            _observe("pet_turn", started_at=started_at, ok=False)
            reason = clean_text(getattr(guard_decision, "reason", "")) or "busy"
            message = clean_text(getattr(guard_decision, "message", "")) or "当前体验人数较多，请稍后再试。"
            _log("petdesk_guard_blocked", reason=reason)
            return JSONResponse(
                {
                    "ok": False,
                    "status": "error",
                    "reason": reason,
                    "message": message,
                },
                status_code=429,
                headers={"Cache-Control": "no-store"},
            )

        query_params = dict(request.query_params)
        akane_payload = build_akane_turn_payload(
            payload,
            query_params=query_params,
            character_resources=_character_resources(),
        )
        bundle = build_petdesk_resource_bundle(_character_resources(), akane_payload.get("character_pack_id"))
        manifest = _manifest_for_bundle(bundle.character_pack_id)
        runtime_manifest = copy.deepcopy(bundle.runtime_manifest)
        turn_id = clean_text(payload.get("turnId"))

        async def _stream():
            ok = False
            try:
                yield serialize_petdesk_sse("resource_manifest", runtime_manifest)
                frame = await asyncio.to_thread(engine.process_turn, akane_payload)
                envelope = build_petdesk_display_envelope(
                    frame if isinstance(frame, dict) else {},
                    bundle=bundle,
                    resource_manifest=manifest,
                    turn_id=turn_id,
                    fallback_speech="我这边暂时没有可以显示的回复。",
                )
                yield serialize_petdesk_sse("display", envelope)
                ok = True
                audio_result = await _try_synthesize_petdesk_tts(
                    envelope=envelope,
                    akane_payload=akane_payload,
                )
                if audio_result is not None:
                    audio_handle, audio_url = audio_registry.put(
                        audio_result.audio,
                        media_type=audio_result.media_type,
                    )
                    audio_manifest = copy.deepcopy(runtime_manifest)
                    if add_petdesk_audio_resource(
                        audio_manifest,
                        audio_handle=audio_handle,
                        url=audio_url,
                    ) and attach_petdesk_tts_audio(envelope, audio_handle=audio_handle):
                        runtime_manifest.update(audio_manifest)
                        yield serialize_petdesk_sse("resource_manifest", runtime_manifest)
                        yield serialize_petdesk_sse("display", envelope)
                _log(
                    "petdesk_turn_complete",
                    session_id=str(akane_payload.get("user_id") or ""),
                    character_pack_id=str(akane_payload.get("character_pack_id") or ""),
                )
            except Exception:
                yield serialize_petdesk_sse(
                    "error",
                    {
                        "reason": "akane_turn_failed",
                        "payload": {"retryable": True},
                    },
                )
                _log("petdesk_turn_error", session_id=str(akane_payload.get("user_id") or ""))
            finally:
                _release_guard(guard_decision)
                _observe("pet_turn", started_at=started_at, ok=ok)
                yield "event: done\ndata: {}\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    async def _try_synthesize_petdesk_tts(
        *,
        envelope: dict[str, Any],
        akane_payload: dict[str, Any],
    ) -> _PetdeskSynthesizedAudio | None:
        text = clean_text(envelope.get("speech"))
        if not text:
            return None
        started_at = time.perf_counter()
        payload = {
            "text": text,
            "real_user_id": clean_text(akane_payload.get("real_user_id")),
            "profile_user_id": clean_text(akane_payload.get("real_user_id")),
            "user_id": clean_text(akane_payload.get("user_id")),
            "session_id": clean_text(akane_payload.get("user_id")),
            "client_mode": "desktop_pet",
            "character_pack_id": clean_text(akane_payload.get("character_pack_id")),
        }
        visual = envelope.get("visual") if isinstance(envelope.get("visual"), dict) else {}
        emotion = clean_text(visual.get("emotion"))
        if emotion:
            payload["emotion"] = emotion

        resolution = _resolve_tts_runtime_provider(
            engine=engine,
            payload=payload,
            base_dir=provider_config_base_dir,
            config_module=config_module,
            edge_tts_available=tts_client is not None,
            gpt_sovits_client_factory=gpt_sovits_client_factory,
        )

        if resolution.get("activeProviderId") == GPT_SOVITS_PROVIDER_ID:
            try:
                audio, media_type = await _invoke_tts_adapter(
                    provider_id=GPT_SOVITS_PROVIDER_ID,
                    client=resolution["client"],
                    text=text,
                    payload=payload,
                    resolution=resolution,
                    default_media_type="audio/wav",
                )
                _observe("pet_tts", started_at=started_at, ok=True)
                return _PetdeskSynthesizedAudio(audio=audio, media_type=media_type)
            except Exception as exc:
                resolution = {
                    **resolution,
                    "status": "degraded",
                    "activeProviderId": EDGE_TTS_PROVIDER_ID if tts_client is not None else "",
                    "fallbackProviderId": EDGE_TTS_PROVIDER_ID if tts_client is not None else "",
                    "reason": "gpt_sovits_failed",
                }
                _log(
                    "petdesk_tts_provider_fallback",
                    provider=GPT_SOVITS_PROVIDER_ID,
                    fallbackProviderId=resolution.get("fallbackProviderId"),
                    reason="gpt_sovits_failed",
                    errorType=_safe_tts_reason(type(exc).__name__),
                    text_length=len(text),
                )

        if tts_client is None:
            _observe("pet_tts", started_at=started_at, ok=False)
            _log("petdesk_tts_skipped", reason=str(resolution.get("reason") or "tts_client_unavailable"))
            return None

        try:
            audio, media_type = await _invoke_tts_adapter(
                provider_id=EDGE_TTS_PROVIDER_ID,
                client=tts_client,
                text=text,
                payload=payload,
                resolution=resolution,
                default_media_type="audio/mpeg",
            )
        except Exception as exc:
            _observe("pet_tts", started_at=started_at, ok=False)
            _log(
                "petdesk_tts_failed",
                reason=_safe_tts_reason(type(exc).__name__),
                text_length=len(text),
            )
            return None

        _observe("pet_tts", started_at=started_at, ok=True)
        return _PetdeskSynthesizedAudio(audio=audio, media_type=media_type)

    def _manifest_for_bundle(character_pack_id: str) -> Any:
        resources = _character_resources()
        getter = getattr(resources, "get_manifest", None)
        if not callable(getter) or not character_pack_id:
            return None
        try:
            return getter(character_pack_id)
        except Exception:
            return None

    def _try_acquire_guard() -> Any:
        if public_guard is None or not hasattr(public_guard, "try_acquire"):
            return _AllowedGuardDecision()
        try:
            return public_guard.try_acquire()
        except Exception:
            return _AllowedGuardDecision()

    def _release_guard(decision: Any) -> None:
        if not bool(getattr(decision, "acquired", False)):
            return
        if public_guard is None or not hasattr(public_guard, "release"):
            return
        try:
            public_guard.release()
        except Exception:
            return

    def _observe(name: str, *, started_at: float, ok: bool) -> None:
        observer = getattr(runtime_metrics, "observe_request", None)
        if not callable(observer):
            return
        try:
            observer(name, duration_ms=(time.perf_counter() - started_at) * 1000, ok=ok)
        except Exception:
            return

    def _log(event: str, **fields: object) -> None:
        if log_event is None:
            return
        try:
            log_event(event, **fields)
        except Exception:
            return

    return router


class _AllowedGuardDecision:
    allowed = True
    acquired = False
    reason = ""
    message = ""


@dataclass(frozen=True)
class _PetdeskSynthesizedAudio:
    audio: bytes
    media_type: str


@dataclass(frozen=True)
class _PetdeskAudioItem:
    audio: bytes
    media_type: str
    created_at: float
    expires_at: float


class _PetdeskAudioRegistry:
    def __init__(self, *, ttl_seconds: int, max_items: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._items: dict[str, _PetdeskAudioItem] = {}
        self._lock = threading.Lock()

    def put(self, audio: bytes, *, media_type: str) -> tuple[str, str]:
        now = time.time()
        token = secrets.token_hex(16)
        item = _PetdeskAudioItem(
            audio=bytes(audio),
            media_type=_safe_audio_media_type(media_type),
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._prune_locked(now)
            self._items[token] = item
            self._prune_locked(now)
        return f"akane/tts/{token}", f"/audio/petdesk/{token}"

    def get(self, token: str) -> _PetdeskAudioItem | None:
        if not _safe_audio_token(token):
            return None
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            return self._items.get(token)

    def _prune_locked(self, now: float) -> None:
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._items.pop(token, None)
        while len(self._items) > self._max_items:
            oldest = min(self._items, key=lambda token: self._items[token].created_at)
            self._items.pop(oldest, None)


def _character_pack_id_from_request(request: Request) -> str:
    return (
        clean_text(request.query_params.get("character_pack_id"))
        or clean_text(request.query_params.get("characterPackId"))
        or clean_text(request.query_params.get("packId"))
    )


def _coerce_int_config(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(value))
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _safe_audio_token(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and len(text) == 32 and all(ch in "0123456789abcdef" for ch in text)


def _safe_audio_media_type(value: Any) -> str:
    text = str(value or "").split(";", 1)[0].strip().lower()
    if "/" not in text or any(ch in text for ch in "\r\n"):
        return "audio/mpeg"
    if not text.startswith("audio/"):
        return "audio/mpeg"
    return text[:80]
