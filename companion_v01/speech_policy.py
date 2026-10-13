"""Character-owned speech language and provider-appropriate delivery intent."""
from __future__ import annotations

from dataclasses import replace
import json
import re

from .desktop_pet_character_resources import load_character_speech_config
from .prompt_blocks import build_system_prompt
from .speech_units import PROTOCOL

IRODORI_CAPTION_MAX_CHARS = 80


def irodori_caption(style):
    """Bound execution metadata without rewriting the model's original intent."""
    caption = str(style or "").strip()
    if not caption:
        return "", ""
    from .assistant_history_projection import ARCHIVED_STYLE
    if caption in (ARCHIVED_STYLE, "[archived]"):
        return "", "caption_archived"
    if len(caption) > IRODORI_CAPTION_MAX_CHARS:
        return "", "caption_too_long"
    if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", caption):
        return "", "legacy_style_id"
    if any(ord(char) < 32 and char not in "\n\t" for char in caption):
        return "", "caption_invalid"
    return caption, ""


def character_speech_policy(engine, pack_id, *, provider_id=None):
    raw = load_character_speech_config(getattr(engine, "desktop_pet_character_resources", None), pack_id)
    if raw.get("enabled") is not True:
        return {}
    language = str(raw.get("language") or "").strip()
    if not language or len(language) > 30:
        return {}
    from .tts_provider_selection import IRODORI_PROVIDER_ID, select_tts_provider
    if provider_id is None:
        builder = getattr(getattr(engine, "desktop_pet_character_resources", None), "build_character_voice_preference", None)
        try:
            preference = builder(pack_id) if callable(builder) else {}
        except Exception:
            preference = {}
        provider_id = select_tts_provider(preference or {}).provider_id
    if provider_id == IRODORI_PROVIDER_ID:
        # Irodori selects a registered speaker directly. GPT reference mappings
        # are neither prerequisites nor options for its model-facing contract.
        return {"language": language, "style_mode": "description"}
    styles = {}
    for name, item in (raw.get("styles") or {}).items() if isinstance(raw.get("styles"), dict) else ():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", str(name)) or not isinstance(item, dict):
            continue
        profile = str(item.get("profile_id") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", profile):
            continue
        styles[name] = {"profile_id": profile, "description": str(item.get("description") or "")[:80]}
    default = str(raw.get("default_style") or "").strip()
    if default not in styles:
        return {}
    return {"language": language, "default_style": default, "styles": styles}


def speech_prompt_profile(profile, policy):
    # Replace conflicting old speech rules; don't pile exceptions onto them.
    blocks = tuple(key for key in profile.system_block_ids
                   if key not in {"field_order", "speech_streaming"})
    system = (build_system_prompt(*blocks)
              .replace("当前是 QQ 文字聊天模式", "当前是 QQ 聊天模式")
              .replace("speech 是发送到 QQ 的消息正文", "text 是发送到 QQ 的消息正文")
              .replace("speech 写自然解释", "text 写自然解释")
              .replace("不要在 speech 里假装动作", "不要在 text 里假装动作"))
    style_help = "、".join(f"{name}（{item['description']}）" if item["description"] else name
                          for name, item in policy.get("styles", {}).items())
    example = {"emotion": "normal"}
    free_style = policy.get("style_mode") == "description"
    example_say = "おかえり。今日もお疲れさま。" if policy["language"] in {"日语", "日本語", "ja"} else "配音台词"
    example["utterances"] = [{"emotion": "normal",
                              "style": "親しみを込めて、自然な会話の調子で" if free_style else policy["default_style"],
                              "text": "欢迎回来，今天辛苦了。", "say": example_say}]
    style_contract = (
        f"style 用日语一句简短自然语言描述本组 say 的情绪、语气和节奏，随语境自由表达，通常 10～40 字，最多 {IRODORI_CAPTION_MAX_CHARS} 字；可省略。"
        "声音身份沿用角色，与画面 emotion 独立。\n"
        if free_style else f"style 选择声音语气：{style_help}；省略时用 {policy['default_style']}，与画面 emotion 独立。\n"
    )
    contract = (
        f"当前客户端：{profile.mode.value}。回复格式：\n{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}\n"
        "示例演示字段配合，台词与表现随语境自由表达。\n"
        f"text 写中文显示正文；say 用{policy['language']}自然表达同一意思，只含实际台词，不含动作描写。"
        "普通对话填写完整 say 用于语音合成；纯展示内容用 say: null，沉默用 utterances: []。语音是否发送由宿主开关决定。\n"
        f"{style_contract}"
        "顶层 emotion 是本轮整体表情，供 QQ 使用并作为桌宠第一组的兜底。组内 emotion 选择本组台词展示时的已有表情，"
        "第一组省略则用顶层，后续省略则延续上一组。\n"
        "每组按 emotion、style（均可省略）、text、say 的顺序写。同一组可含多句，换表情或语气再分组。"
        "text 和 say 用。？！等自然句末标点，宿主边接收边按标点分句展示、合成，一组不等于一次合成。"
        "长段按完整意思分组，让中文和配音及时对应。\n"
        "最终 JSON 先写 emotion、utterances，其它字段只在有实际作用时按原说明置后；"
        "不另写 speech 或 speech_segments，不加排版缩进。"
    )
    return replace(profile, system_block_ids=blocks, system_prompt_override=system,
                   fast_mode_prompt=contract, debug_mode_prompt=contract)


def configure_speech_prompt(engine, pack_id, client_context, profile):
    if not client_context.has_capability(PROTOCOL) or client_context.has_capability("scene_presentation_v1"):
        return profile
    policy = character_speech_policy(engine, pack_id)
    return speech_prompt_profile(profile, policy) if policy else profile
