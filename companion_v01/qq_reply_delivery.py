"""QQ text/voice delivery selection, failure fallback and emotion images."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .qq_voice_delivery import _synthesize_qq_voice_file
from .qq_gateway_runtime.validation import _safe_reply_mode
from .runtime_settings import runtime_setting

if TYPE_CHECKING:
    from .qq_gateway import NapCatQQGateway


def _normalize_reply_medium(value: Any) -> str:
    return _safe_reply_mode(value, default="")


def _streaming_allows_text(reply_mode: str) -> bool:
    mode = _normalize_reply_medium(reply_mode) or "text"
    return mode in {"text", "both"}


def _streaming_allows_tool_preface(reply_mode: str) -> bool:
    """Allow a native-tool preface unless QQ has explicitly selected voice-only."""

    return _streaming_allows_text(reply_mode)


def _resolve_delivery_medium(
    *,
    context: Any,
    qq_gateway: Any,
) -> dict[str, str]:
    reply_mode = (
        _normalize_reply_medium(getattr(context, "reply_mode", ""))
        or _normalize_reply_medium(qq_gateway.resolve_reply_mode(getattr(context, "session_id", "")))
        or "text"
    )
    return {
        "reply_mode": reply_mode,
        "medium": reply_mode,
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
    settings: Any = None,
) -> dict[str, Any]:
    plan = _resolve_delivery_medium(
        context=context,
        qq_gateway=qq_gateway,
    )
    medium = plan["medium"]
    text_enabled = medium in {"text", "both"}
    voice_enabled = medium in {"voice", "both"}
    text_send_result = {"ok": True, "count": 0, "results": []}
    voice_send_result = {"ok": True, "count": 0, "results": []}

    if text_enabled and unsent_reply_messages:
        text_send_result = qq_gateway.send_replies(context, unsent_reply_messages)

    voice_reason = ""
    if voice_enabled:
        max_segments = int(
            runtime_setting(settings, config_module, "qq_voice_max_segments", "QQ_VOICE_MAX_SEGMENTS", 3) or 3
        )
        voice_messages = [str(message or "").strip() for message in reply_messages if str(message or "").strip()]
        speech_units = frame.get("utterances")
        # Display and spoken sentences have different boundaries. A legacy
        # display-message cap must not silently discard translated dialogue.
        bilingual = isinstance(speech_units, list)
        voice_text = "\n".join(voice_messages if bilingual else voice_messages[:max_segments]).strip()
        if voice_text:
            try:
                voice_file = _synthesize_qq_voice_file(
                    engine=engine,
                    config_module=config_module,
                    tts_client=tts_client,
                    text=voice_text,
                    context=context,
                    emotion=str(frame.get("emotion") or ""),
                    settings=settings,
                    **({"utterances": speech_units} if bilingual else {}),
                )
            except Exception:
                # Unexpected provider/cache errors can contain private paths or keys.
                voice_file = {"ok": False, "reason": "voice_synthesis_failed"}
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
    current_outfit_id: str = "",
) -> dict[str, Any]:
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    if not character_pack_id:
        return {"ok": True, "status": "skipped", "reason": "empty_character_pack_id"}
    image_config = (
        qq_delivery_config.get("emotion_images") if isinstance(qq_delivery_config.get("emotion_images"), dict) else {}
    )
    if image_config.get("enabled") is False:
        return {"ok": True, "status": "skipped", "reason": "disabled"}
    service = getattr(engine, "desktop_pet_character_resources", None)
    resolver = getattr(service, "resolve_emotion_image_file", None)
    if not callable(resolver):
        return {"ok": True, "status": "skipped", "reason": "missing_character_resource_service"}
    emotion = str((frame or {}).get("emotion") or "").strip()
    try:
        image = resolver(character_pack_id, emotion, outfit_id=current_outfit_id)
    except TypeError:
        try:
            image = resolver(character_pack_id, emotion)
        except Exception:
            image = {}
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
