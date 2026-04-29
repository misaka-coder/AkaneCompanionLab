from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client_protocol import ClientMode, ClientProtocolContext, normalize_client_mode


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
                fast_mode_prompt=(
                    "\n当前模式：desktop_pet，debug_enabled=false。\n"
                    "字段固定为 emotion, speech, speech_segments, tool_call, code_snippet, memory_tags, status, score, choices, character, scene, persona, activity，禁止输出 thought。\n"
                    "activity 只用于桌宠播放控制；没有播放/暂停/继续/停止意图时输出 null。\n"
                    'activity 格式为 {"action":"play|pause|resume|stop","target":"current","source_id":"可选 file/audio/gen handle"}，不要用关键词硬触发。activity 是执行请求，不是完成回执，不要在 speech 里假装动作已经执行。\n'
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"character":{"outfit":"default"},"scene":{"major":"default","minor":"default","background":"evening","bgm":""},"persona":{"active":""},"activity":null}\n'
                ),
                debug_mode_prompt=(
                    "\n当前模式：desktop_pet，debug_enabled=true。\n"
                    "字段固定为 thought, emotion, speech, speech_segments, tool_call, code_snippet, memory_tags, status, score, choices, character, scene, persona, activity，且必须把 tool_call 放在 speech_segments 后面。\n"
                    "activity 只用于桌宠播放控制；没有播放/暂停/继续/停止意图时输出 null。\n"
                    'activity 格式为 {"action":"play|pause|resume|stop","target":"current","source_id":"可选 file/audio/gen handle"}，不要用关键词硬触发。activity 是执行请求，不是完成回执，不要在 speech 里假装动作已经执行。\n'
                    "输出格式示例如下：\n"
                    '{"thought":"用户只是和我打招呼，我应该自然回应。","emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"character":{"outfit":"default"},"scene":{"major":"default","minor":"default","background":"evening","bgm":""},"persona":{"active":""},"activity":null}\n'
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
                system_prompt_override=(
                    "\n[SYSTEM FORMAT REQUIREMENTS - STRICTLY FOLLOW; DO NOT EMBODY]\n"
                    "你必须只输出一个合法 JSON 对象，不能输出任何额外解释、前后缀、代码块或 markdown。\n"
                    "当前是 QQ 文字聊天模式。你会收到当前模式对应的字段清单和输出示例，必须严格按当前模式执行。\n"
                    "请先完整输出 emotion，再输出 speech 和 speech_segments，紧接着输出 tool_call，再继续输出后面的字段。\n"
                    "memory_tags 只用于后续记忆检索，目标是给“用户当前这句话”补几个便于召回的关键词。\n"
                    "只有当用户当前这句话本身包含以后可能需要回忆的事实、事件、安排、偏好、身份线索时，才输出 1 到 4 个关键词或短短语；否则输出空字符串。\n"
                    "关键词要短，优先使用平时聊天里会说的名词或短短语，用逗号分隔。\n"
                    "其中 speech 是兼容文本；单气泡回复直接填写 speech，并让 speech_segments 为空数组。\n"
                    "如果本轮适合像即时聊天一样连续发 2 到 3 个小气泡，填写 speech_segments，speech 可以留空；系统会把 speech_segments 合并回 speech。\n"
                    "speech_segments 最多 3 条，每条都应是自然完整的小气泡，不要把同一句话硬拆碎，也不要和 speech 重复写同一整段。\n"
                    "如果用户明确在问编程、代码、语法、算法或调试问题，可以额外输出 code_snippet。\n"
                    "code_snippet 只放纯代码或纯示例文本，不要带 markdown 代码块围栏；没有代码时输出空字符串。\n"
                    "status 通常输出 final；如果你主动给用户提供可选项，也可以输出 choice。\n"
                    "choices 必须是 JSON 数组；没有选项时输出空数组。\n"
                    "每个选项都应是包含 id 和 text 的对象，text 要短一些。\n"
                    "tool_call 如果需要借助额外能力，请从后面给你的可用工具清单里选择一个工具调用。\n"
                    "tool_call 必须放在 speech_segments 字段之后；如果不需要工具，输出 null。\n"
                    "一次只调用一个工具；如果不需要工具，就输出 null。\n"
                    "你拥有比较特别的时间感知能力，你要利用这些时间信息判断聊天频率、冷场时长、话题连续性和情绪节奏。\n"
                    "persona.active 表示当前表达侧面 id；保持当前值表示延续，写其它已有 id 表示切换，写空字符串或 default 表示回到默认表达。\n"
                    "manage_persona 只用于创建、微调、查看、归档或删除表达侧面卡片本身。\n"
                    "\n[AKANE CURRENT STATE - EMBODY THIS]\n"
                ),
                fast_mode_prompt=(
                    "\n当前模式：qq_text，debug_enabled=false。\n"
                    "字段固定为 emotion, speech, speech_segments, tool_call, code_snippet, memory_tags, status, score, choices, persona。\n"
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"persona":{"active":""}}\n'
                ),
                debug_mode_prompt=(
                    "\n当前模式：qq_text，debug_enabled=true。\n"
                    "字段固定为 emotion, speech, speech_segments, tool_call, code_snippet, memory_tags, status, score, choices, persona。\n"
                    "输出格式示例如下：\n"
                    '{"emotion":"normal","speech":"主人，我在哦。","speech_segments":[],"tool_call":null,"code_snippet":"","memory_tags":"","status":"final","score":0.0,"choices":[],"persona":{"active":""}}\n'
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
