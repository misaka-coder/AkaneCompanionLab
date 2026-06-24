from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client_protocol import ClientMode, ClientProtocolContext, normalize_client_mode
from .prompt_blocks import (
    DESKTOP_PET_SYSTEM_BLOCKS,
    QQ_TEXT_SYSTEM_BLOCKS,
    SCENE_LIVE2D_SYSTEM_BLOCKS,
    SCENE_STATIC_SYSTEM_BLOCKS,
    build_desktop_pet_system_prompt,
    build_qq_text_system_prompt,
)


class PromptModule(str, Enum):
    CLIENT_MODE = "client_mode"
    EXTRA_CONTEXT = "extra_context"
    CURRENT_VISUAL_STATE = "current_visual_state"
    SCENE_OBSERVATION = "scene_observation"
    OUTFIT_OBSERVATION = "outfit_observation"
    RESOURCE_MANIFEST = "resource_manifest"
    PENDING_GIFTS = "pending_gifts"
    FOCUSED_GIFT_OBSERVATION = "focused_gift_observation"
    PERSONA = "persona"
    TOOLS = "tools"


SCENE_STATIC_PROMPT_MODULES = (
    PromptModule.CLIENT_MODE.value,
    PromptModule.EXTRA_CONTEXT.value,
    PromptModule.CURRENT_VISUAL_STATE.value,
    PromptModule.SCENE_OBSERVATION.value,
    PromptModule.OUTFIT_OBSERVATION.value,
    PromptModule.RESOURCE_MANIFEST.value,
    PromptModule.PENDING_GIFTS.value,
    PromptModule.FOCUSED_GIFT_OBSERVATION.value,
    PromptModule.PERSONA.value,
    PromptModule.TOOLS.value,
)


@dataclass(frozen=True)
class PromptProfile:
    id: str
    mode: ClientMode
    modules: tuple[str, ...] = field(default_factory=tuple)
    system_block_ids: tuple[str, ...] = field(default_factory=tuple)
    fallback_profile_id: str = ""
    system_prompt_override: str = ""
    fast_mode_prompt: str = ""
    debug_mode_prompt: str = ""
    supports_thought_debug: bool = True

    def includes(self, module: PromptModule | str) -> bool:
        key = module.value if isinstance(module, PromptModule) else str(module or "").strip()
        return key in set(self.modules)

    def mode_prompt_override(self, *, debug_enabled: bool) -> str:
        return self.debug_mode_prompt if debug_enabled else self.fast_mode_prompt

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode.value,
            "modules": list(self.modules),
            "system_block_ids": list(self.system_block_ids),
            "fallback_profile_id": self.fallback_profile_id,
            "supports_thought_debug": self.supports_thought_debug,
        }


