from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from services.llm_client import build_llm_client, normalize_api_protocol
from services.gemini_native_client import gemini_thinking_config

from .runtime_settings import normalize_reasoning_effort, normalize_thinking_mode


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
class SubagentModelSettings:
    mode: str = "inherit"
    provider_id: str = "openai_compatible"
    protocol: str = "openai"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    thinking_mode: str = ""
    reasoning_effort: str = ""
    max_output_tokens: int = 0
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        if self.mode == "inherit":
            return True
        if self.mode == "shared_service":
            return bool(self.model)
        if self.mode == "independent":
            if not self.base_url or not self.model:
                return False
            if self.protocol == "ollama":
                return True
            return bool(self.api_key)
        return False

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class ModelServiceSettings:
    provider_id: str
    protocol: str
    base_url: str
    api_key: str = field(repr=False)
    chat_model: str
    use_for_vision: bool = True
    use_for_image_generation: bool = False
    image_generation_api_key: str = field(default="", repr=False)
    image_generation_base_url: str = ""
    image_generation_model: str = "gpt-image-2"
    vision_model: str = ""
    # 独立的视觉 provider。当 chat 与 vision 需要走不同服务商（例如聊天用
    # DeepSeek、看图用 Gemini）时，填这三项 + vision_model；此时视觉不再
    # 复用 chat 的 key/base_url。留空则维持旧行为：视觉复用 chat provider。
    vision_api_key: str = field(default="", repr=False)
    vision_base_url: str = ""
    vision_api_protocol: str = ""
    standalone_vision: bool | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    chat_thinking_mode: str = ""
    chat_reasoning_effort: str = ""
    chat_max_output_tokens: int = 0
    subagent: SubagentModelSettings = field(default_factory=SubagentModelSettings)

    @property
    def configured(self) -> bool:
        if not self.base_url or not self.chat_model:
            return False
        if self.protocol == "ollama":
            return True
        return bool(self.api_key)

    @property
    def vision_configured(self) -> bool:
        """Whether an independent vision provider is fully specified.

        A standalone vision route requires base_url + model; api_key is only
        mandatory for non-Ollama providers.  Protocol falls back to the chat
        protocol so an empty value still yields a usable client.
        """
        if not (self.vision_base_url and self.vision_model):
            return False
        if self.vision_api_protocol != "ollama":
            return bool(self.vision_api_key)
        return True

    @property
    def uses_standalone_vision(self) -> bool:
        # Older files inferred this from the route; new saves carry the user's
        # explicit choice so turning it off cannot reactivate a stale provider.
        return self.vision_configured if self.standalone_vision is None else self.standalone_vision


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
        subagent_dict = asdict(settings.subagent)
        if settings.subagent.mode != "independent":
            subagent_dict["api_key"] = ""
        payload = {
            "schemaVersion": MODEL_SERVICE_SCHEMA_VERSION,
            **asdict(settings),
            "subagent": subagent_dict,
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
    existing_vision_api_key: str = "",
    existing_subagent: SubagentModelSettings | None = None,
    require_model: bool = True,
    validate_primary: bool = True,
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
    vision_api_key = str(
        raw.get("visionApiKey") or raw.get("vision_api_key") or ""
    ).strip()
    if not vision_api_key and not bool(
        raw.get("clearVisionApiKey") or raw.get("clear_vision_api_key")
    ):
        vision_api_key = str(existing_vision_api_key or "").strip()
    vision_base_url = _normalize_configured_base_url(
        str(raw.get("visionBaseUrl") or raw.get("vision_base_url") or ""),
        protocol=str(raw.get("visionApiProtocol") or raw.get("vision_api_protocol") or "openai"),
    )
    vision_api_protocol = normalize_api_protocol(
        protocol=str(
            raw.get("visionApiProtocol")
            or raw.get("vision_api_protocol")
            or ""
        ),
        base_url=str(raw.get("visionBaseUrl") or raw.get("vision_base_url") or ""),
    )
    timeout_seconds = _bounded_int(
        raw.get("timeoutSeconds", raw.get("timeout_seconds")),
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=5,
        maximum=600,
    )
    raw_chat_thinking_mode = str(raw.get("chatThinkingMode", raw.get("chat_thinking_mode", "")) or "").strip()
    chat_thinking_mode = normalize_thinking_mode(raw_chat_thinking_mode)
    if raw_chat_thinking_mode and not chat_thinking_mode:
        raise ValueError("model_service_chat_thinking_mode_invalid")
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

    subagent_raw = raw.get("subagent")
    if subagent_raw is None:
        if existing_subagent is not None:
            if existing_subagent.mode == "shared_service":
                subagent_settings = SubagentModelSettings(
                    mode="shared_service",
                    provider_id=provider_id if provider_id in PRESET_BY_ID else "openai_compatible",
                    protocol=protocol,
                    base_url=base_url,
                    api_key=api_key,
                    model=existing_subagent.model,
                    thinking_mode=existing_subagent.thinking_mode,
                    reasoning_effort=existing_subagent.reasoning_effort,
                    max_output_tokens=existing_subagent.max_output_tokens,
                    timeout_seconds=existing_subagent.timeout_seconds,
                )
            elif existing_subagent.mode == "inherit":
                subagent_settings = SubagentModelSettings(
                    mode="inherit",
                    provider_id=provider_id if provider_id in PRESET_BY_ID else "openai_compatible",
                    protocol=protocol,
                    base_url=base_url,
                    api_key=api_key,
                    model=chat_model,
                    thinking_mode="",
                    reasoning_effort=chat_reasoning_effort,
                    max_output_tokens=chat_max_output_tokens,
                    timeout_seconds=timeout_seconds,
                )
            else:
                subagent_settings = existing_subagent
        else:
            subagent_settings = SubagentModelSettings(
                mode="inherit",
                provider_id=provider_id if provider_id in PRESET_BY_ID else "openai_compatible",
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                model=chat_model,
                thinking_mode="",
                reasoning_effort=chat_reasoning_effort,
                max_output_tokens=chat_max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
    elif isinstance(subagent_raw, dict):
        raw_sub_mode = str(
            subagent_raw.get("mode") or (existing_subagent.mode if existing_subagent else "inherit")
        ).strip().lower()
        subagent_mode = raw_sub_mode if raw_sub_mode in {"inherit", "shared_service", "independent"} else "inherit"

        raw_sub_thinking = str(subagent_raw.get("thinkingMode") or subagent_raw.get("thinking_mode") or "").strip()
        subagent_thinking_mode = normalize_thinking_mode(raw_sub_thinking)
        if raw_sub_thinking and not subagent_thinking_mode:
            raise ValueError("model_service_subagent_thinking_mode_invalid")

        raw_sub_reasoning = str(subagent_raw.get("reasoningEffort") or subagent_raw.get("reasoning_effort") or "").strip()
        subagent_reasoning_effort = normalize_reasoning_effort(raw_sub_reasoning)
        if raw_sub_reasoning and not subagent_reasoning_effort:
            raise ValueError("model_service_subagent_reasoning_effort_invalid")

        subagent_max_tokens = _bounded_int(
            subagent_raw.get("maxOutputTokens", subagent_raw.get("max_output_tokens")),
            default=0,
            minimum=0,
            maximum=131072,
        )
        subagent_timeout = _bounded_int(
            subagent_raw.get("timeoutSeconds", subagent_raw.get("timeout_seconds")),
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=5,
            maximum=600,
        )
        subagent_model = normalize_provider_model_id(
            subagent_raw.get("model") or subagent_raw.get("chat_model") or ""
        )

        resolved_main_provider = provider_id if provider_id in PRESET_BY_ID else "openai_compatible"
        if subagent_mode == "inherit":
            subagent_settings = SubagentModelSettings(
                mode="inherit",
                provider_id=resolved_main_provider,
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                model=chat_model,
                thinking_mode="",
                reasoning_effort=chat_reasoning_effort,
                max_output_tokens=chat_max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        elif subagent_mode == "shared_service":
            subagent_settings = SubagentModelSettings(
                mode="shared_service",
                provider_id=resolved_main_provider,
                protocol=protocol,
                base_url=base_url,
                api_key=api_key,
                model=subagent_model,
                thinking_mode=subagent_thinking_mode,
                reasoning_effort=subagent_reasoning_effort,
                max_output_tokens=subagent_max_tokens,
                timeout_seconds=subagent_timeout,
            )
        else:
            sub_provider_id = str(
                subagent_raw.get("providerId") or subagent_raw.get("provider_id") or "openai_compatible"
            ).strip()
            if sub_provider_id not in PRESET_BY_ID:
                sub_provider_id = "openai_compatible"
            sub_preset = PRESET_BY_ID.get(sub_provider_id, PRESET_BY_ID["openai_compatible"])
            sub_protocol = normalize_api_protocol(
                protocol=str(subagent_raw.get("protocol") or sub_preset.protocol),
                base_url=str(subagent_raw.get("baseUrl") or subagent_raw.get("base_url") or sub_preset.base_url),
            )
            sub_base_url = _normalize_configured_base_url(
                str(subagent_raw.get("baseUrl") or subagent_raw.get("base_url") or sub_preset.base_url),
                protocol=sub_protocol,
            )
            sub_api_key = str(subagent_raw.get("apiKey") or subagent_raw.get("api_key") or "").strip()
            if not sub_api_key and not bool(subagent_raw.get("clearApiKey") or subagent_raw.get("clear_api_key")):
                if existing_subagent and existing_subagent.mode == "independent":
                    same_prov = sub_provider_id == existing_subagent.provider_id
                    same_url = sub_base_url.rstrip("/") == existing_subagent.base_url.rstrip("/")
                    if same_prov and same_url:
                        sub_api_key = existing_subagent.api_key

            subagent_settings = SubagentModelSettings(
                mode="independent",
                provider_id=sub_provider_id,
                protocol=sub_protocol,
                base_url=sub_base_url,
                api_key=sub_api_key,
                model=subagent_model,
                thinking_mode=subagent_thinking_mode,
                reasoning_effort=subagent_reasoning_effort,
                max_output_tokens=subagent_max_tokens,
                timeout_seconds=subagent_timeout,
            )
    else:
        subagent_settings = SubagentModelSettings(mode="inherit")

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
        vision_api_key=vision_api_key,
        vision_base_url=vision_base_url,
        vision_api_protocol=vision_api_protocol,
        standalone_vision=(
            _bool_value(raw.get("standaloneVision", raw.get("standalone_vision")), False)
            if raw.get("standaloneVision", raw.get("standalone_vision")) is not None else None
        ),
        timeout_seconds=timeout_seconds,
        chat_thinking_mode=chat_thinking_mode,
        chat_reasoning_effort=chat_reasoning_effort,
        chat_max_output_tokens=chat_max_output_tokens,
        subagent=subagent_settings,
    )
    if validate_primary:
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
    api_key = str(getattr(config_module, "CHAT_API_KEY", "") or "").strip()
    chat_model = str(getattr(config_module, "CHAT_MODEL_NAME", "") or "").strip()
    chat_reasoning_effort = normalize_reasoning_effort(
        getattr(config_module, "LLM_CHAT_REASONING_EFFORT", "")
    )
    chat_max_output_tokens = max(
        0,
        int(getattr(config_module, "LLM_CHAT_MAX_OUTPUT_TOKENS", 0) or 0),
    )
    return ModelServiceSettings(
        provider_id=provider_id,
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        chat_model=chat_model,
        use_for_vision=bool(getattr(config_module, "VISION_ENABLED", True) and vision_model),
        use_for_image_generation=bool(getattr(config_module, "IMAGE_GENERATION_ENABLED", False)),
        image_generation_api_key=str(getattr(config_module, "IMAGE_GENERATION_API_KEY", "") or "").strip(),
        image_generation_base_url=str(getattr(config_module, "IMAGE_GENERATION_BASE_URL", "") or "").strip(),
        image_generation_model=str(
            getattr(config_module, "IMAGE_GENERATION_MODEL", "gpt-image-2") or "gpt-image-2"
        ).strip(),
        vision_model=vision_model,
        vision_api_key=str(getattr(config_module, "VISION_API_KEY", "") or "").strip(),
        vision_base_url=str(getattr(config_module, "VISION_BASE_URL", "") or "").strip(),
        vision_api_protocol=str(getattr(config_module, "VISION_API_PROTOCOL", "") or "").strip(),
        standalone_vision=_vision_route_is_separate(config_module, prefix_style=True),
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        chat_thinking_mode=normalize_thinking_mode(getattr(config_module, "LLM_THINKING_MODE", "")),
        chat_reasoning_effort=chat_reasoning_effort,
        chat_max_output_tokens=chat_max_output_tokens,
        subagent=SubagentModelSettings(
            mode="inherit",
            provider_id=provider_id,
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            model=chat_model,
            thinking_mode="",
            reasoning_effort=chat_reasoning_effort,
            max_output_tokens=chat_max_output_tokens,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        ),
    )


def effective_settings_from_runtime_settings(settings: Any) -> ModelServiceSettings:
    base_url = str(getattr(settings, "chat_base_url", "") or "").strip()
    protocol = normalize_api_protocol(
        protocol=str(getattr(settings, "chat_api_protocol", "auto") or "auto"),
        base_url=base_url,
    )
    provider_id = infer_provider_id(protocol=protocol, base_url=base_url)
    api_key = str(getattr(settings, "chat_api_key", "") or "").strip()
    chat_model = str(getattr(settings, "chat_model_name", "") or "").strip()
    vision_model = str(getattr(settings, "vision_model_name", "") or "").strip()
    timeout_seconds = int(
        getattr(settings, "vision_request_timeout", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS
    )
    chat_reasoning_effort = normalize_reasoning_effort(
        getattr(settings, "llm_chat_reasoning_effort", "")
    )
    chat_max_output_tokens = max(
        0,
        int(getattr(settings, "llm_chat_max_output_tokens", 0) or 0),
    )
    return ModelServiceSettings(
        provider_id=provider_id,
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        chat_model=chat_model,
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
        vision_api_key=str(getattr(settings, "vision_api_key", "") or "").strip(),
        vision_base_url=str(getattr(settings, "vision_base_url", "") or "").strip(),
        vision_api_protocol=str(getattr(settings, "vision_api_protocol", "") or "").strip(),
        standalone_vision=_vision_route_is_separate(settings),
        timeout_seconds=timeout_seconds,
        chat_thinking_mode=normalize_thinking_mode(getattr(settings, "llm_thinking_mode", "")),
        chat_reasoning_effort=chat_reasoning_effort,
        chat_max_output_tokens=chat_max_output_tokens,
        subagent=SubagentModelSettings(
            mode="inherit",
            provider_id=provider_id,
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            model=chat_model,
            thinking_mode="",
            reasoning_effort=chat_reasoning_effort,
            max_output_tokens=chat_max_output_tokens,
            timeout_seconds=timeout_seconds,
        ),
    )


def _vision_route_is_separate(settings: Any, *, prefix_style: bool = False) -> bool:
    def value(role: str, field: str) -> str:
        name = f"{role}_{field}".upper() if prefix_style else f"{role}_{field}".lower()
        return str(getattr(settings, name, "") or "").strip().rstrip("/")
    return bool(value("vision", "base_url")) and any(
        value("vision", field) != value("chat", field)
        for field in ("base_url", "api_protocol", "api_key")
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
    sub = settings.subagent
    has_subagent_api_key = bool(sub.api_key) if sub.mode == "independent" else False
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
        "visionModel": settings.vision_model,
        "visionConfigured": settings.vision_configured,
        "standaloneVision": settings.uses_standalone_vision,
        "hasVisionApiKey": bool(settings.vision_api_key),
        "visionBaseUrl": settings.vision_base_url,
        "visionApiProtocol": settings.vision_api_protocol,
        "useForImageGeneration": settings.use_for_image_generation,
        "hasImageGenerationApiKey": bool(settings.image_generation_api_key),
        "imageGenerationBaseUrl": settings.image_generation_base_url,
        "imageGenerationModel": settings.image_generation_model,
        "timeoutSeconds": settings.timeout_seconds,
        "chatThinkingMode": settings.chat_thinking_mode,
        "chatReasoningEffort": settings.chat_reasoning_effort,
        "chatMaxOutputTokens": settings.chat_max_output_tokens,
        "subagent": {
            "mode": sub.mode,
            "providerId": sub.provider_id,
            "protocol": sub.protocol,
            "baseUrl": sub.base_url,
            "hasApiKey": has_subagent_api_key,
            "model": sub.model,
            "thinkingMode": sub.thinking_mode,
            "reasoningEffort": sub.reasoning_effort,
            "maxOutputTokens": sub.max_output_tokens,
            "timeoutSeconds": sub.timeout_seconds,
            "configured": sub.configured,
        },
        "providers": provider_presets_payload(),
    }


def resolve_subagent_candidate_settings(
    settings: ModelServiceSettings,
    *,
    require_model: bool = True,
) -> ModelServiceSettings:
    sub = settings.subagent
    if sub.mode == "inherit":
        validate_model_service_settings(settings, require_model=require_model)
        return settings
    if sub.mode == "shared_service":
        candidate = ModelServiceSettings(
            provider_id=settings.provider_id,
            protocol=settings.protocol,
            base_url=settings.base_url,
            api_key=settings.api_key,
            chat_model=sub.model,
            timeout_seconds=sub.timeout_seconds,
            chat_thinking_mode=sub.thinking_mode,
            chat_reasoning_effort=sub.reasoning_effort,
            chat_max_output_tokens=sub.max_output_tokens,
        )
        validate_model_service_settings(candidate, require_model=require_model)
        return candidate
    candidate = ModelServiceSettings(
        provider_id=sub.provider_id,
        protocol=sub.protocol,
        base_url=sub.base_url,
        api_key=sub.api_key,
        chat_model=sub.model,
        timeout_seconds=sub.timeout_seconds,
        chat_thinking_mode=sub.thinking_mode,
        chat_reasoning_effort=sub.reasoning_effort,
        chat_max_output_tokens=sub.max_output_tokens,
    )
    validate_model_service_settings(candidate, require_model=require_model)
    return candidate


def resolve_vision_candidate_settings(
    settings: ModelServiceSettings, *, require_model: bool = True,
) -> ModelServiceSettings:
    if not settings.use_for_vision:
        raise ValueError("model_service_vision_disabled")
    separate = settings.uses_standalone_vision
    candidate = ModelServiceSettings(
        provider_id=settings.provider_id,
        protocol=settings.vision_api_protocol if separate else settings.protocol,
        base_url=settings.vision_base_url if separate else settings.base_url,
        api_key=settings.vision_api_key if separate else settings.api_key,
        chat_model=settings.vision_model or ("" if separate else settings.chat_model),
        timeout_seconds=settings.timeout_seconds,
        chat_thinking_mode="" if separate else settings.chat_thinking_mode,
        chat_reasoning_effort="" if separate else settings.chat_reasoning_effort,
    )
    validate_model_service_settings(candidate, require_model=require_model)
    return candidate


def apply_model_service_settings(config_module: Any, settings: ModelServiceSettings) -> None:
    for prefix in ("TEXT", "AUX", "CHAT"):
        setattr(config_module, f"{prefix}_API_KEY", settings.api_key)
        setattr(config_module, f"{prefix}_BASE_URL", settings.base_url)
        setattr(config_module, f"{prefix}_MODEL_NAME", settings.chat_model)
        setattr(config_module, f"{prefix}_API_PROTOCOL", settings.protocol)
    setattr(config_module, "LLM_CHAT_REASONING_EFFORT", settings.chat_reasoning_effort)
    if settings.chat_thinking_mode:
        setattr(config_module, "LLM_THINKING_MODE", settings.chat_thinking_mode)
    setattr(config_module, "LLM_CHAT_MAX_OUTPUT_TOKENS", settings.chat_max_output_tokens)
    setattr(config_module, "IMAGE_GENERATION_ENABLED", settings.use_for_image_generation)
    setattr(config_module, "IMAGE_GENERATION_API_KEY", settings.image_generation_api_key)
    setattr(config_module, "IMAGE_GENERATION_BASE_URL", settings.image_generation_base_url)
    setattr(config_module, "IMAGE_GENERATION_MODEL", settings.image_generation_model)

    setattr(config_module, "VISION_ENABLED", settings.use_for_vision)
    if settings.use_for_vision:
        if settings.uses_standalone_vision:
            # 独立视觉 provider：chat 与 vision 走不同服务商时，
            # 视觉路由使用自己的 key/base_url/protocol，不再复用 chat。
            setattr(config_module, "VISION_API_KEY", settings.vision_api_key)
            setattr(config_module, "VISION_BASE_URL", settings.vision_base_url)
            setattr(config_module, "VISION_MODEL_NAME", settings.vision_model)
            setattr(config_module, "VISION_API_PROTOCOL", settings.vision_api_protocol or settings.protocol)
        else:
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


def test_model_service(settings: ModelServiceSettings, *, image_url: str = "") -> str:
    validate_model_service_settings(settings)
    client = build_llm_client(
        api_key=settings.api_key,
        base_url=settings.base_url,
        protocol=settings.protocol,
        timeout=float(settings.timeout_seconds),
        max_retries=0,
    )
    # Exercise the same reasoning controls as real requests, including the
    # child's thinking mode; eight output tokens cannot test a reasoning model.
    from .model_runtime.models import ModelBundle
    from .model_runtime.request_policy import RequestPolicyMixin
    from .model_runtime.targets import ModelTargetsMixin
    from .runtime_settings import BotSettingsView

    class TestRequestPolicy(RequestPolicyMixin, ModelTargetsMixin):
        def _settings_view(self) -> BotSettingsView:
            return BotSettingsView(
                llm_thinking_mode=settings.chat_thinking_mode,
                llm_reasoning_effort=settings.chat_reasoning_effort,
            )

    controls = TestRequestPolicy()._build_reasoning_control_kwargs(
        bundle=ModelBundle(client=client, model=settings.chat_model)
    )
    prompt = "Describe the main color in this image in a few words." if image_url else "Reply with only OK."
    if settings.protocol == "responses":
        response = client.responses.create(
            model=settings.chat_model,
            input=([{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_url},
            ]}] if image_url else prompt),
            max_output_tokens=4096,
            store=False,
            **controls,
        )
        text = getattr(response, "output_text", "")
    else:
        response = client.chat.completions.create(
            model=settings.chat_model,
            messages=[{"role": "user", "content": ([
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ] if image_url else prompt)}],
            max_tokens=4096,
            **controls,
        )
        try:
            text = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError("model_service_response_invalid") from exc
    result = str(text or "").strip()
    if not result:
        raise RuntimeError("model_service_response_empty")
    return result


def test_vision_model_service(settings: ModelServiceSettings) -> str:
    import base64
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))

    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
    image += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32)) + chunk(b"IEND", b"")
    return test_model_service(settings, image_url="data:image/png;base64," + base64.b64encode(image).decode("ascii"))


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
    if settings.protocol == "gemini" and settings.chat_model:
        gemini_thinking_config(settings.chat_model, settings.chat_reasoning_effort)


def redact_provider_error(error: Exception, *, api_key: str | Any = "") -> str:
    text = str(error or "").strip() or error.__class__.__name__
    keys: list[str] = []
    if isinstance(api_key, str):
        if api_key:
            keys.append(api_key)
    elif hasattr(api_key, "__iter__"):
        for item in api_key:
            if isinstance(item, str) and item:
                keys.append(item)
    for key in keys:
        text = text.replace(key, "<redacted>")
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
