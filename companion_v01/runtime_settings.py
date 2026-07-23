"""Immutable per-Bot runtime settings snapshots.

The process-level ``config`` module remains the boot/default source during the
Slice 2 migration.  A BotRuntime captures the effective model and speech
settings once so request handlers no longer read mutable module globals during
a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


REASONING_EFFORT_VALUES = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def normalize_reasoning_effort(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in REASONING_EFFORT_VALUES else ""


@dataclass(frozen=True, slots=True)
class BotSettingsView:
    """Effective model and speech settings for one BotRuntime.

    Secret fields are deliberately excluded from repr/public snapshots.  The
    view is immutable so a second Bot cannot be affected by another Bot's
    settings update or request-time provider selection.
    """

    text_api_key: str = field(default="", repr=False)
    text_base_url: str = ""
    text_model_name: str = "deepseek-chat"
    text_api_protocol: str = "auto"
    aux_api_key: str = field(default="", repr=False)
    aux_base_url: str = ""
    aux_model_name: str = "deepseek-chat"
    aux_api_protocol: str = "auto"
    chat_api_key: str = field(default="", repr=False)
    chat_base_url: str = ""
    chat_model_name: str = ""
    chat_api_protocol: str = "auto"
    vision_api_key: str = field(default="", repr=False)
    vision_base_url: str = ""
    vision_model_name: str = ""
    vision_api_protocol: str = "auto"
    vision_enabled: bool = True
    vision_request_timeout: float = 60.0
    vision_prompt_version: str = "v1"
    vision_auto_scene_observe: bool = True
    vision_auto_gift_observe: bool = True
    vision_auto_outfit_observe: bool = True
    vision_max_image_bytes: int = 8 * 1024 * 1024
    prompt_cache_hints_enabled: bool = True
    prompt_cache_hints_force: bool = False
    prompt_cache_namespace: str = "akane"
    prompt_cache_retention: str = ""
    llm_context_window: int = 0
    llm_auto_compact_token_limit: int = 0
    llm_reasoning_effort: str = ""
    llm_aux_reasoning_effort: str = ""
    llm_chat_reasoning_effort: str = ""

    # Voice/runtime settings.  These belong to a BotRuntime rather than the
    # process-wide config module so multiple Bots can use different voices and
    # local speech providers without changing each other's requests.
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "+6%"
    tts_volume: str = "+0%"
    tts_pitch: str = "+4Hz"
    streaming_tts_enabled: bool = True
    gpt_sovits_tts_timeout_seconds: float = 45.0
    gpt_sovits_text_lang: str = "zh"
    gpt_sovits_media_type: str = "wav"
    gpt_sovits_streaming_mode: bool = False
    gpt_sovits_parallel_infer: bool | None = None
    gpt_sovits_split_bucket: bool | None = None
    gpt_sovits_batch_size: int | None = None
    gpt_sovits_speed_factor: float | None = None
    gpt_sovits_fragment_interval: float | None = None
    gpt_sovits_text_split_method: str = ""
    asr_max_upload_mb: float = 20.0
    openai_compat_asr_timeout_seconds: float = 45.0
    openai_compat_asr_model: str = "whisper-1"
    asr_whisper_model_size: str = "small"
    asr_whisper_device: str = "auto"
    asr_whisper_compute_type: str = "auto"
    asr_language: str = "zh"
    asr_vad_filter: bool = True
    whisper_cache_dir: str = ""
    qq_tts_profile_user_id: str = "master"
    qq_voice_max_text_chars: int = 280
    qq_voice_max_segments: int = 3

    @classmethod
    def from_config(cls, config_module: Any) -> "BotSettingsView":
        return cls(
            text_api_key=_text(getattr(config_module, "TEXT_API_KEY", "")),
            text_base_url=_text(getattr(config_module, "TEXT_BASE_URL", "")),
            text_model_name=_text(getattr(config_module, "TEXT_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            text_api_protocol=_text(getattr(config_module, "TEXT_API_PROTOCOL", "auto")) or "auto",
            aux_api_key=_text(getattr(config_module, "AUX_API_KEY", "")),
            aux_base_url=_text(getattr(config_module, "AUX_BASE_URL", "")),
            aux_model_name=_text(getattr(config_module, "AUX_MODEL_NAME", "deepseek-chat")) or "deepseek-chat",
            aux_api_protocol=_text(getattr(config_module, "AUX_API_PROTOCOL", "auto")) or "auto",
            chat_api_key=_text(getattr(config_module, "CHAT_API_KEY", "")),
            chat_base_url=_text(getattr(config_module, "CHAT_BASE_URL", "")),
            chat_model_name=_text(getattr(config_module, "CHAT_MODEL_NAME", "")),
            chat_api_protocol=_text(getattr(config_module, "CHAT_API_PROTOCOL", "auto")) or "auto",
            vision_api_key=_text(getattr(config_module, "VISION_API_KEY", "")),
            vision_base_url=_text(getattr(config_module, "VISION_BASE_URL", "")),
            vision_model_name=_text(getattr(config_module, "VISION_MODEL_NAME", "")),
            vision_api_protocol=_text(getattr(config_module, "VISION_API_PROTOCOL", "auto")) or "auto",
            vision_enabled=bool(getattr(config_module, "VISION_ENABLED", True)),
            vision_request_timeout=max(
                1.0,
                float(getattr(config_module, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0),
            ),
            vision_prompt_version=_text(getattr(config_module, "VISION_PROMPT_VERSION", "v1")) or "v1",
            vision_auto_scene_observe=bool(getattr(config_module, "VISION_AUTO_SCENE_OBSERVE", True)),
            vision_auto_gift_observe=bool(getattr(config_module, "VISION_AUTO_GIFT_OBSERVE", True)),
            vision_auto_outfit_observe=bool(getattr(config_module, "VISION_AUTO_OUTFIT_OBSERVE", True)),
            vision_max_image_bytes=max(
                128 * 1024,
                int(getattr(config_module, "VISION_MAX_IMAGE_BYTES", 8 * 1024 * 1024) or 0),
            ),
            prompt_cache_hints_enabled=bool(getattr(config_module, "PROMPT_CACHE_HINTS_ENABLED", True)),
            prompt_cache_hints_force=bool(getattr(config_module, "PROMPT_CACHE_HINTS_FORCE", False)),
            prompt_cache_namespace=_text(getattr(config_module, "PROMPT_CACHE_NAMESPACE", "akane")) or "akane",
            prompt_cache_retention=_text(getattr(config_module, "PROMPT_CACHE_RETENTION", "")).lower(),
            llm_context_window=max(0, int(getattr(config_module, "LLM_CONTEXT_WINDOW", 0) or 0)),
            llm_auto_compact_token_limit=max(
                0,
                int(getattr(config_module, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or 0),
            ),
            llm_reasoning_effort=normalize_reasoning_effort(
                getattr(config_module, "LLM_REASONING_EFFORT", "")
            ),
            llm_aux_reasoning_effort=normalize_reasoning_effort(
                getattr(config_module, "LLM_AUX_REASONING_EFFORT", "")
            ),
            llm_chat_reasoning_effort=normalize_reasoning_effort(
                getattr(config_module, "LLM_CHAT_REASONING_EFFORT", "")
            ),
            tts_voice=_text(getattr(config_module, "TTS_VOICE", "zh-CN-XiaoxiaoNeural"))
            or "zh-CN-XiaoxiaoNeural",
            tts_rate=_text(getattr(config_module, "TTS_RATE", "+6%")) or "+6%",
            tts_volume=_text(getattr(config_module, "TTS_VOLUME", "+0%")) or "+0%",
            tts_pitch=_text(getattr(config_module, "TTS_PITCH", "+4Hz")) or "+4Hz",
            streaming_tts_enabled=bool(getattr(config_module, "STREAMING_TTS_ENABLED", True)),
            gpt_sovits_tts_timeout_seconds=max(
                1.0, float(getattr(config_module, "GPT_SOVITS_TTS_TIMEOUT_SECONDS", 45.0) or 45.0)
            ),
            gpt_sovits_text_lang=_text(getattr(config_module, "GPT_SOVITS_TEXT_LANG", "zh")) or "zh",
            gpt_sovits_media_type=_text(getattr(config_module, "GPT_SOVITS_MEDIA_TYPE", "wav")) or "wav",
            gpt_sovits_streaming_mode=bool(getattr(config_module, "GPT_SOVITS_STREAMING_MODE", False)),
            gpt_sovits_parallel_infer=_optional_bool(getattr(config_module, "GPT_SOVITS_PARALLEL_INFER", None)),
            gpt_sovits_split_bucket=_optional_bool(getattr(config_module, "GPT_SOVITS_SPLIT_BUCKET", None)),
            gpt_sovits_batch_size=_optional_int(getattr(config_module, "GPT_SOVITS_BATCH_SIZE", None)),
            gpt_sovits_speed_factor=_optional_float(getattr(config_module, "GPT_SOVITS_SPEED_FACTOR", None)),
            gpt_sovits_fragment_interval=_optional_float(
                getattr(config_module, "GPT_SOVITS_FRAGMENT_INTERVAL", None)
            ),
            gpt_sovits_text_split_method=_text(getattr(config_module, "GPT_SOVITS_TEXT_SPLIT_METHOD", "")),
            asr_max_upload_mb=max(0.1, float(getattr(config_module, "ASR_MAX_UPLOAD_MB", 20.0) or 20.0)),
            openai_compat_asr_timeout_seconds=max(
                1.0,
                float(getattr(config_module, "OPENAI_COMPAT_ASR_TIMEOUT_SECONDS", 45.0) or 45.0),
            ),
            openai_compat_asr_model=_text(getattr(config_module, "OPENAI_COMPAT_ASR_MODEL", "whisper-1"))
            or "whisper-1",
            asr_whisper_model_size=_text(
                getattr(config_module, "ASR_WHISPER_MODEL_SIZE", getattr(config_module, "WHISPER_MODEL_SIZE", "small"))
            )
            or "small",
            asr_whisper_device=_text(
                getattr(config_module, "ASR_WHISPER_DEVICE", getattr(config_module, "WHISPER_DEVICE", "auto"))
            )
            or "auto",
            asr_whisper_compute_type=_text(
                getattr(
                    config_module,
                    "ASR_WHISPER_COMPUTE_TYPE",
                    getattr(config_module, "WHISPER_COMPUTE_TYPE", "auto"),
                )
            )
            or "auto",
            asr_language=_text(getattr(config_module, "ASR_LANGUAGE", "zh")) or "zh",
            asr_vad_filter=bool(getattr(config_module, "ASR_VAD_FILTER", True)),
            whisper_cache_dir=_text(getattr(config_module, "WHISPER_CACHE_DIR", "")),
            qq_tts_profile_user_id=_safe_profile_id(
                getattr(config_module, "QQ_TTS_PROFILE_USER_ID", "")
                or getattr(config_module, "WEB_OWNER_PROFILE_USER_ID", "master")
            ),
            qq_voice_max_text_chars=max(20, min(1200, int(getattr(config_module, "QQ_VOICE_MAX_TEXT_CHARS", 280) or 280))),
            qq_voice_max_segments=max(1, min(10, int(getattr(config_module, "QQ_VOICE_MAX_SEGMENTS", 3) or 3))),
        )

    def overlay(self, overrides: Mapping[str, Any] | None = None) -> "BotSettingsView":
        if not overrides:
            return self
        allowed = {
            "text_api_key",
            "text_base_url",
            "text_model_name",
            "text_api_protocol",
            "aux_api_key",
            "aux_base_url",
            "aux_model_name",
            "aux_api_protocol",
            "chat_api_key",
            "chat_base_url",
            "chat_model_name",
            "chat_api_protocol",
            "vision_api_key",
            "vision_base_url",
            "vision_model_name",
            "vision_api_protocol",
            "vision_enabled",
            "vision_request_timeout",
            "vision_prompt_version",
            "vision_auto_scene_observe",
            "vision_auto_gift_observe",
            "vision_auto_outfit_observe",
            "vision_max_image_bytes",
            "prompt_cache_hints_enabled",
            "prompt_cache_hints_force",
            "prompt_cache_namespace",
            "prompt_cache_retention",
            "llm_context_window",
            "llm_auto_compact_token_limit",
            "llm_reasoning_effort",
            "llm_aux_reasoning_effort",
            "llm_chat_reasoning_effort",
            "tts_voice",
            "tts_rate",
            "tts_volume",
            "tts_pitch",
            "streaming_tts_enabled",
            "gpt_sovits_tts_timeout_seconds",
            "gpt_sovits_text_lang",
            "gpt_sovits_media_type",
            "gpt_sovits_streaming_mode",
            "gpt_sovits_parallel_infer",
            "gpt_sovits_split_bucket",
            "gpt_sovits_batch_size",
            "gpt_sovits_speed_factor",
            "gpt_sovits_fragment_interval",
            "gpt_sovits_text_split_method",
            "asr_max_upload_mb",
            "openai_compat_asr_timeout_seconds",
            "openai_compat_asr_model",
            "asr_whisper_model_size",
            "asr_whisper_device",
            "asr_whisper_compute_type",
            "asr_language",
            "asr_vad_filter",
            "whisper_cache_dir",
            "qq_tts_profile_user_id",
            "qq_voice_max_text_chars",
            "qq_voice_max_segments",
        }
        unknown = sorted(str(key) for key in overrides if key not in allowed)
        if unknown:
            raise ValueError(f"bot_settings_unknown_field:{unknown[0]}")
        values = {key: _overlay_value(key, value) for key, value in overrides.items()}
        return replace(self, **values)

    def with_model_service(self, model_settings: Any) -> "BotSettingsView":
        api_key = _text(getattr(model_settings, "api_key", ""))
        base_url = _text(getattr(model_settings, "base_url", ""))
        model = _text(getattr(model_settings, "chat_model", ""))
        protocol = _text(getattr(model_settings, "protocol", "auto")) or "auto"
        # A saved model-service profile owns the primary chat route, but an
        # explicitly configured AUX route is an intentional advanced split
        # (for example: PinAI chat + DeepSeek compaction).  Only inherit the
        # primary provider when AUX has no usable provider of its own.
        preserve_explicit_aux = _configured(
            self.aux_api_key,
            self.aux_base_url,
            self.aux_model_name,
            self.aux_api_protocol,
        )
        use_for_vision = bool(getattr(model_settings, "use_for_vision", True))
        vision_model = _text(getattr(model_settings, "vision_model", "")) or model
        chat_reasoning_effort = normalize_reasoning_effort(
            getattr(model_settings, "chat_reasoning_effort", "")
        )
        return replace(
            self,
            text_api_key=api_key,
            text_base_url=base_url,
            text_model_name=model,
            text_api_protocol=protocol,
            aux_api_key=self.aux_api_key if preserve_explicit_aux else api_key,
            aux_base_url=self.aux_base_url if preserve_explicit_aux else base_url,
            aux_model_name=self.aux_model_name if preserve_explicit_aux else model,
            aux_api_protocol=self.aux_api_protocol if preserve_explicit_aux else protocol,
            chat_api_key=api_key,
            chat_base_url=base_url,
            chat_model_name=model,
            chat_api_protocol=protocol,
            vision_api_key=api_key if use_for_vision else "",
            vision_base_url=base_url if use_for_vision else "",
            vision_model_name=vision_model if use_for_vision else "",
            vision_api_protocol=protocol,
            llm_chat_reasoning_effort=chat_reasoning_effort,
        )

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "text": {
                "base_url": self.text_base_url,
                "model": self.text_model_name,
                "protocol": self.text_api_protocol,
                "configured": _configured(
                    self.text_api_key,
                    self.text_base_url,
                    self.text_model_name,
                    self.text_api_protocol,
                ),
            },
            "aux": {
                "base_url": self.aux_base_url,
                "model": self.aux_model_name,
                "protocol": self.aux_api_protocol,
                "configured": _configured(
                    self.aux_api_key,
                    self.aux_base_url,
                    self.aux_model_name,
                    self.aux_api_protocol,
                ),
            },
            "chat": {
                "base_url": self.chat_base_url,
                "model": self.chat_model_name,
                "protocol": self.chat_api_protocol,
                "configured": _configured(
                    self.chat_api_key,
                    self.chat_base_url,
                    self.chat_model_name,
                    self.chat_api_protocol,
                ),
            },
            "vision": {
                "enabled": self.vision_enabled,
                "base_url": self.vision_base_url,
                "model": self.vision_model_name,
                "protocol": self.vision_api_protocol,
                "configured": self.vision_enabled
                and _configured(
                    self.vision_api_key,
                    self.vision_base_url,
                    self.vision_model_name,
                    self.vision_api_protocol,
                ),
            },
            "prompt_cache": {
                "hints_enabled": self.prompt_cache_hints_enabled,
                "hints_force": self.prompt_cache_hints_force,
                "namespace": self.prompt_cache_namespace,
                "retention": self.prompt_cache_retention,
            },
            "context": {
                "window": self.llm_context_window,
                "auto_compact_token_limit": self.llm_auto_compact_token_limit,
            },
            "reasoning": {
                "default": self.llm_reasoning_effort,
                "aux": self.llm_aux_reasoning_effort,
                "chat": self.llm_chat_reasoning_effort,
            },
            "voice": {
                "voice": self.tts_voice,
                "rate": self.tts_rate,
                "volume": self.tts_volume,
                "pitch": self.tts_pitch,
                "streaming": self.streaming_tts_enabled,
                "gpt_sovits": {
                    "text_lang": self.gpt_sovits_text_lang,
                    "media_type": self.gpt_sovits_media_type,
                    "streaming_mode": self.gpt_sovits_streaming_mode,
                },
                "asr": {
                    "model": self.asr_whisper_model_size,
                    "language": self.asr_language,
                },
            },
            "qq_voice": {
                "profile_user_id": self.qq_tts_profile_user_id,
                "max_text_chars": self.qq_voice_max_text_chars,
                "max_segments": self.qq_voice_max_segments,
            },
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _configured(api_key: str, base_url: str, model: str, protocol: str) -> bool:
    return bool(base_url and model and (protocol == "ollama" or api_key))


def _overlay_value(key: str, value: Any) -> Any:
    if key in {"llm_reasoning_effort", "llm_aux_reasoning_effort", "llm_chat_reasoning_effort"}:
        raw = str(value or "").strip()
        normalized = normalize_reasoning_effort(raw)
        if raw and not normalized:
            raise ValueError(f"bot_settings_reasoning_effort_invalid:{key}")
        return normalized
    if key in {
        "vision_enabled",
        "vision_auto_scene_observe",
        "vision_auto_gift_observe",
        "vision_auto_outfit_observe",
        "prompt_cache_hints_enabled",
        "prompt_cache_hints_force",
        "streaming_tts_enabled",
        "gpt_sovits_streaming_mode",
        "asr_vad_filter",
    }:
        if not isinstance(value, bool):
            raise ValueError(f"bot_settings_boolean_required:{key}")
        return value
    if key in {
        "vision_max_image_bytes",
        "llm_context_window",
        "llm_auto_compact_token_limit",
        "qq_voice_max_text_chars",
        "qq_voice_max_segments",
        "gpt_sovits_batch_size",
    }:
        if value is None and key == "gpt_sovits_batch_size":
            return None
        parsed = int(value)
        if key == "vision_max_image_bytes":
            return max(128 * 1024, parsed)
        if key == "qq_voice_max_text_chars":
            return max(20, min(1200, parsed))
        if key == "qq_voice_max_segments":
            return max(1, min(10, parsed))
        return max(0, parsed)
    if key in {
        "vision_request_timeout",
        "gpt_sovits_tts_timeout_seconds",
        "asr_max_upload_mb",
        "openai_compat_asr_timeout_seconds",
        "gpt_sovits_speed_factor",
        "gpt_sovits_fragment_interval",
    }:
        if value is None and key in {"gpt_sovits_speed_factor", "gpt_sovits_fragment_interval"}:
            return None
        parsed = float(value)
        if key in {"vision_request_timeout", "gpt_sovits_tts_timeout_seconds", "openai_compat_asr_timeout_seconds"}:
            return max(1.0, parsed)
        return max(0.1, parsed) if key == "asr_max_upload_mb" else parsed
    if key in {"gpt_sovits_parallel_infer", "gpt_sovits_split_bucket"}:
        return _optional_bool(value)
    if key == "qq_tts_profile_user_id":
        return _safe_profile_id(value)
    if key == "vision_request_timeout":
        return max(1.0, float(value))
    text = _text(value)
    if key == "prompt_cache_namespace":
        return text or "akane"
    if key == "prompt_cache_retention":
        return text.lower()
    if key in {"vision_prompt_version", "vision_api_protocol"}:
        return text or ("v1" if key == "vision_prompt_version" else "auto")
    return text


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("bot_settings_boolean_required")


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _safe_profile_id(value: Any) -> str:
    import re

    raw = _text(value)
    if raw.lower() in {"conversation", "context", "current"}:
        return raw.lower()
    return raw if raw and re.fullmatch(r"[A-Za-z0-9_.-]+", raw) else "master"


def runtime_setting(settings: Any, config_module: Any, field_name: str, config_name: str, default: Any) -> Any:
    """Read a Bot-scoped setting with a legacy config fallback.

    Route builders are still used directly by older tests and integrations,
    so ``settings`` is optional during the migration.  A real BotRuntime
    always supplies it and therefore never needs to mutate process globals.
    """

    if settings is not None and hasattr(settings, field_name):
        return getattr(settings, field_name)
    return getattr(config_module, config_name, default) if config_module is not None else default


__all__ = [
    "BotSettingsView",
    "REASONING_EFFORT_VALUES",
    "normalize_reasoning_effort",
    "runtime_setting",
]
