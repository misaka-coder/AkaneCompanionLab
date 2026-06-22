from __future__ import annotations

import asyncio
import re
import uuid
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .voice import (
    GPT_SOVITS_PROVIDER_ID,
    _coerce_synthesized_audio,
    _resolve_tts_runtime_provider,
)


LogEvent = Callable[..., None]

QQ_REPLY_OBJECT_TERMS = ("工作台", "文件", "结果", "成果", "产物", "音频", "视频", "人声", "伴奏", "任务")
QQ_REPLY_ACTION_TERMS = (
    "清理",
    "清空",
    "清除",
    "收拾",
    "干净",
    "删除",
    "归档",
    "发",
    "发送",
    "转",
    "转换",
    "转成",
    "分离",
    "拆",
    "完成",
    "做好",
)


def _normalize_reply_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split()).strip()


def _reply_similarity(left: str, right: str) -> float:
    left_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(left or "").strip().lower())
    right_text = re.sub(r"[\s，。！？!?~～、,.]+", "", str(right or "").strip().lower())
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    if len(left_text) < 4 or len(right_text) < 4:
        return 0.0
    left_grams = {left_text[index : index + 2] for index in range(len(left_text) - 1)}
    right_grams = {right_text[index : index + 2] for index in range(len(right_text) - 1)}
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _is_similar_reply(left: str, right: str) -> bool:
    if _reply_similarity(left, right) >= 0.42:
        return True
    left_text = str(left or "")
    right_text = str(right or "")
    shared_objects = [term for term in QQ_REPLY_OBJECT_TERMS if term in left_text and term in right_text]
    shared_actions = [term for term in QQ_REPLY_ACTION_TERMS if term in left_text and term in right_text]
    return bool(shared_objects and shared_actions)


def _filter_unsent_reply_messages(messages: list[str], sent_messages: list[str]) -> list[str]:
    sent_normalized = {_normalize_reply_text(item) for item in sent_messages if _normalize_reply_text(item)}
    sent_joined = "".join(str(item or "").strip() for item in sent_messages if str(item or "").strip()).strip()
    sent_joined_normalized = _normalize_reply_text(sent_joined)
    unsent: list[str] = []
    for message in messages:
        text = str(message or "").strip()
        if not text:
            continue
        normalized = _normalize_reply_text(text)
        if normalized and normalized in sent_normalized:
            continue
        if sent_joined and text.startswith(sent_joined):
            text = text[len(sent_joined) :].strip()
            normalized = _normalize_reply_text(text)
            if not normalized:
                continue
        elif sent_joined_normalized and normalized == sent_joined_normalized:
            continue
        trimmed_by_sent_prefix = False
        for sent_item in sent_messages:
            sent_text = str(sent_item or "").strip()
            if sent_text and text.startswith(sent_text):
                text = text[len(sent_text) :].strip()
                normalized = _normalize_reply_text(text)
                trimmed_by_sent_prefix = True
                break
        if trimmed_by_sent_prefix:
            if not normalized:
                continue
            generic_tail = re.sub(r"[\s，。！？!?~～、,.]+", "", text)
            if len(generic_tail) < 10:
                continue
        if any(_is_similar_reply(text, sent_item) for sent_item in sent_messages):
            continue
        unsent.append(text)
    return unsent


def _send_pending_stage_messages(
    *,
    qq_gateway: Any,
    context: Any,
    pending_messages: list[str],
    streamed_messages: list[str],
    stream_send_results: list[dict[str, Any]],
    max_streamed: int,
) -> list[str]:
    for text in pending_messages:
        if len(streamed_messages) >= max_streamed:
            break
        normalized = _normalize_reply_text(text)
        if not normalized or normalized in {_normalize_reply_text(item) for item in streamed_messages}:
            continue
        if any(_is_similar_reply(text, sent_item) for sent_item in streamed_messages):
            continue
        result = qq_gateway.send_reply(context, text[:1800].strip())
        streamed_messages.append(text)
        stream_send_results.append(result)
    return []


