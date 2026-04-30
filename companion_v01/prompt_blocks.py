from __future__ import annotations

from dataclasses import dataclass


CURRENT_ASSISTANT_STATE_MARKER = "[CURRENT ASSISTANT STATE - EMBODY THIS]"


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
            "reply_bubbles": PromptBlock(
                id="reply_bubbles",
                text=(
                    "speech 是兼容文本；单气泡回复直接填写 speech，并让 speech_segments 为空数组。\n"
                    "如果本轮适合像即时聊天一样连续发 2 到 3 个小气泡，填写 speech_segments，speech 可以留空；系统会把 speech_segments 合并回 speech。\n"
                    "speech_segments 最多 3 条，每条都应是自然完整的小气泡，不要把同一句话硬拆碎，也不要和 speech 重复写同一整段。"
                ),
            ),
            "memory_tags": PromptBlock(
                id="memory_tags",
                text=(
                    "memory_tags 只用于后续记忆检索，目标是给“用户当前这句话”补几个便于召回的关键词。\n"
                    "只有当用户当前这句话本身包含以后可能需要回忆的事实、事件、安排、偏好、身份线索时，才输出 1 到 4 个关键词或短短语；否则输出空字符串。"
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
                    "code_snippet 只放纯代码或纯示例文本，不要带 markdown 代码块围栏；没有代码时输出空字符串。"
                ),
            ),
            "persona_state": PromptBlock(
                id="persona_state",
                text=(
                    "persona.active 表示当前表达侧面 id；保持当前值表示延续，写其它已有 id 表示切换，写空字符串或 default 表示回到默认表达。\n"
                    "manage_persona 只用于创建、微调、查看、归档或删除表达侧面卡片本身。"
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


def build_desktop_pet_system_prompt() -> str:
    return PromptBlockRegistry().compose(
        "json_object_only",
        "reply_bubbles",
        "memory_tags",
        "code_snippet",
        "tool_call",
        "persona_state",
        "desktop_pet_visual",
        "desktop_pet_activity",
        "current_assistant_state",
    )
