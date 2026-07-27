from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from services.llm_client import build_llm_client, normalize_api_protocol

from .runtime_settings import normalize_reasoning_effort


MODEL_SERVICE_SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 120
PROVIDER_MODEL_ID_MAX_LENGTH = 180


def normalize_provider_model_id(value: Any) -> str:
    """Keep a provider model id exact while rejecting unsafe transport text.

    OpenAI-compatible gateways may prefix model ids with routing labels such
    as ``[group]`` or use non-ASCII provider names.  Those are opaque API
    identifiers, not local paths or shell tokens, so an ASCII-only allowlist
    incorrectly hides valid models and rewrites user choices.  Preserve any
    compact printable identifier and reject only empty, oversized, whitespace,
    or control-character values.
    """

    text = str(value or "").strip()
    text = text.strip("`'\"“”‘’")
    text = text.rstrip("。!！?？,，;；").strip()
    if not text or len(text) > PROVIDER_MODEL_ID_MAX_LENGTH:
        return ""
    if any(char.isspace() or not char.isprintable() for char in text):
        return ""
    return text


@dataclass(frozen=True)
class ModelProviderPreset:
    id: str
    label: str
    protocol: str
    base_url: str
    api_key_required: bool
    description: str


PROVIDER_PRESETS: tuple[ModelProviderPreset, ...] = (
    ModelProviderPreset(
        id="pinai",
        label="PinAI Responses",
        protocol="responses",
        base_url="https://api.pinaic.com/v1",
        api_key_required=True,
        description="PinAI 的 OpenAI Responses API 兼容接口。",
    ),
    ModelProviderPreset(
        id="openai",
        label="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key_required=True,
        description="OpenAI 官方接口。",
    ),
    ModelProviderPreset(
        id="deepseek",
        label="DeepSeek",
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        api_key_required=True,
        description="DeepSeek 官方 OpenAI 兼容接口。",
    ),
    ModelProviderPreset(
        id="gemini",
        label="Google Gemini",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com",
        api_key_required=True,
        description="Google AI Studio / Gemini 原生 generateContent 接口。",
    ),
    ModelProviderPreset(
        id="anthropic",
        label="Anthropic Claude",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key_required=True,
        description="Anthropic 官方 Messages API。",
    ),
    ModelProviderPreset(
        id="ollama",
        label="Ollama 本地模型",
        protocol="ollama",
        base_url="http://127.0.0.1:11434",
        api_key_required=False,
        description="本机 Ollama 的 OpenAI 兼容接口。",
    ),
    ModelProviderPreset(
        id="openai_compatible",
        label="其他 OpenAI 兼容服务",
        protocol="openai",
        base_url="",
        api_key_required=True,
        description="中转站、自部署网关或其他兼容 /v1 的服务。",
    ),
)
PRESET_BY_ID = {preset.id: preset for preset in PROVIDER_PRESETS}


@dataclass(frozen=True)
class ModelServiceSettings:
    provider_id: str
    protocol: str
    base_url: str
    api_key: str
    chat_model: str
    use_for_vision: bool = True
    use_for_image_generation: bool = False
    image_generation_api_key: str = field(default="", repr=False)
    image_generation_base_url: str = ""
    image_generation_model: str = "gpt-image-2"
    vision_model: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    chat_reasoning_effort: str = ""
    chat_max_output_tokens: int = 0

    @property
    def configured(self) -> bool:
        if not self.base_url or not self.chat_model:
            return False
        if self.protocol == "ollama":
            return True
        return bool(self.api_key)


class ModelServiceConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> ModelServiceSettings | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("model_service_config_invalid")
        return settings_from_mapping(data)

    def save(self, settings: ModelServiceSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": MODEL_SERVICE_SCHEMA_VERSION,
            **asdict(settings),
        }
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)


def provider_presets_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": preset.id,
            "label": preset.label,
            "protocol": preset.protocol,
            "baseUrl": preset.base_url,
            "apiKeyRequired": preset.api_key_required,
            "description": preset.description,
        }
        for preset in PROVIDER_PRESETS
    ]


