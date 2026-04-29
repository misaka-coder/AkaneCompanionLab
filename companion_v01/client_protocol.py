from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClientMode(str, Enum):
    SCENE_STATIC = "scene_static"
    SCENE_LIVE2D = "scene_live2d"
    DESKTOP_PET = "desktop_pet"
    QQ_TEXT = "qq_text"


class ClientCapability(str, Enum):
    SPEECH_SEGMENTS = "speech_segments"
    BACKGROUND = "background"
    BGM = "bgm"
    STATIC_SPRITE = "static_sprite"
    LIVE2D = "live2d"
    DESKTOP_CONTEXT = "desktop_context"
    SCREEN_VISION = "screen_vision"
    TOUCH_EVENT = "touch_event"
    FILE_DROP = "file_drop"
    AUDIO_PLAYBACK = "audio_playback"
    TTS = "tts"
    CHOICES = "choices"
    TOOL_ACTIONS = "tool_actions"


SCENE_STATIC_DEFAULT_CAPABILITIES = (
    ClientCapability.SPEECH_SEGMENTS.value,
    ClientCapability.BACKGROUND.value,
    ClientCapability.BGM.value,
    ClientCapability.STATIC_SPRITE.value,
    ClientCapability.AUDIO_PLAYBACK.value,
    ClientCapability.TTS.value,
    ClientCapability.CHOICES.value,
    ClientCapability.TOOL_ACTIONS.value,
)

SCENE_LIVE2D_DEFAULT_CAPABILITIES = (
    ClientCapability.SPEECH_SEGMENTS.value,
    ClientCapability.BACKGROUND.value,
    ClientCapability.BGM.value,
    ClientCapability.LIVE2D.value,
    ClientCapability.AUDIO_PLAYBACK.value,
    ClientCapability.TTS.value,
    ClientCapability.CHOICES.value,
    ClientCapability.TOOL_ACTIONS.value,
)

DESKTOP_PET_DEFAULT_CAPABILITIES = (
    ClientCapability.SPEECH_SEGMENTS.value,
    ClientCapability.LIVE2D.value,
    ClientCapability.DESKTOP_CONTEXT.value,
    ClientCapability.SCREEN_VISION.value,
    ClientCapability.TOUCH_EVENT.value,
    ClientCapability.FILE_DROP.value,
    ClientCapability.AUDIO_PLAYBACK.value,
    ClientCapability.TTS.value,
    ClientCapability.CHOICES.value,
    ClientCapability.TOOL_ACTIONS.value,
)

QQ_TEXT_DEFAULT_CAPABILITIES = (
    ClientCapability.SPEECH_SEGMENTS.value,
    ClientCapability.FILE_DROP.value,
    ClientCapability.CHOICES.value,
    ClientCapability.TOOL_ACTIONS.value,
)


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def normalize_client_mode(value: Any, *, default: ClientMode = ClientMode.SCENE_STATIC) -> ClientMode:
    raw = str(value or "").strip().lower()
    if raw == "gal":
        raw = ClientMode.SCENE_STATIC.value
    for mode in ClientMode:
        if raw == mode.value:
            return mode
    return default


def default_capabilities_for_mode(mode: ClientMode) -> tuple[str, ...]:
    if mode == ClientMode.SCENE_LIVE2D:
        return SCENE_LIVE2D_DEFAULT_CAPABILITIES
    if mode == ClientMode.DESKTOP_PET:
        return DESKTOP_PET_DEFAULT_CAPABILITIES
    if mode == ClientMode.QQ_TEXT:
        return QQ_TEXT_DEFAULT_CAPABILITIES
    return SCENE_STATIC_DEFAULT_CAPABILITIES


def normalize_client_capabilities(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_items: list[str] = []
    if isinstance(value, dict):
        raw_items = [str(key).strip() for key, enabled in value.items() if enabled and str(key).strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.replace("，", ",").replace(";", ",").split(",") if item.strip()]

    if not raw_items:
        return tuple(default)

    allowed = {capability.value for capability in ClientCapability}
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        key = item.lower()
        if key not in allowed or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return tuple(normalized or default)


class ClientProtocolContext(BaseModel):
    requested_mode: ClientMode = ClientMode.SCENE_STATIC
    effective_mode: ClientMode = ClientMode.SCENE_STATIC
    capabilities: tuple[str, ...] = Field(default_factory=lambda: tuple(SCENE_STATIC_DEFAULT_CAPABILITIES))
    output_profile: str = ClientMode.SCENE_STATIC.value
    renderer_profile: str = ClientMode.SCENE_STATIC.value
    degraded_from: str = ""
    degrade_reason: str = ""

    class Config:
        use_enum_values = False

    def has_capability(self, capability: ClientCapability | str) -> bool:
        key = capability.value if isinstance(capability, ClientCapability) else str(capability or "").strip().lower()
        return key in set(self.capabilities)

    def to_public_dict(self) -> dict[str, Any]:
        data = model_to_dict(self)
        data["requested_mode"] = self.requested_mode.value
        data["effective_mode"] = self.effective_mode.value
        data["capabilities"] = list(self.capabilities)
        return data


class SceneState(BaseModel):
    major: str = ""
    minor: str = ""
    background_id: str = ""
    bgm_id: str = ""
    atmosphere_tags: list[str] = Field(default_factory=list)


class CharacterState(BaseModel):
    outfit_id: str = ""
    expression_id: str = ""
    sprite_id: str = ""


class Live2DState(BaseModel):
    model_id: str = ""
    expression_id: str = ""
    motion_id: str = ""
    attention: str = ""


class PetState(BaseModel):
    expression_id: str = ""
    motion_id: str = ""
    attention: str = ""
    bubble_priority: str = ""


class RuntimeState(BaseModel):
    last_scene: SceneState | None = None
    last_character: CharacterState | None = None
    last_live2d: Live2DState | None = None
    last_pet: PetState | None = None
    last_client_mode: ClientMode = ClientMode.SCENE_STATIC
    last_emotion: str = "normal"

    class Config:
        use_enum_values = False

    def to_public_dict(self) -> dict[str, Any]:
        data = model_to_dict(self)
        data["last_client_mode"] = self.last_client_mode.value
        return data