def _normalize_reply_medium(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "text": "text",
        "文字": "text",
        "文本": "text",
        "voice": "voice",
        "audio": "voice",
        "record": "voice",
        "语音": "voice",
        "both": "both",
        "all": "both",
        "text_voice": "both",
        "voice_text": "both",
        "文字语音": "both",
        "双发": "both",
        "auto": "auto",
        "自动": "auto",
    }
    return aliases.get(text, "")


def _streaming_allows_text(reply_mode: str, delivery_hint: str) -> bool:
    mode = _normalize_reply_medium(reply_mode) or "auto"
    hint = _normalize_reply_medium(delivery_hint)
    if mode == "auto":
        return hint in {"text", "both"}
    return mode in {"text", "both"}


def _frame_reply_medium(frame: dict[str, Any], *, delivery_hint: str = "") -> str:
    return (
        _normalize_reply_medium(frame.get("reply_medium"))
        or _normalize_reply_medium(delivery_hint)
    )


def _resolve_delivery_medium(
    *,
    context: Any,
    qq_gateway: Any,
    frame: dict[str, Any],
    delivery_hint: str = "",
) -> dict[str, str]:
    reply_mode = _normalize_reply_medium(getattr(context, "reply_mode", "")) or _normalize_reply_medium(
        qq_gateway.resolve_reply_mode(getattr(context, "session_id", ""))
    ) or "auto"
    model_medium = _frame_reply_medium(frame, delivery_hint=delivery_hint) or "text"
    medium = model_medium if reply_mode == "auto" else reply_mode
    if medium not in {"text", "voice", "both"}:
        medium = "text"
    return {
        "reply_mode": reply_mode,
        "model_medium": model_medium,
        "medium": medium,
    }


def _media_type_extension(media_type: str) -> str:
    clean = str(media_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/flac": "flac",
        "audio/aac": "aac",
        "audio/mp4": "m4a",
    }.get(clean, "wav")


def _run_async_safely(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _resolve_qq_tts_profile_user_id(*, config_module: Any, context: Any) -> str:
    raw_value = str(getattr(config_module, "QQ_TTS_PROFILE_USER_ID", "") or "").strip()
    if not raw_value:
        raw_value = str(getattr(config_module, "WEB_OWNER_PROFILE_USER_ID", "") or "master").strip()
    if raw_value.lower() in {"conversation", "context", "current"}:
        raw_value = str(getattr(context, "profile_user_id", "") or "master").strip()
    if not raw_value or not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_value):
        return "master"
    return raw_value


def _synthesize_qq_voice_file(
    *,
    engine: Any,
    config_module: Any,
    tts_client: Any,
    text: str,
    context: Any,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"ok": False, "reason": "empty_voice_text"}

    data_dir = Path(str(getattr(config_module, "DATA_DIR", "users_data") or "users_data"))
    base_dir = data_dir
    tts_profile_user_id = _resolve_qq_tts_profile_user_id(config_module=config_module, context=context)
    payload = {
        "text": clean_text,
        "real_user_id": tts_profile_user_id,
        "profile_user_id": tts_profile_user_id,
        "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
    }
    resolution = _resolve_tts_runtime_provider(
        engine=engine,
        payload=payload,
        base_dir=base_dir,
        config_module=config_module,
        edge_tts_available=tts_client is not None,
        gpt_sovits_client_factory=gpt_sovits_client_factory,
    )

    active_provider = str(resolution.get("activeProviderId") or "")
    if active_provider == GPT_SOVITS_PROVIDER_ID:
        synthesize_kwargs: dict[str, Any] = {
            "voice_profile_id": str(resolution.get("voiceProfileId") or ""),
        }
        voice_profile = resolution.get("voiceProfile")
        if isinstance(voice_profile, dict) and voice_profile:
            synthesize_kwargs["profile"] = voice_profile
        result = _run_async_safely(resolution["client"].synthesize(clean_text, **synthesize_kwargs))
        audio, media_type = _coerce_synthesized_audio(result, default_media_type="audio/wav")
    elif tts_client is not None:
        result = _run_async_safely(tts_client.synthesize(clean_text))
        audio, media_type = _coerce_synthesized_audio(result, default_media_type="audio/mpeg")
    else:
        return {"ok": False, "reason": "tts_unavailable", "resolution": resolution}

    cache_dir = data_dir / "qq_voice_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = _media_type_extension(media_type)
    path = cache_dir / f"qq_reply_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.{ext}"
    path.write_bytes(audio)
    return {
        "ok": True,
        "path": str(path),
        "media_type": media_type,
        "provider": active_provider,
        "resolution_status": str(resolution.get("status") or ""),
        "tts_profile_user_id": tts_profile_user_id,
    }


