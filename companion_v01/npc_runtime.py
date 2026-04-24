from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .llm_runtime import LLMRuntime
from .store import MemoryStore
from .text_utils import extract_semantic_tags, render_chat_timeline


class GenericNPCRuntime:
    def __init__(self, base_dir: Path, llm: LLMRuntime):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(self.base_dir)
        self.llm = llm

    def reset(self) -> None:
        self.store.reset()

    def reply(
        self,
        *,
        profile_user_id: str,
        npc_name: str,
        npc_role: str,
        query: str,
        scene_context: str,
        now_ts: int,
    ) -> dict[str, Any]:
        session_id = f"generic_npc::{profile_user_id}"

        self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            role="user",
            content=query,
            timestamp=now_ts,
            semantic_tags=extract_semantic_tags(query),
        )

        recent_context = self.store.get_unsummarized_messages(session_id)[-12:]
        context_text = render_chat_timeline(recent_context)
        fallback_speech = "这个嘛，大概就是这样，你再看看？"
        fallback = {
            "speech": fallback_speech,
            "status": "npc_final",
        }
        system_prompt = (
            "你是视觉小说场景里的临时 NPC。"
            "你的任务是根据当前场景和被提问内容，自然、简短、符合身份地回答。"
            "不要代替主角色发言，不要抢戏，不要展开长篇独白。"
            "如果问题很直接，就直接回答；如果信息不足，也要像路人、摊主、店员那样自然回应。"
            "你必须只输出一个合法 JSON 对象，不能输出任何额外解释、代码块或 markdown。"
            "字段固定为 speech, status。"
            "status 固定输出 npc_final。"
        )
        user_prompt = (
            f"当前时间：{datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d %H:%M')}\n"
            f"NPC 名字：{npc_name}\n"
            f"NPC 身份：{npc_role}\n"
            f"当前场景：{scene_context or '未提供'}\n\n"
            f"这个 NPC 近期记忆：\n{context_text or '(无)'}\n\n"
            f"现在有人向这个 NPC 发问：{query}\n\n"
            "请输出 JSON。"
        )
        result = self.llm.call_aux_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=0.4,
            prompt_cache_key="aux:generic_npc",
        )

        speech = str(result.get("speech") or fallback_speech).strip() or fallback_speech
        self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            role="assistant",
            content=speech,
            timestamp=now_ts,
            semantic_tags=extract_semantic_tags(speech),
        )

        return {
            "npc_id": "generic_npc",
            "speaker": npc_name or "路人",
            "role": npc_role or "通用NPC",
            "speech": speech,
            "status": "npc_final",
        }
