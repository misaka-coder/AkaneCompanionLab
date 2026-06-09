from __future__ import annotations

from dataclasses import dataclass


CURRENT_ASSISTANT_STATE_MARKER = "[CURRENT ASSISTANT STATE - EMBODY THIS]"


COMMON_RESPONSE_BLOCKS = (
    "json_object_only",
    "mode_schema_contract",
    "field_order",
    "reply_bubbles",
    "code_snippet",
    "status_choices",
    "tool_call",
    "tool_execution_intent",
    "time_awareness",
    "persona_state",
    "memory_metadata",
)

SCENE_STATIC_SYSTEM_BLOCKS = (
    *COMMON_RESPONSE_BLOCKS,
    "scene_visual_resources",
    "current_assistant_state",
)

SCENE_LIVE2D_SYSTEM_BLOCKS = SCENE_STATIC_SYSTEM_BLOCKS

DESKTOP_PET_SYSTEM_BLOCKS = (
    *COMMON_RESPONSE_BLOCKS,
    "desktop_pet_visual",
    "desktop_pet_activity",
    "current_assistant_state",
)

QQ_TEXT_SYSTEM_BLOCKS = (
    *COMMON_RESPONSE_BLOCKS,
    "qq_text_mode",
    "current_assistant_state",
)


@dataclass(frozen=True)
class PromptBlock:
    id: str
    text: str