def _send_qq_delivery(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    frame: dict[str, Any],
    reply_messages: list[str],
    unsent_reply_messages: list[str],
    streamed_messages: list[str],
    config_module: Any,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
    delivery_hint: str = "",
) -> dict[str, Any]:
    plan = _resolve_delivery_medium(
        context=context,
        qq_gateway=qq_gateway,
        frame=frame,
        delivery_hint=delivery_hint,
    )
    medium = plan["medium"]
    text_enabled = medium in {"text", "both"}
    voice_enabled = medium in {"voice", "both"}
    text_send_result = {"ok": True, "count": 0, "results": []}
    voice_send_result = {"ok": True, "count": 0, "results": []}

    if text_enabled:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)

    voice_reason = ""
    if voice_enabled:
        max_segments = max(1, min(10, int(getattr(config_module, "QQ_VOICE_MAX_SEGMENTS", 3) or 3)))
        voice_messages = [str(message or "").strip() for message in reply_messages if str(message or "").strip()]
        voice_text = "\n".join(voice_messages[:max_segments]).strip()
        max_auto_chars = max(20, min(1200, int(getattr(config_module, "QQ_VOICE_MAX_TEXT_CHARS", 280) or 280)))
        if plan["reply_mode"] == "auto" and len(voice_text) > max_auto_chars:
            voice_enabled = False
            voice_reason = "auto_voice_text_too_long"
            if not text_enabled and not streamed_messages and unsent_reply_messages:
                text_enabled = True
                text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)
                text_send_result["fallback_from_voice"] = True
        elif voice_text:
            try:
                voice_file = _synthesize_qq_voice_file(
                    engine=engine,
                    config_module=config_module,
                    tts_client=tts_client,
                    text=voice_text,
                    context=context,
                    gpt_sovits_client_factory=gpt_sovits_client_factory,
                )
            except Exception as exc:
                voice_file = {"ok": False, "reason": str(exc)[:200]}
            if voice_file.get("ok"):
                result = qq_gateway.send_voice(
                    context,
                    audio_path=str(voice_file.get("path") or ""),
                    name="akane_reply",
                )
                result["provider"] = str(voice_file.get("provider") or "")
                result["media_type"] = str(voice_file.get("media_type") or "")
                result["tts_profile_user_id"] = str(voice_file.get("tts_profile_user_id") or "")
                voice_send_result = {
                    "ok": bool(result.get("ok")),
                    "count": 1,
                    "results": [result],
                }
                voice_reason = "" if result.get("ok") else str(result.get("reason") or "voice_send_failed")
            else:
                voice_send_result = {
                    "ok": False,
                    "count": 0,
                    "reason": str(voice_file.get("reason") or "voice_synthesis_failed"),
                    "results": [],
                }
                voice_reason = str(voice_file.get("reason") or "voice_synthesis_failed")

    needs_text_fallback = (
        voice_enabled
        and not bool(voice_send_result.get("ok"))
        and not text_enabled
        and not streamed_messages
        and unsent_reply_messages
    )
    if needs_text_fallback:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)
        text_send_result["fallback_from_voice"] = True

    combined_results = [
        *list(text_send_result.get("results") or []),
        *list(voice_send_result.get("results") or []),
    ]
    ok_parts = [bool(text_send_result.get("ok"))]
    if voice_enabled:
        ok_parts.append(bool(voice_send_result.get("ok")) or needs_text_fallback)
    return {
        "ok": all(ok_parts),
        "count": len(combined_results),
        "results": combined_results,
        "delivery": {
            **plan,
            "text_enabled": bool(text_enabled),
            "voice_enabled": bool(voice_enabled),
            "voice_reason": voice_reason,
        },
        "text_result": text_send_result,
        "voice_result": voice_send_result,
    }