class PromptProfileRegistry:
    def __init__(self) -> None:
        self._profiles: dict[ClientMode, PromptProfile] = {
            ClientMode.SCENE_STATIC: PromptProfile(
                id=ClientMode.SCENE_STATIC.value,
                mode=ClientMode.SCENE_STATIC,
                modules=SCENE_STATIC_PROMPT_MODULES,
                system_block_ids=SCENE_STATIC_SYSTEM_BLOCKS,
            ),
            ClientMode.SCENE_LIVE2D: PromptProfile(
                id=ClientMode.SCENE_LIVE2D.value,
                mode=ClientMode.SCENE_LIVE2D,
                modules=(
                    PromptModule.CLIENT_MODE.value,
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.CURRENT_VISUAL_STATE.value,
                    PromptModule.SCENE_OBSERVATION.value,
                    PromptModule.OUTFIT_OBSERVATION.value,
                    PromptModule.RESOURCE_MANIFEST.value,
                    PromptModule.PENDING_GIFTS.value,
                    PromptModule.FOCUSED_GIFT_OBSERVATION.value,
                    PromptModule.PERSONA.value,
                    PromptModule.TOOLS.value,
                ),
                system_block_ids=SCENE_LIVE2D_SYSTEM_BLOCKS,
            ),
            ClientMode.DESKTOP_PET: PromptProfile(
                id=ClientMode.DESKTOP_PET.value,
                mode=ClientMode.DESKTOP_PET,
                modules=(
                    PromptModule.CLIENT_MODE.value,
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.CURRENT_VISUAL_STATE.value,
                    PromptModule.RESOURCE_MANIFEST.value,
                    PromptModule.OUTFIT_OBSERVATION.value,
                    PromptModule.PENDING_GIFTS.value,
                    PromptModule.FOCUSED_GIFT_OBSERVATION.value,
                    PromptModule.PERSONA.value,
                    PromptModule.TOOLS.value,
                ),
                system_block_ids=DESKTOP_PET_SYSTEM_BLOCKS,
                system_prompt_override=build_desktop_pet_system_prompt(),
                fast_mode_prompt=(
                    "\n当前模式：desktop_pet，debug_enabled=false。\n"
                    "字段固定为 emotion, speech, speech_segments, tool_call, status, choices, persona, activity, memory_metadata, state_request，禁止输出 thought、character、scene。\n"
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"status":"final","choices":[],"persona":{"active":""},"activity":null,"memory_metadata":{"keywords":[],"subject_scopes":[],"categories":[],"importance":0.0,"confidence":0.0},"state_request":null}\n'
                ),
                debug_mode_prompt=(
                    "\n当前模式：desktop_pet，debug_enabled=true。\n"
                    "字段固定为 thought, emotion, speech, speech_segments, tool_call, status, choices, persona, activity, memory_metadata, state_request，且必须把 tool_call 放在 speech_segments 后面；禁止输出 character、scene。\n"
                    "输出格式示例如下：\n"
                    '{"thought":"用户只是和我打招呼，我应该自然回应。","emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"status":"final","choices":[],"persona":{"active":""},"activity":null,"memory_metadata":{"keywords":[],"subject_scopes":[],"categories":[],"importance":0.0,"confidence":0.0},"state_request":null}\n'
                ),
            ),
            ClientMode.QQ_TEXT: PromptProfile(
                id=ClientMode.QQ_TEXT.value,
                mode=ClientMode.QQ_TEXT,
                modules=(
                    PromptModule.CLIENT_MODE.value,
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.PENDING_GIFTS.value,
                    PromptModule.PERSONA.value,
                    PromptModule.TOOLS.value,
                ),
                supports_thought_debug=False,
                system_block_ids=QQ_TEXT_SYSTEM_BLOCKS,
                system_prompt_override=build_qq_text_system_prompt(),
                fast_mode_prompt=(
                    "\n当前模式：qq_text，debug_enabled=false。\n"
                    "字段固定为 emotion, reply_medium, speech, speech_segments, tool_call, status, choices, persona, memory_metadata, state_request。\n"
                    'reply_medium 只用于 QQ 自动回复模式的投递偏好，只能是 "text"、"voice" 或 "both"；代码、长解释、列表和文件说明优先 text，短句、亲密私聊或用户发来语音时可用 voice。\n'
                    "必须先输出 emotion，再输出 reply_medium，然后才输出 speech 和 speech_segments，便于 QQ 端尽早决定是否合成语音。\n"
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","reply_medium":"text","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"status":"final","choices":[],"persona":{"active":""},"memory_metadata":{"keywords":[],"subject_scopes":[],"categories":[],"importance":0.0,"confidence":0.0},"state_request":null}\n'
                ),
                debug_mode_prompt=(
                    "\n当前模式：qq_text，debug_enabled=true。\n"
                    "字段固定为 emotion, reply_medium, speech, speech_segments, tool_call, status, choices, persona, memory_metadata, state_request。\n"
                    'reply_medium 只用于 QQ 自动回复模式的投递偏好，只能是 "text"、"voice" 或 "both"；代码、长解释、列表和文件说明优先 text，短句、亲密私聊或用户发来语音时可用 voice。\n'
                    "必须先输出 emotion，再输出 reply_medium，然后才输出 speech 和 speech_segments，便于 QQ 端尽早决定是否合成语音。\n"
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","reply_medium":"text","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"status":"final","choices":[],"persona":{"active":""},"memory_metadata":{"keywords":[],"subject_scopes":[],"categories":[],"importance":0.0,"confidence":0.0},"state_request":null}\n'
                ),
            ),
        }

    def resolve(self, client_context: ClientProtocolContext | None) -> PromptProfile:
        mode = ClientMode.SCENE_STATIC
        if client_context is not None:
            mode = client_context.effective_mode
        return self._profiles.get(mode) or self._profiles[ClientMode.SCENE_STATIC]

    def get(self, mode: ClientMode | str) -> PromptProfile:
        key = mode if isinstance(mode, ClientMode) else normalize_client_mode(mode)
        return self._profiles.get(key) or self._profiles[ClientMode.SCENE_STATIC]
