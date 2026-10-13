"""Durable host explanation separating reading history from new replies."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..assistant_history_projection import ASSISTANT_HISTORY_RECORD_HEADER
from .. import assistant_history_projection
from .contracts import _PROMPT_CONTEXT_KIND_PREFIX, _PROMPT_CONTEXT_SOURCE_PREFIX

logger = logging.getLogger("akane.memcore")

HISTORY_NOTICE_VERSION = 5
HISTORY_NOTICE_KIND = f"{_PROMPT_CONTEXT_KIND_PREFIX}.voice_history_notice"
HISTORY_NOTICE_INCLUDE_EXAMPLE = True


def build_history_projection_notice(policy: dict[str, Any], *, history_format: str | None = None,
                                    include_example: bool | None = None) -> str:
    language = str(policy.get("language") or "").strip()
    if not language:
        return ""
    if language in {"ja", "日本語"}:
        language = "日语"
    style_help = (
        "style：用简短日语描述当前这组台词的情绪、语气和节奏。"
        if policy.get("style_mode") == "description"
        else "style：按当前客户端的语音规则，为这组台词填写对应的短语气标签。"
    )
    if (history_format or assistant_history_projection.ASSISTANT_HISTORY_FORMAT) == "text":
        history_help = (
            f"历史中的“{ASSISTANT_HISTORY_RECORD_HEADER}”是系统为了节省上下文，"
            "将助手原始 JSON 回复转换成的阅读记录。助手当时输出的是 JSON，"
            "系统提取每组 text 的中文原话放入引号，开头记录整体表情，"
            "引号外的注记记录该组独立表情、短语气和显示状态。"
            "各组沿用整体表情，独立表情在该组旁标出。"
            "完整 JSON、say 与自然语言 style 原文另行保存。\n"
        )
    else:
        history_help = (
            "历史 JSON 中的 [hidden] 标记过去收起的语音正文。"
            "中文、表情和短语气标签保留，完整 say 与自然语言 style 原文另行保存。\n"
        )
    notice = (
        "【系统说明：历史语音字段】\n"
        + history_help +
        "历史用于理解对话；最终回复使用当前客户端 JSON 格式。每组 text 写中文，"
        f"say 写同义的完整、自然{language}台词。\n"
        f"{style_help}\n"
        "调用工具时沿用工具协议。"
    )
    if language == "日语" and (HISTORY_NOTICE_INCLUDE_EXAMPLE if include_example is None else include_example):
        description_style = policy.get("style_mode") == "description"
        default_style = str(policy.get("default_style") or "").strip()
        example = {
            "emotion": "normal",
            "utterances": [
                {"emotion": "happy",
                 "style": "親しみを込めて、明るく自然に" if description_style else default_style,
                 "text": "欢迎回来，今天辛苦了。",
                 "say": "おかえり。今日もお疲れさま。"},
                {"emotion": "normal",
                 "style": "穏やかに、相手を気遣うように" if description_style else default_style,
                 "text": "先休息一会儿吧，想聊天的时候我就在这里。",
                 "say": "まずは少し休んでね。話したくなったら、私はここにいるよ。"},
            ],
            "memory_metadata": {},
        }
        notice += (
            "\n完整 JSON 示例（内容与组数随当前语境变化）：\n"
            + json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        )
    return notice


class MemcoreHistoryNoticeMixin:
    def _ensure_history_projection_notice(
        self, system: Any, *, compaction_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append once on activation and once after each committed raw compaction.

        Ordinary response turns use an in-memory ready check. Source IDs also
        make reopening the manager and repeated completion callbacks idempotent.
        Operational material is excluded from autobiographical semantic memory.
        """
        if str(getattr(getattr(system, "namespace", None), "conversation_id", "") or "").startswith("subagent_"):
            return {"ok": True, "status": "disabled"}
        provider = self._history_notice_policy_provider
        if provider is None:
            return {"ok": True, "status": "disabled"}
        if compaction_stats is not None:
            # Semantic-only compaction, no-op, busy and failed raw batches do
            # not justify another reminder. A committed raw batch can still
            # be followed by a failing semantic phase; its summaries are real.
            if not compaction_stats.get("summary_source_ids") or not compaction_stats.get("summaries_created"):
                return {"ok": True, "status": "unchanged"}
        key = self._compaction_namespace_key(system)
        with self._lock:
            if self._closing or self._closed:
                return {"ok": False, "status": "closed"}
            if compaction_stats is not None:
                self._history_notice_refresh_pending.add(key)
                self._history_notice_ready.discard(key)
            if compaction_stats is None and key in self._history_notice_ready:
                return {"ok": True, "status": "unchanged"}
            try:
                text = build_history_projection_notice(provider(key[2]) or {})
                if not text:
                    return {"ok": True, "status": "disabled"}
                generation, _ = self._store.get_conversation_generations(namespace=system.namespace)
                digest = hashlib.sha256("\x00".join((*key, text)).encode("utf-8")).hexdigest()[:32]
                source_id = (
                    f"{_PROMPT_CONTEXT_SOURCE_PREFIX}:voice-history-notice:"
                    f"{HISTORY_NOTICE_VERSION}:{digest}:{generation}"
                )
                if key not in self._history_notice_refresh_pending and self._store.get_entry(
                    namespace=system.namespace, source_id=source_id,
                ) is None:
                    # A semantic-only generation change leaves the raw notice
                    # intact. Restore that notice instead of adding another.
                    visible = self._store.list_prompt_visible_entries(namespace=system.namespace)
                    previous = next((entry for entry in reversed(visible)
                                     if entry.kind == HISTORY_NOTICE_KIND and entry.semantic_text == text), None)
                    if previous is not None:
                        self._history_notice_ready.add(key)
                        return {"ok": True, "status": "restored", "source_id": previous.source_id,
                                "compaction_generation": previous.payload["compaction_generation"]}
                memcore = self._memcore_module
                stored = system.append_standalone_entry(memcore.TimelineEntryInput(
                    source_id=source_id, kind=HISTORY_NOTICE_KIND,
                    origin=memcore.EntryOrigin.ENVIRONMENT, turn_role=None,
                    semantic_text=text, timestamp=int(time.time()),
                    payload={"notice_version": HISTORY_NOTICE_VERSION,
                             "compaction_generation": generation},
                    memory_metadata={}, annotation_status=memcore.AnnotationStatus.UNANNOTATED,
                    retrieval_policy=memcore.RetrievalPolicy.NEVER,
                    retrieval_visibility=memcore.RetrievalVisibility.NEVER,
                    semanticize=False, prompt_visible=True,
                    trust=memcore.EntryTrust.TRUSTED_INSTRUCTION,
                    compatibility_role=HISTORY_NOTICE_KIND,
                ))
                self._history_notice_ready.add(key)
                self._history_notice_refresh_pending.discard(key)
                return {"ok": True, "status": "ready", "source_id": stored.source_id,
                        "compaction_generation": generation}
            except Exception as exc:
                # Retrying at the next actual turn also covers a reminder write
                # failing after compaction; never cache a failed activation.
                self._history_notice_ready.discard(key)
                logger.warning("memcore history notice status=failed error_type=%s", type(exc).__name__)
                return {"ok": False, "status": "failed", "reason": "history_notice_write_failed"}
