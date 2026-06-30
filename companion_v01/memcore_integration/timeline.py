"""Timeline adapter that lets Akane's existing tool handler read from memcore."""

from __future__ import annotations

import logging
from typing import Any, Iterable

import config


logger = logging.getLogger("akane.memcore.timeline")

TIME_PERIOD_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "night": "夜晚",
    "midnight": "凌晨",
}


def _memory_backend() -> str:
    backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


class MemcoreTimelineToolService:
    """A MemoryTimelineService-compatible facade for the read_memory_timeline tool.

    Legacy timeline remains the fallback and still owns acquaintance/mirror helpers.
    In memcore mode only the precise read path is switched.
    """

    def __init__(self, *, legacy_service: Any, memcore_manager: Any | None) -> None:
        self.legacy_service = legacy_service
        self.memcore_manager = memcore_manager

    def normalize_time_periods(self, values: Iterable[str] | None) -> list[str]:
        return self.legacy_service.normalize_time_periods(values)

    def read(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        date_from: str,
        date_to: str,
        time_periods: Iterable[str] | None = None,
        exclude_source_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        if _memory_backend() == "memcore":
            if manager is not None and getattr(manager, "enabled", False) and getattr(manager, "available", False):
                try:
                    result = manager.read_memory_timeline(
                        profile_user_id=profile_user_id,
                        # Akane's legacy timeline is profile + character scoped, not session scoped.
                        # Use profile_user_id as the memcore system key and ask memcore to read cross-conversation.
                        session_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        date_from=date_from,
                        date_to=date_to,
                        time_periods=list(time_periods or []),
                        exclude_source_ids=[str(item) for item in (exclude_source_ids or [])],
                        cross_conversation=True,
                    )
                except Exception as exc:
                    logger.warning("memcore timeline adapter failed: %s", str(exc) or exc.__class__.__name__)
                else:
                    if isinstance(result, dict) and result.get("ok"):
                        return result
                    logger.warning(
                        "memcore timeline adapter unavailable: %s",
                        str((result or {}).get("reason") or (result or {}).get("status") or "unknown"),
                    )
            else:
                logger.warning("memcore timeline adapter unavailable: manager_not_available")
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "memcore_timeline_unavailable",
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": list(time_periods or []),
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }
        return self.legacy_service.read(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_from=date_from,
            date_to=date_to,
            time_periods=time_periods,
            exclude_source_ids=exclude_source_ids,
        )

    def render_tool_context(self, result: dict[str, Any]) -> str:
        if str((result or {}).get("backend") or "") != "memcore":
            return self.legacy_service.render_tool_context(result)
        status = str(result.get("status") or "")
        date_from = str(result.get("date_from") or "")
        date_to = str(result.get("date_to") or "")
        periods = list(result.get("time_periods") or [])
        range_label = date_from if date_from == date_to else f"{date_from} 至 {date_to}"
        period_label = "、".join(TIME_PERIOD_LABELS.get(str(item), str(item)) for item in periods) or "全天"
        if status == "invalid_range":
            return "原始对话时间线读取失败：日期范围无效。日期必须使用 YYYY-MM-DD，且 date_from 不能晚于 date_to。"
        if status == "empty":
            return f"原始对话时间线：{range_label}（{period_label}）没有留下对话记录。"
        text = str(result.get("text") or "").strip()
        if not text:
            return f"原始对话时间线：{range_label}（{period_label}）没有留下对话记录。"
        return "\n".join(
            [
                f"【原始对话时间线：{range_label}（{period_label}）】",
                "下面是由 memcore 按数据库时间精确读取的原始对话，不是摘要或长期语义记忆。",
                text,
                "请只根据这些已加载的原始记录回答；缺失的细节不要凭印象补写。",
            ]
        )

    def build_acquaintance_prompt(self, **kwargs: Any) -> str:
        return self.legacy_service.build_acquaintance_prompt(**kwargs)