def settings_from_mapping(
    raw: dict[str, Any],
    *,
    existing_api_key: str = "",
    existing_image_generation_api_key: str = "",
    require_model: bool = True,
) -> ModelServiceSettings:
    provider_id = str(raw.get("providerId") or raw.get("provider_id") or "openai_compatible").strip()
    preset = PRESET_BY_ID.get(provider_id, PRESET_BY_ID["openai_compatible"])
    protocol = normalize_api_protocol(
        protocol=str(raw.get("protocol") or preset.protocol),
        base_url=str(raw.get("baseUrl") or raw.get("base_url") or preset.base_url),
    )
    base_url = _normalize_configured_base_url(
        str(raw.get("baseUrl") or raw.get("base_url") or preset.base_url),
        protocol=protocol,
    )
    api_key = str(raw.get("apiKey") or raw.get("api_key") or "").strip()
    if not api_key and not bool(raw.get("clearApiKey") or raw.get("clear_api_key")):
        api_key = str(existing_api_key or "").strip()
    chat_model = str(raw.get("chatModel") or raw.get("chat_model") or raw.get("model") or "").strip()
    use_for_vision = _bool_value(
        raw.get("useForVision", raw.get("use_for_vision")),
        True,
    )
    use_for_image_generation = _bool_value(
        raw.get("useForImageGeneration", raw.get("use_for_image_generation")),
        False,
    )
    image_generation_api_key = str(
        raw.get("imageGenerationApiKey") or raw.get("image_generation_api_key") or ""
    ).strip()
    if not image_generation_api_key and not bool(
        raw.get("clearImageGenerationApiKey") or raw.get("clear_image_generation_api_key")
    ):
        image_generation_api_key = str(existing_image_generation_api_key or "").strip()
    image_generation_base_url = _normalize_configured_base_url(
        str(
            raw.get("imageGenerationBaseUrl")
            or raw.get("image_generation_base_url")
            or base_url
        ),
        protocol="openai",
    )
    image_generation_model = str(
        raw.get("imageGenerationModel")
        or raw.get("image_generation_model")
        or "gpt-image-2"
    ).strip() or "gpt-image-2"
    vision_model = str(raw.get("visionModel") or raw.get("vision_model") or "").strip()
    timeout_seconds = _bounded_int(
        raw.get("timeoutSeconds", raw.get("timeout_seconds")),
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=5,
        maximum=600,
    )
    raw_chat_reasoning_effort = str(
        raw.get("chatReasoningEffort", raw.get("chat_reasoning_effort", "")) or ""
    ).strip()
    chat_reasoning_effort = normalize_reasoning_effort(raw_chat_reasoning_effort)
    if raw_chat_reasoning_effort and not chat_reasoning_effort:
        raise ValueError("model_service_chat_reasoning_effort_invalid")
    chat_max_output_tokens = _bounded_int(
        raw.get("chatMaxOutputTokens", raw.get("chat_max_output_tokens")),
        default=0,
        minimum=0,
        maximum=131072,
    )
    settings = ModelServiceSettings(
        provider_id=provider_id if provider_id in PRESET_BY_ID else "openai_compatible",
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        chat_model=chat_model,
        use_for_vision=use_for_vision,
        use_for_image_generation=use_for_image_generation,
        image_generation_api_key=image_generation_api_key,
        image_generation_base_url=image_generation_base_url,
        image_generation_model=image_generation_model,
        vision_model=vision_model,
        timeout_seconds=timeout_seconds,
        chat_reasoning_effort=chat_reasoning_effort,
        chat_max_output_tokens=chat_max_output_tokens,
    )
    validate_model_service_settings(settings, require_model=require_model)
    return settings


def effective_settings_from_config(config_module: Any) -> ModelServiceSettings:
    base_url = str(getattr(config_module, "CHAT_BASE_URL", "") or "").strip()
    protocol = normalize_api_protocol(
        protocol=str(getattr(config_module, "CHAT_API_PROTOCOL", "auto") or "auto"),
        base_url=base_url,
    )
    provider_id = infer_provider_id(protocol=protocol, base_url=base_url)
    vision_model = str(getattr(config_module, "VISION_MODEL_NAME", "") or "").strip()
    return ModelServiceSettings(
        provider_id=provider_id,
        protocol=protocol,
        base_url=base_url,
        api_key=str(getattr(config_module, "CHAT_API_KEY", "") or "").strip(),
        chat_model=str(getattr(config_module, "CHAT_MODEL_NAME", "") or "").strip(),
        use_for_vision=bool(vision_model),
        use_for_image_generation=bool(getattr(config_module, "IMAGE_GENERATION_ENABLED", False)),
        image_generation_api_key=str(getattr(config_module, "IMAGE_GENERATION_API_KEY", "") or "").strip(),
        image_generation_base_url=str(getattr(config_module, "IMAGE_GENERATION_BASE_URL", "") or "").strip(),
        image_generation_model=str(
            getattr(config_module, "IMAGE_GENERATION_MODEL", "gpt-image-2") or "gpt-image-2"
        ).strip(),
        vision_model=vision_model,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        chat_reasoning_effort=normalize_reasoning_effort(
            getattr(config_module, "LLM_CHAT_REASONING_EFFORT", "")
        ),
        chat_max_output_tokens=max(
            0,
            int(getattr(config_module, "LLM_CHAT_MAX_OUTPUT_TOKENS", 0) or 0),
        ),
    )