def _load_qq_delivery_config(engine: Any, context: Any) -> dict[str, Any]:
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    if not character_pack_id:
        return {}
    service = getattr(engine, "desktop_pet_character_resources", None)
    loader = getattr(service, "load_qq_delivery_config", None)
    if not callable(loader):
        return {}
    try:
        value = loader(character_pack_id)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _send_qq_emotion_image_fallback(
    *,
    engine: Any,
    qq_gateway: NapCatQQGateway,
    context: Any,
    frame: dict[str, Any],
    qq_delivery_config: dict[str, Any],
) -> dict[str, Any]:
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    if not character_pack_id:
        return {"ok": True, "status": "skipped", "reason": "empty_character_pack_id"}
    image_config = (
        qq_delivery_config.get("emotion_images")
        if isinstance(qq_delivery_config.get("emotion_images"), dict)
        else {}
    )
    if image_config.get("enabled") is False:
        return {"ok": True, "status": "skipped", "reason": "disabled"}
    service = getattr(engine, "desktop_pet_character_resources", None)
    resolver = getattr(service, "resolve_emotion_image_file", None)
    if not callable(resolver):
        return {"ok": True, "status": "skipped", "reason": "missing_character_resource_service"}
    emotion = str((frame or {}).get("emotion") or "").strip()
    try:
        image = resolver(character_pack_id, emotion)
    except Exception:
        image = {}
    if not isinstance(image, dict) or not image.get("path"):
        return {"ok": True, "status": "skipped", "reason": "missing_emotion_image", "emotion": emotion}
    try:
        min_interval = int(image_config.get("min_interval_seconds") or image_config.get("cooldown_seconds") or 20)
    except (TypeError, ValueError):
        min_interval = 20
    return qq_gateway.send_emotion_image(
        context,
        frame,
        image=image,
        min_interval_seconds=min_interval,
    )


