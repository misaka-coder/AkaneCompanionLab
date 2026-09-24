from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    build_scene_static_system_prompt,
    build_system_prompt,
    strip_care_prompt_contract,
)


class PromptModule(str, Enum):
    EXTRA_CONTEXT = "extra_context"
    CURRENT_VISUAL_STATE = "current_visual_state"
    SCENE_OBSERVATION = "scene_observation"
    OUTFIT_OBSERVATION = "outfit_observation"
    RESOURCE_MANIFEST = "resource_manifest"
    PERSONA = "persona"
    DOMAIN_PROFILE = "domain_profile"
    TOOLS = "tools"


SCENE_STATIC_PROMPT_MODULES = (
    PromptModule.EXTRA_CONTEXT.value,
    PromptModule.CURRENT_VISUAL_STATE.value,
    PromptModule.SCENE_OBSERVATION.value,
    PromptModule.OUTFIT_OBSERVATION.value,
    PromptModule.RESOURCE_MANIFEST.value,
    PromptModule.PERSONA.value,
    PromptModule.TOOLS.value,
)


QQ_TEXT_MODE_PROMPT = """
当前模式：qq_text。
常规回复：
{"emotion":"normal","reply_medium":"text","speech":"主人，我在哦。"}

字段含义：
- emotion：当前表达情绪。
- reply_medium：QQ 自动投递偏好，可填 text、voice 或 both。代码、长解释、列表和文件说明使用 text；适合朗读的短句可使用 voice 或 both。`qq.reply_delivery` 已固定为 text、voice 或 both 时遵循宿主设置。
- speech：发送给用户的完整消息正文。

整条响应只输出 JSON 对象；要发送的话只写在 speech 字段内，不要直接输出裸文本。

不需要文字时输出：
{"speech":""}

有真实作用时，可按对应字段说明追加 memory_metadata 或本轮明确提供的其它扩展字段。
""".strip()


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
                system_prompt_override=build_scene_static_system_prompt(),
            ),
            ClientMode.SCENE_LIVE2D: PromptProfile(
                id=ClientMode.SCENE_LIVE2D.value,
                mode=ClientMode.SCENE_LIVE2D,
                modules=(
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.CURRENT_VISUAL_STATE.value,
                    PromptModule.SCENE_OBSERVATION.value,
                    PromptModule.OUTFIT_OBSERVATION.value,
                    PromptModule.RESOURCE_MANIFEST.value,
                    PromptModule.PERSONA.value,
                    PromptModule.TOOLS.value,
                ),
                system_block_ids=SCENE_LIVE2D_SYSTEM_BLOCKS,
                system_prompt_override=build_scene_static_system_prompt(),
            ),
            ClientMode.DESKTOP_PET: PromptProfile(
                id=ClientMode.DESKTOP_PET.value,
                mode=ClientMode.DESKTOP_PET,
                modules=(
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.CURRENT_VISUAL_STATE.value,
                    PromptModule.RESOURCE_MANIFEST.value,
                    PromptModule.OUTFIT_OBSERVATION.value,
                    PromptModule.PERSONA.value,
                    PromptModule.TOOLS.value,
                ),
                system_block_ids=DESKTOP_PET_SYSTEM_BLOCKS,
                system_prompt_override=build_desktop_pet_system_prompt(),
                fast_mode_prompt=(
                    "当前模式：desktop_pet。\n"
                    "常规回复：\n"
                    '{"emotion":"normal","speech":"主人，我在哦。"}\n'
                    '需要明确控制本地或系统音乐时，才追加 activity，例如 {"action":"next","target":"current"}；不要根据用户措辞在宿主侧猜测动作。\n'
                    "其它字段仅在有真实作用时按上文定义追加；没有作用的字段省略。"
                ),
                debug_mode_prompt=(
                    "当前模式：desktop_pet（调试输出）。\n"
                    "常规回复：\n"
                    '{"thought":"用户只是和我打招呼，我应该自然回应。","emotion":"normal","speech":"主人，我在哦。"}\n'
                    '需要明确控制本地或系统音乐时，才追加 activity，例如 {"action":"next","target":"current"}；不要根据用户措辞在宿主侧猜测动作。\n'
                    "其它字段仅在有真实作用时按上文定义追加；没有作用的字段省略。"
                ),
            ),
            ClientMode.QQ_TEXT: PromptProfile(
                id=ClientMode.QQ_TEXT.value,
                mode=ClientMode.QQ_TEXT,
                modules=(
                    PromptModule.EXTRA_CONTEXT.value,
                    PromptModule.PERSONA.value,
                    PromptModule.DOMAIN_PROFILE.value,
                    PromptModule.TOOLS.value,
                ),
                supports_thought_debug=False,
                system_block_ids=QQ_TEXT_SYSTEM_BLOCKS,
                system_prompt_override=build_qq_text_system_prompt(),
                fast_mode_prompt=QQ_TEXT_MODE_PROMPT,
                debug_mode_prompt=QQ_TEXT_MODE_PROMPT,
            ),
        }

        self._care_disabled_profiles = {
            mode: self._without_care(profile)
            for mode, profile in self._profiles.items()
        }

    @staticmethod
    def _without_care(profile: PromptProfile) -> PromptProfile:
        system_block_ids = tuple(
            block_id for block_id in profile.system_block_ids if block_id not in {"state_request", "care_runtime"}
        )
        return replace(
            profile,
            system_block_ids=system_block_ids,
            system_prompt_override=build_system_prompt(*system_block_ids),
            fast_mode_prompt=strip_care_prompt_contract(profile.fast_mode_prompt),
            debug_mode_prompt=strip_care_prompt_contract(profile.debug_mode_prompt),
        )

    def resolve(self, client_context: ClientProtocolContext | None, *, care_enabled: bool = True) -> PromptProfile:
        mode = ClientMode.SCENE_STATIC
        if client_context is not None:
            mode = client_context.effective_mode
        profiles = self._profiles if care_enabled else self._care_disabled_profiles
        profile = profiles.get(mode) or profiles[ClientMode.SCENE_STATIC]
        if client_context is not None and client_context.has_capability("scene_presentation_v1"):
            from .scene.presentation.model_contract import scene_prompt_profile
            return scene_prompt_profile(profile)
        return profile

    def get(self, mode: ClientMode | str, *, care_enabled: bool = True) -> PromptProfile:
        key = mode if isinstance(mode, ClientMode) else normalize_client_mode(mode)
        profiles = self._profiles if care_enabled else self._care_disabled_profiles
        return profiles.get(key) or profiles[ClientMode.SCENE_STATIC]