def effective_settings_from_runtime_settings(settings: Any) -> ModelServiceSettings:
    base_url = str(getattr(settings, "chat_base_url", "") or "").strip()
    protocol = normalize_api_protocol(
        protocol=str(getattr(settings, "chat_api_protocol", "auto") or "auto"),
        base_url=base_url,
    )
    vision_model = str(getattr(settings, "vision_model_name", "") or "").strip()
    return ModelServiceSettings(
        provider_id=infer_provider_id(protocol=protocol, base_url=base_url),
        protocol=protocol,
        base_url=base_url,
        api_key=str(getattr(settings, "chat_api_key", "") or "").strip(),
        chat_model=str(getattr(settings, "chat_model_name", "") or "").strip(),
        use_for_vision=bool(
            getattr(settings, "vision_enabled", True)
            and getattr(settings, "vision_api_key", "")
            and getattr(settings, "vision_base_url", "")
            and vision_model
        ),
        use_for_image_generation=bool(getattr(settings, "image_generation_enabled", False)),
        image_generation_api_key=str(getattr(settings, "image_generation_api_key", "") or "").strip(),
        image_generation_base_url=str(getattr(settings, "image_generation_base_url", "") or "").strip(),
        image_generation_model=str(
            getattr(settings, "image_generation_model", "gpt-image-2") or "gpt-image-2"
        ).strip(),
        vision_model=vision_model,
        timeout_seconds=int(
            getattr(settings, "vision_request_timeout", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS
        ),
        chat_reasoning_effort=normalize_reasoning_effort(
            getattr(settings, "llm_chat_reasoning_effort", "")
        ),
        chat_max_output_tokens=max(
            0,
            int(getattr(settings, "llm_chat_max_output_tokens", 0) or 0),
        ),
    )


def infer_provider_id(*, protocol: str, base_url: str) -> str:
    lowered = str(base_url or "").lower()
    if protocol == "gemini":
        return "gemini"
    if "api.pinaic.com" in lowered:
        return "pinai"
    if protocol == "ollama" or "11434" in lowered:
        return "ollama"
    if protocol == "anthropic" or "anthropic.com" in lowered:
        return "anthropic"
    if "api.openai.com" in lowered:
        return "openai"
    if "api.deepseek.com" in lowered:
        return "deepseek"
    if "generativelanguage.googleapis.com" in lowered:
        return "gemini"
    return "openai_compatible"


def public_model_service_snapshot(
    settings: ModelServiceSettings,
    *,
    source: str,
    load_status: str = "ok",
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "configured" if settings.configured else "missing_config",
        "loadStatus": load_status,
        "source": source,
        "providerId": settings.provider_id,
        "protocol": settings.protocol,
        "baseUrl": settings.base_url,
        "hasApiKey": bool(settings.api_key),
        "chatModel": settings.chat_model,
        "useForVision": settings.use_for_vision,
        "useForImageGeneration": settings.use_for_image_generation,
        "hasImageGenerationApiKey": bool(settings.image_generation_api_key),
        "imageGenerationBaseUrl": settings.image_generation_base_url,
        "imageGenerationModel": settings.image_generation_model,
        "visionModel": settings.vision_model,
        "timeoutSeconds": settings.timeout_seconds,
        "chatReasoningEffort": settings.chat_reasoning_effort,
        "chatMaxOutputTokens": settings.chat_max_output_tokens,
        "providers": provider_presets_payload(),
    }


def apply_model_service_settings(config_module: Any, settings: ModelServiceSettings) -> None:
    for prefix in ("TEXT", "AUX", "CHAT"):
        setattr(config_module, f"{prefix}_API_KEY", settings.api_key)
        setattr(config_module, f"{prefix}_BASE_URL", settings.base_url)
        setattr(config_module, f"{prefix}_MODEL_NAME", settings.chat_model)
        setattr(config_module, f"{prefix}_API_PROTOCOL", settings.protocol)
    setattr(config_module, "LLM_CHAT_REASONING_EFFORT", settings.chat_reasoning_effort)
    setattr(config_module, "LLM_CHAT_MAX_OUTPUT_TOKENS", settings.chat_max_output_tokens)
    setattr(config_module, "IMAGE_GENERATION_ENABLED", settings.use_for_image_generation)
    setattr(config_module, "IMAGE_GENERATION_API_KEY", settings.image_generation_api_key)
    setattr(config_module, "IMAGE_GENERATION_BASE_URL", settings.image_generation_base_url)
    setattr(config_module, "IMAGE_GENERATION_MODEL", settings.image_generation_model)

    if settings.use_for_vision:
        setattr(config_module, "VISION_API_KEY", settings.api_key)
        setattr(config_module, "VISION_BASE_URL", settings.base_url)
        setattr(config_module, "VISION_MODEL_NAME", settings.vision_model or settings.chat_model)
        setattr(config_module, "VISION_API_PROTOCOL", settings.protocol)
    else:
        # The visible model-service settings own the runtime VISION_* route.
        # Leaving previous values in place silently keeps an old provider alive
        # (for example a stale DashScope key) even though the UI says vision is off.
        setattr(config_module, "VISION_API_KEY", "")
        setattr(config_module, "VISION_BASE_URL", "")
        setattr(config_module, "VISION_MODEL_NAME", "")
        setattr(config_module, "VISION_API_PROTOCOL", settings.protocol)


def probe_model_ids(settings: ModelServiceSettings) -> list[str]:
    validate_model_service_settings(settings, require_model=False)
    if settings.protocol == "anthropic":
        endpoint = _anthropic_models_endpoint(settings.base_url)
        response = requests.get(
            endpoint,
            headers={
                "x-api-key": settings.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=settings.timeout_seconds,
        )
        _raise_provider_error(response)
        payload = response.json()
        return _model_ids_from_payload(payload)

    client = build_llm_client(
        api_key=settings.api_key,
        base_url=settings.base_url,
        protocol=settings.protocol,
        timeout=float(settings.timeout_seconds),
        max_retries=0,
    )
    response = client.models.list()
    raw_models = getattr(response, "data", response)
    ids: set[str] = set()
    for item in raw_models or []:
        model_id = getattr(item, "id", "")
        if not model_id and isinstance(item, dict):
            model_id = item.get("id", "")
        text = normalize_provider_model_id(model_id)
        if text:
            ids.add(text)
    return sorted(ids, key=str.casefold)


def test_model_service(settings: ModelServiceSettings) -> str:
    validate_model_service_settings(settings)
    client = build_llm_client(
        api_key=settings.api_key,
        base_url=settings.base_url,
        protocol=settings.protocol,
        timeout=float(settings.timeout_seconds),
        max_retries=0,
    )
    if settings.protocol == "responses":
        response = client.responses.create(
            model=settings.chat_model,
            input="Reply with only OK.",
            max_output_tokens=8,
            store=False,
        )
        return str(getattr(response, "output_text", "") or "").strip() or "OK"
    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[{"role": "user", "content": "Reply with only OK."}],
        temperature=0,
        max_tokens=8,
    )
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("model_service_response_invalid") from exc
    return str(text or "").strip() or "OK"


def validate_model_service_settings(
    settings: ModelServiceSettings,
    *,
    require_model: bool = True,
) -> None:
    if settings.protocol not in {"openai", "responses", "anthropic", "gemini", "ollama"}:
        raise ValueError("model_service_protocol_invalid")
    if not settings.base_url.startswith(("http://", "https://")):
        raise ValueError("model_service_base_url_invalid")
    if settings.protocol != "ollama" and not settings.api_key:
        raise ValueError("model_service_api_key_missing")
    if require_model and not settings.chat_model:
        raise ValueError("model_service_model_missing")


def redact_provider_error(error: Exception, *, api_key: str = "") -> str:
    text = str(error or "").strip() or error.__class__.__name__
    if api_key:
        text = text.replace(api_key, "<redacted>")
    return text[:600]


def load_and_apply_saved_model_service(
    *,
    store: ModelServiceConfigStore,
    config_module: Any,
    on_error: Callable[[Exception], None] | None = None,
) -> ModelServiceSettings | None:
    try:
        settings = store.load()
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        return None
    if settings is not None:
        apply_model_service_settings(config_module, settings)
    return settings


def _normalize_configured_base_url(base_url: str, *, protocol: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
    if protocol == "anthropic" and normalized.endswith("/v1/messages"):
        normalized = normalized[: -len("/messages")]
    return normalized


def _anthropic_models_endpoint(base_url: str) -> str:
    clean = str(base_url or "").strip().rstrip("/")
    if clean.endswith("/v1/messages"):
        clean = clean[: -len("/messages")]
    if clean.endswith("/v1"):
        return f"{clean}/models"
    return f"{clean}/v1/models"


def _model_ids_from_payload(payload: Any) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("model_service_models_response_invalid")
    ids = {
        str(item.get("id") or "").strip()
        for item in data
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    return sorted(ids, key=str.casefold)


def _raise_provider_error(response: requests.Response) -> None:
    if response.ok:
        return
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
            elif error:
                message = str(error).strip()
    except Exception:
        message = str(response.text or "").strip()
    raise RuntimeError(message or f"model_service_http_{response.status_code}")


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default