def _process_qq_turn_streaming(
    *,
    engine: Any,
    qq_gateway: Any,
    context: Any,
    turn_payload: dict[str, Any],
    config_module: Any,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    pending_stage_messages: list[str] = []
    streamed_messages: list[str] = []
    stream_send_results: list[dict[str, Any]] = []
    frame: dict[str, Any] = {}
    delivery_hint = ""
    active_reply_mode = _normalize_reply_medium(getattr(context, "reply_mode", "")) or _normalize_reply_medium(
        qq_gateway.resolve_reply_mode(getattr(context, "session_id", ""))
    ) or "auto"
    max_streamed = max(0, min(20, int(getattr(config_module, "QQ_STREAM_MAX_SEGMENTS", getattr(config_module, "QQ_REPLY_MAX_SEGMENTS", 8)) or 0)))
    stream_enabled = bool(getattr(config_module, "QQ_STREAM_REPLIES_ENABLED", True)) and max_streamed > 0

    for stream_event in engine.process_turn_stream(turn_payload):
        if not isinstance(stream_event, dict):
            continue
        event_type = str(stream_event.get("type") or "").strip()
        if event_type == "delivery_hint":
            delivery_hint = _normalize_reply_medium(stream_event.get("medium")) or delivery_hint
            continue
        if event_type == "speech_segment" and stream_enabled:
            text = str(stream_event.get("text") or "").strip()
            if not text:
                continue
            normalized = _normalize_reply_text(text)
            if not normalized or normalized in {_normalize_reply_text(item) for item in pending_stage_messages}:
                continue
            pending_stage_messages.append(text)
            continue
        if (
            event_type == "assistant_stage_decision"
            and pending_stage_messages
            and _streaming_allows_text(active_reply_mode, delivery_hint)
        ):
            pending_stage_messages = _send_pending_stage_messages(
                qq_gateway=qq_gateway,
                context=context,
                pending_messages=pending_stage_messages,
                streamed_messages=streamed_messages,
                stream_send_results=stream_send_results,
                max_streamed=max_streamed,
            )
            continue
        if event_type == "final_ui" and isinstance(stream_event.get("payload"), dict):
            frame = dict(stream_event.get("payload") or {})

    if (
        stream_enabled
        and pending_stage_messages
        and not frame
        and _streaming_allows_text(active_reply_mode, delivery_hint)
    ):
        pending_stage_messages = _send_pending_stage_messages(
            qq_gateway=qq_gateway,
            context=context,
            pending_messages=pending_stage_messages,
            streamed_messages=streamed_messages,
            stream_send_results=stream_send_results,
            max_streamed=max_streamed,
        )

    if not frame and not streamed_messages:
        frame = engine.process_turn(turn_payload)

    reply_messages = qq_gateway.render_reply_messages(frame)
    unsent_reply_messages = _filter_unsent_reply_messages(reply_messages, streamed_messages)
    send_result = _send_qq_delivery(
        engine=engine,
        qq_gateway=qq_gateway,
        context=context,
        frame=frame,
        reply_messages=reply_messages,
        unsent_reply_messages=unsent_reply_messages,
        streamed_messages=streamed_messages,
        config_module=config_module,
        tts_client=tts_client,
        gpt_sovits_client_factory=gpt_sovits_client_factory,
        delivery_hint=delivery_hint,
    )
    if streamed_messages:
        combined_results = [*stream_send_results, *list(send_result.get("results") or [])]
        send_result = {
            "ok": all(bool(item.get("ok")) for item in combined_results) if combined_results else True,
            "count": len(combined_results),
            "streamed_count": len(streamed_messages),
            "deferred_count": len(unsent_reply_messages),
            "results": combined_results,
            "delivery": send_result.get("delivery"),
            "text_result": send_result.get("text_result"),
            "voice_result": send_result.get("voice_result"),
        }

    emotion_image_result = {"ok": True, "status": "skipped", "reason": "not_attempted"}
    if send_result.get("ok"):
        qq_delivery_config = _load_qq_delivery_config(engine, context)
        emotion_mface_result = qq_gateway.send_emotion_mface(
            context,
            frame,
            qq_delivery_config=qq_delivery_config,
        )
        if emotion_mface_result.get("status") != "sent":
            emotion_image_result = _send_qq_emotion_image_fallback(
                engine=engine,
                qq_gateway=qq_gateway,
                context=context,
                frame=frame,
                qq_delivery_config=qq_delivery_config,
            )
    else:
        emotion_mface_result = {
            "ok": True,
            "status": "skipped",
            "reason": "main_delivery_failed",
        }

    file_send_result = qq_gateway.send_generated_files(
        context,
        list(frame.get("tool_events") or []),
    )
    sticker_send_result = qq_gateway.send_stickers(
        context,
        list(frame.get("tool_events") or []),
    )
    return {
        "frame": frame,
        "reply_messages": [*streamed_messages, *unsent_reply_messages],
        "send_result": send_result,
        "emotion_mface_result": emotion_mface_result,
        "emotion_image_result": emotion_image_result,
        "file_send_result": file_send_result,
        "sticker_send_result": sticker_send_result,
    }


def build_qq_router(
    *,
    engine: Any,
    config_module: Any,
    qq_gateway: Any,
    runtime_metrics: Any,
    logger: Any,
    log_event: LogEvent,
    tts_client: Any = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/qq/napcat/status")
    async def qq_napcat_status() -> JSONResponse:
        return JSONResponse({"status": "ok", "data": qq_gateway.status()})

    @router.post("/api/qq/self-check")
    async def qq_self_check() -> JSONResponse:
        """QQ / NapCat 连通性自检。主动测试 OneBot HTTP API 可达性和鉴权，返回结构化诊断。"""
        result = qq_gateway.self_check()
        return JSONResponse({"status": "ok", "data": result})

    @router.post("/api/qq/napcat/event")
    async def qq_napcat_event(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        event: dict = {}
        context = None
        try:
            event = await request.json()
            if not bool(getattr(config_module, "QQ_BRIDGE_ENABLED", False)):
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "disabled", "message": "QQ bridge is disabled"})

            context = qq_gateway.build_message_context(event)
            if not context.should_respond:
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    ok=True,
                )
                return JSONResponse({"status": "ignored", "reason": context.reason})

            mface_config_result = qq_gateway.handle_mface_config_command(context, event)
            if isinstance(mface_config_result, dict):
                reply = str(mface_config_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_mface_config_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(mface_config_result.get("status") or ""),
                    command_ok=bool(mface_config_result.get("ok")),
                    character_pack_id=str(mface_config_result.get("character_pack_id") or ""),
                    emotion=str(mface_config_result.get("emotion") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_mface_config_command",
                        "command_status": str(mface_config_result.get("status") or ""),
                        "command_ok": bool(mface_config_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(mface_config_result.get("character_pack_id") or ""),
                        "emotion": str(mface_config_result.get("emotion") or ""),
                        "mface": mface_config_result.get("mface"),
                        "send_result": send_result,
                    }
                )

            character_command_result = qq_gateway.handle_character_command(
                context,
                character_resource_service=getattr(engine, "desktop_pet_character_resources", None),
            )
            if isinstance(character_command_result, dict):
                reply = str(character_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_character_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(character_command_result.get("status") or ""),
                    command_ok=bool(character_command_result.get("ok")),
                    character_pack_id=str(character_command_result.get("character_pack_id") or ""),
                    state_persisted=character_command_result.get("state_persisted"),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_character_command",
                        "command_status": str(character_command_result.get("status") or ""),
                        "command_ok": bool(character_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "character_pack_id": str(character_command_result.get("character_pack_id") or ""),
                        "state_persisted": character_command_result.get("state_persisted"),
                        "send_result": send_result,
                    }
                )

            reply_mode_command_result = qq_gateway.handle_reply_mode_command(context)
            if isinstance(reply_mode_command_result, dict):
                reply = str(reply_mode_command_result.get("reply") or "").strip()
                send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request(
                    "qq_napcat_event",
                    duration_ms=duration_ms,
                    ok=bool(send_result.get("ok")),
                )
                log_event(
                    "qq_reply_mode_command",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    command_status=str(reply_mode_command_result.get("status") or ""),
                    command_ok=bool(reply_mode_command_result.get("ok")),
                    reply_mode=str(reply_mode_command_result.get("reply_mode") or ""),
                    sent=bool(send_result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                )
                return JSONResponse(
                    {
                        "status": "ok" if send_result.get("ok") else "send_failed",
                        "reason": "qq_reply_mode_command",
                        "command_status": str(reply_mode_command_result.get("status") or ""),
                        "command_ok": bool(reply_mode_command_result.get("ok")),
                        "session_id": context.session_id,
                        "profile_user_id": context.profile_user_id,
                        "reply_mode": str(reply_mode_command_result.get("reply_mode") or ""),
                        "send_result": send_result,
                    }
                )

            _care_runtime = getattr(engine, "care_runtime", None)
            _char_resources = getattr(engine, "desktop_pet_character_resources", None)
            _shop_items = (
                _char_resources.load_care_shop_items(context.character_pack_id)
                if _char_resources and context.character_pack_id
                else None
            )
            economy_command_result = qq_gateway.handle_economy_command(
                context,
                care_runtime=_care_runtime,
                shop_items=_shop_items,
                now_ms=int(time.time() * 1000),
            )
            _qq_action_note = ""
            if isinstance(economy_command_result, dict):
                if economy_command_result.get("_llm_passthrough"):
                    # Economy action processed; hand off to LLM for the actual reply
                    _qq_action_note = str(economy_command_result.get("qq_action_note") or "").strip()
                    # fall through to LLM pipeline below
                else:
                    reply = str(economy_command_result.get("reply") or "").strip()
                    send_result = qq_gateway.send_reply(context, reply) if reply else {"ok": False, "reason": "empty_reply"}
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    runtime_metrics.observe_request(
                        "qq_napcat_event",
                        duration_ms=duration_ms,
                        ok=bool(send_result.get("ok")),
                    )
                    log_event(
                        "qq_economy_command",
                        session_id=context.session_id,
                        profile_user_id=context.profile_user_id,
                        command_status=str(economy_command_result.get("status") or ""),
                        command_ok=bool(economy_command_result.get("ok")),
                        sent=bool(send_result.get("ok")),
                        duration_ms=round(duration_ms, 1),
                    )
                    return JSONResponse(
                        {
                            "status": "ok" if send_result.get("ok") else "send_failed",
                            "reason": "qq_economy_command",
                            "command_status": str(economy_command_result.get("status") or ""),
                            "command_ok": bool(economy_command_result.get("ok")),
                            "session_id": context.session_id,
                            "profile_user_id": context.profile_user_id,
                            "send_result": send_result,
                        }
                    )

            attachments_registered = []
            if context.attachments:
                attachments_registered = await asyncio.to_thread(
                    engine.ingest_qq_attachments,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    attachments=list(context.attachments),
                    timestamp=int(event.get("time") or time.time()),
                )
                attachment_ids = [
                    str(item.get("attachment_id") or "").strip()
                    for item in attachments_registered
                    if isinstance(item, dict) and str(item.get("attachment_id") or "").strip()
                ]
                debounce_token = qq_gateway.register_attachment_debounce(context, attachment_ids=attachment_ids)
                if bool(debounce_token.get("enabled")):
                    await asyncio.sleep(float(debounce_token.get("delay_seconds") or 0.0))
                    debounce_result = qq_gateway.consume_attachment_debounce(debounce_token)
                    if not bool(debounce_result.get("process")):
                        duration_ms = (time.perf_counter() - started_at) * 1000
                        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=True)
                        log_event(
                            "qq_napcat_event_buffered",
                            session_id=context.session_id,
                            profile_user_id=context.profile_user_id,
                            reason=str(debounce_result.get("reason") or "attachment_debounce"),
                            attachment_count=len(context.attachments or []),
                            attachments_registered=len(attachments_registered),
                            duration_ms=round(duration_ms, 1),
                        )
                        return JSONResponse(
                            {
                                "status": "buffered",
                                "reason": str(debounce_result.get("reason") or "attachment_debounce"),
                                "session_id": context.session_id,
                                "profile_user_id": context.profile_user_id,
                                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                                "attachments_registered": len(attachments_registered),
                            }
                        )
                    attachment_ids = [
                        str(item or "").strip()
                        for item in list(debounce_result.get("attachment_ids") or attachment_ids)
                        if str(item or "").strip()
                    ]
                if attachment_ids:
                    await asyncio.to_thread(
                        engine.wait_for_qq_attachments_settled,
                        profile_user_id=context.profile_user_id,
                        session_id=context.session_id,
                        attachment_ids=attachment_ids,
                        timeout_seconds=float(getattr(config_module, "QQ_ATTACHMENT_READY_WAIT_SECONDS", 8.0) or 0.0),
                    )

            turn_payload = context.to_turn_payload()
            if _qq_action_note:
                turn_payload["qq_action_note"] = _qq_action_note
            if context.reason == "qq_poke":
                log_event(
                    "qq_poke_context",
                    session_id=context.session_id,
                    profile_user_id=context.profile_user_id,
                    is_group=bool(context.is_group),
                    group_id=int(getattr(context, "group_id", 0) or 0),
                    event_user_id=str(event.get("user_id") or ""),
                    event_sender_id=str(event.get("sender_id") or ""),
                    event_operator_id=str(event.get("operator_id") or ""),
                    event_target_id=str(event.get("target_id") or ""),
                    event_self_id=str(event.get("self_id") or ""),
                    resolved_user_id=int(getattr(context, "user_id", 0) or 0),
                    sender_label=str(getattr(context, "sender_label", "") or ""),
                    turn_message=str(turn_payload.get("message") or "")[:240],
                )
            remote_prefetch_result = await asyncio.to_thread(
                engine.prefetch_remote_media_links_for_message,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                message=context.clean_message or context.raw_message,
                timestamp=int(event.get("time") or time.time()),
            )
            if isinstance(remote_prefetch_result, dict) and str(remote_prefetch_result.get("followup_context") or "").strip():
                prefetch_context = str(remote_prefetch_result.get("followup_context") or "").strip()
                original_extra_context = str(turn_payload.get("extra_context") or "").strip()
                turn_payload["extra_context"] = "\n\n".join(
                    part
                    for part in (
                        original_extra_context,
                        "【链接素材预处理结果】\n" + prefetch_context,
                    )
                    if part
                )
            turn_result = await asyncio.to_thread(
                _process_qq_turn_streaming,
                engine=engine,
                qq_gateway=qq_gateway,
                context=context,
                turn_payload=turn_payload,
                config_module=config_module,
                tts_client=tts_client,
                gpt_sovits_client_factory=gpt_sovits_client_factory,
            )
            frame = dict(turn_result.get("frame") or {})
            reply_messages = list(turn_result.get("reply_messages") or [])
            send_result = dict(turn_result.get("send_result") or {"ok": False, "reason": "missing_send_result", "results": []})
            emotion_mface_result = dict(turn_result.get("emotion_mface_result") or {"ok": True, "status": "skipped", "reason": "missing_result"})
            emotion_image_result = dict(turn_result.get("emotion_image_result") or {"ok": True, "status": "skipped", "reason": "missing_result"})
            file_send_result = dict(turn_result.get("file_send_result") or {"ok": True, "count": 0, "results": []})
            for item in list(file_send_result.get("results") or []):
                generated_id = str(item.get("generated_id") or "").strip()
                if not generated_id:
                    continue
                await asyncio.to_thread(
                    engine.mark_generated_file_delivery,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    generated_id=generated_id,
                    delivery_status="sent" if item.get("ok") else "failed",
                    timestamp=int(time.time()),
                )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=False)
            logger.exception("qq_napcat_event failed")
            log_event(
                "qq_napcat_event_error",
                session_id=getattr(context, "session_id", ""),
                profile_user_id=getattr(context, "profile_user_id", ""),
                post_type=str(event.get("post_type") or "") if isinstance(event, dict) else "",
                message_type=str(event.get("message_type") or "") if isinstance(event, dict) else "",
                reason=str(exc),
                duration_ms=round(duration_ms, 1),
            )
            return JSONResponse(
                {
                    "status": "error",
                    "reason": "qq_event_processing_failed",
                    "message": str(exc)[:500],
                }
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("qq_napcat_event", duration_ms=duration_ms, ok=bool(send_result.get("ok")))
        log_event(
            "qq_napcat_event",
            session_id=context.session_id,
            profile_user_id=context.profile_user_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or ""),
            reason=context.reason,
            sent=bool(send_result.get("ok")),
            attachment_count=len(context.attachments or []),
            attachments_registered=len(attachments_registered),
            duration_ms=round(duration_ms, 1),
        )
        return JSONResponse(
            {
                "status": "ok" if send_result.get("ok") else "send_failed",
                "reason": context.reason,
                "session_id": context.session_id,
                "profile_user_id": context.profile_user_id,
                "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
                # NapCat / OneBot HTTP report supports "quick operation" replies.
                # Do not include a "reply" field here, or it may send a second
                # aggregated message after our explicit send_replies() call.
                "sent_count": len(reply_messages),
                "attachment_count": len(context.attachments or []),
                "attachments_registered": len(attachments_registered),
                "send_result": send_result,
                "emotion_mface_result": emotion_mface_result,
                "emotion_image_result": emotion_image_result,
                "file_send_result": file_send_result,
            }
        )

    return router