class PromptBlockRegistry:
    """Small composable prompt block registry.

    This keeps client profiles from copying large prompt strings while still
    letting each client choose only the rules it actually needs.
    """

    def __init__(self) -> None:
        self._blocks: dict[str, PromptBlock] = {
            "json_object_only": PromptBlock(
                id="json_object_only",
                text=(
                    "[SYSTEM FORMAT REQUIREMENTS - STRICTLY FOLLOW; DO NOT EMBODY]\n"
                    "你必须只输出一个合法 JSON 对象，不能输出任何额外解释、前后缀、代码块或 markdown。"
                ),
            ),
            "mode_schema_contract": PromptBlock(
                id="mode_schema_contract",
                text=(
                    "你会收到当前模式对应的字段清单和输出示例，必须严格按当前模式执行。"
                ),
            ),
            "field_order": PromptBlock(
                id="field_order",
                text=(
                    "请先完整输出 emotion，再输出 speech 和 speech_segments，紧接着输出 tool_call，再继续输出后面的字段。"
                ),
            ),
            "reply_bubbles": PromptBlock(
                id="reply_bubbles",
                text=(
                    "speech 是兼容文本；单气泡回复直接填写 speech，并让 speech_segments 为空数组。\n"
                    "如果本轮适合像即时聊天一样连续发 2 到 3 个小气泡，填写 speech_segments，speech 可以留空；系统会把 speech_segments 合并回 speech。\n"
                    "speech_segments 最多 3 条，每条都应是自然完整的小气泡，不要把同一句话硬拆碎，也不要和 speech 重复写同一整段。"
                ),
            ),
            "memory_metadata": PromptBlock(
                id="memory_metadata",
                text=(
                    "memory_metadata 只用于后台记忆入库，不会展示给用户。\n"
                    "如果当前用户消息没有值得长期检索的事实，keywords/subject_scopes/categories/mood_tags 输出空数组，importance 可以调低。\n"
                    "纯追问、核对或记忆测试本身新增信息少时，importance 可以调低。\n"
                    "keywords 写 0-4 个短词；subject_scopes 从 user, assistant, other 中选；categories 从 casual, preference, personal_profile, plan_goal, project_work, relationship, emotion_state, life_event, memory_query, system_meta 中选。\n"
                    "mood_tags 是你当时记住这件事时的情感余温，从 calm, warm, affectionate, happy, playful, curious, thoughtful, touched, proud, worried, lonely, sad, embarrassed, tense, annoyed, determined 中选 0-3 个。\n"
                    "拿不准可留空或多选；importance 和 confidence 必须是 0-1 数字。"
                ),
            ),
            "tool_call": PromptBlock(
                id="tool_call",
                text=(
                    "tool_call 如果需要借助额外能力，请从后面给你的可用工具清单里选择一个工具调用。\n"
                    "tool_call 必须放在 speech_segments 字段之后；如果不需要工具，输出 null。\n"
                    "一次只调用一个工具；如果不需要工具，就输出 null。"
                ),
            ),
            "code_snippet": PromptBlock(
                id="code_snippet",
                text=(
                    "如果用户明确在问编程、代码、语法、算法或调试问题，可以额外输出 code_snippet。\n"
                    "code_snippet 只放纯代码或纯示例文本，不要带 markdown 代码块围栏；没有代码时输出空字符串。\n"
                    "这类情况下，speech 负责自然解释，code_snippet 负责真正的示例。"
                ),
            ),
            "status_choices": PromptBlock(
                id="status_choices",
                text=(
                    "status 通常输出 final；如果你主动给用户提供可选项，也可以输出 choice。\n"
                    "choices 必须是 JSON 数组；没有选项时输出空数组。\n"
                    "每个选项都应是包含 id 和 text 的对象，text 要短一些。"
                ),
            ),
            "tool_execution_intent": PromptBlock(
                id="tool_execution_intent",
                text=(
                    "当用户明确要求你生成、转换、发送或处理文件，或在已有任务后说“开始、继续、直接做”时，优先调用对应工具，不要只口头说明“我现在开始”。\n"
                    "任务工作区只是记录进度，不能替代真正执行。\n"
                    "当系统把工具结果交还给你时，如果任务仍然缺少下一步必要处理，可以继续在 tool_call 中调用下一步工具；如果结果已经足够，就把 tool_call 设为 null 并自然回复。"
                ),
            ),
            "time_awareness": PromptBlock(
                id="time_awareness",
                text=(
                    "你拥有比较特别的时间感知能力，你要利用这些时间信息判断聊天频率、冷场时长、话题连续性和情绪节奏。"
                ),
            ),
            "persona_state": PromptBlock(
                id="persona_state",
                text=(
                    "persona.active 表示当前表达侧面 id；保持当前值表示延续，写其它已有 id 表示切换，写空字符串或 default 表示回到默认表达。\n"
                    "manage_persona 只用于创建、微调、查看、归档或删除表达侧面卡片本身。"
                ),
            ),
            "scene_visual_resources": PromptBlock(
                id="scene_visual_resources",
                text=(
                    "当前是 Web 场景模式。emotion 只用于同一套服装下切换表情；character.outfit 表示服装大类。\n"
                    "scene.major 表示场景大类，scene.minor 表示子场景，scene.background 表示该子场景下的背景变体，scene.bgm 表示背景音乐。\n"
                    "只能从本轮给你的可用资源里选择，不要编造不存在的背景、服装、表情或 BGM。\n"
                    "资源清单里如果给了显示名、别名、说明，你可以按这些可读名字理解资源；输出时优先写稳定 id。\n"
                    "你会额外收到一个“当前演出状态”作为本轮的基准参考；如果语气、话题、事件推进已经明显更适合新的演出状态，请自然切换。"
                ),
            ),
            "desktop_pet_visual": PromptBlock(
                id="desktop_pet_visual",
                text=(
                    "当前是 desktop_pet 桌宠模式。桌宠只实际渲染 character.outfit 与 emotion。\n"
                    "emotion 只用于同一套服装下切换表情；character.outfit 表示服装大类。\n"
                    "只能从本轮给你的角色包资源清单里选择服装和表情，不要编造不存在的 outfit 或 emotion。\n"
                    "scene 字段仅为兼容后端统一 JSON 结构，桌宠端不会渲染场景、背景或 BGM；不要为了桌宠表现主动设计场景。"
                ),
            ),
            "desktop_pet_activity": PromptBlock(
                id="desktop_pet_activity",
                text=(
                    "activity 只用于桌宠播放控制；没有播放、暂停、继续、停止、上一首、下一首或切换音频的真实意图时输出 null。\n"
                    'activity 格式为 {"action":"play|pause|resume|stop|previous|next","target":"current","source_id":"可选 file/audio/gen handle"}。\n'
                    "activity 是给桌宠执行的请求，不是完成回执；不要在 speech 里假装动作已经播放、暂停或继续。"
                ),
            ),
            "qq_text_mode": PromptBlock(
                id="qq_text_mode",
                text=(
                    "当前是 QQ 文字聊天模式。QQ 端只发送文字、气泡、文件或工具结果，不渲染 character、scene、background、BGM 或桌宠 activity。\n"
                    "不要输出只对 Web 场景或桌宠渲染有意义的演出规划。"
                ),
            ),
            "current_assistant_state": PromptBlock(
                id="current_assistant_state",
                text=CURRENT_ASSISTANT_STATE_MARKER,
            ),
        }

    def compose(self, *block_ids: str) -> str:
        parts: list[str] = []
        for block_id in block_ids:
            block = self._blocks.get(str(block_id or "").strip())
            if block is None:
                continue
            text = block.text.strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)


def build_system_prompt(*block_ids: str) -> str:
    return PromptBlockRegistry().compose(*block_ids)


def build_scene_static_system_prompt() -> str:
    return build_system_prompt(*SCENE_STATIC_SYSTEM_BLOCKS)


def build_desktop_pet_system_prompt() -> str:
    return build_system_prompt(*DESKTOP_PET_SYSTEM_BLOCKS)


def build_qq_text_system_prompt() -> str:
    return build_system_prompt(*QQ_TEXT_SYSTEM_BLOCKS)
