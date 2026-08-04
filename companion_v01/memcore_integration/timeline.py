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
TIME_PERIOD_ORDER = ("midnight", "morning", "afternoon", "night")
TIME_PERIOD_ALIASES = {
    "morning": "morning",
    "上午": "morning",
    "早上": "morning",
    "afternoon": "afternoon",
    "下午": "afternoon",
    "night": "night",
    "evening": "night",
    "晚上": "night",
    "夜晚": "night",
    "midnight": "midnight",
    "凌晨": "midnight",
    "半夜": "midnight",
}


def _memory_backend() -> str:
    backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


class MemcoreTimelineToolService:
    """A MemoryTimelineService-compatible facade for the read_memory_timeline tool.

    In memcore mode the precise read path does not need the legacy timeline
    mirror. Legacy timeline remains an optional fallback for legacy/dual modes.
    """

    def __init__(self, *, legacy_service: Any | None, memcore_manager: Any | None) -> None:
        self.legacy_service = legacy_service
        self.memcore_manager = memcore_manager

    def normalize_time_periods(self, values: Iterable[str] | None) -> list[str]:
        legacy_service = self.legacy_service
        if legacy_service is not None:
            return legacy_service.normalize_time_periods(values)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            period = TIME_PERIOD_ALIASES.get(str(value or "").strip().lower())
            if not period or period in seen:
                continue
            seen.add(period)
            normalized.append(period)
        return [period for period in TIME_PERIOD_ORDER if period in normalized]

    def read(
        self,
        *,
        profile_user_id: str,
        session_id: str = "",
        character_pack_id: str = "",
        time_range: dict[str, Any] | None = None,
        date_from: str = "",
        date_to: str = "",
        time_periods: Iterable[str] | None = None,
        anchor_source_id: str = "",
        before_turns: int = 0,
        after_turns: int = 0,
        exclude_source_ids: Iterable[str] | None = None,
        projection: str = "conversation",
        page_token_budget: int = 0,
        cursor: str = "",
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        anchor_id = str(anchor_source_id or "").strip()
        if _memory_backend() == "memcore":
            if manager is not None and getattr(manager, "enabled", False) and getattr(manager, "available", False):
                try:
                    result = manager.read_memory_timeline(
                        profile_user_id=profile_user_id,
                        # Date lookup is profile-wide. Raw anchors are deliberately
                        # constrained to the current conversation by MemCore.
                        session_id=str(session_id or profile_user_id) if anchor_id else profile_user_id,
                        character_pack_id=character_pack_id,
                        time_range=dict(time_range or {}) or None,
                        date_from=date_from,
                        date_to=date_to,
                        time_periods=list(time_periods or []),
                        anchor_source_id=anchor_id,
                        before_turns=before_turns,
                        after_turns=after_turns,
                        projection=str(projection or "conversation"),
                        page_token_budget=int(page_token_budget or 0),
                        cursor=str(cursor or ""),
                        exclude_source_ids=[str(item) for item in (exclude_source_ids or [])],
                        cross_conversation=not bool(anchor_id),
                    )
                except Exception as exc:
                    logger.warning("memcore timeline adapter failed: %s", str(exc) or exc.__class__.__name__)
                else:
                    if isinstance(result, dict):
                        if not result.get("ok"):
                            logger.warning(
                                "memcore timeline adapter returned %s: %s",
                                str(result.get("status") or "failed"),
                                str(result.get("reason") or "unknown"),
                            )
                        return result
            else:
                logger.warning("memcore timeline adapter unavailable: manager_not_available")
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "memcore_timeline_unavailable",
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": list(time_periods or []),
                "anchor_source_id": anchor_id,
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "memcore",
            }
        legacy_service = self.legacy_service
        if legacy_service is None:
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "legacy_timeline_unavailable",
                "date_from": str(date_from or ""),
                "date_to": str(date_to or ""),
                "time_periods": list(time_periods or []),
                "anchor_source_id": anchor_id,
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "projection": str(projection or "conversation"),
                "coverage": {},
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "legacy",
            }
        if time_range or cursor or str(projection or "conversation") != "conversation" or int(page_token_budget or 0):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "precise_timeline_requires_memcore",
                "projection": str(projection or "conversation"),
                "coverage": {},
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "legacy",
            }
        if anchor_id:
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "raw_anchor_requires_memcore",
                "anchor_source_id": anchor_id,
                "before_turns": int(before_turns or 0),
                "after_turns": int(after_turns or 0),
                "active_dates": [],
                "message_count": 0,
                "messages": [],
                "text": "",
                "backend": "legacy",
            }
        return legacy_service.read(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_from=date_from,
            date_to=date_to,
            time_periods=time_periods,
            exclude_source_ids=exclude_source_ids,
        )

    def read_entry(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        source_id: str,
        detail: str = "full",
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        if (
            _memory_backend() == "memcore"
            and manager is not None
            and getattr(manager, "enabled", False)
            and getattr(manager, "available", False)
        ):
            try:
                result = manager.read_memory_entry(
                    profile_user_id=profile_user_id,
                    session_id=str(session_id or profile_user_id),
                    character_pack_id=character_pack_id,
                    source_id=str(source_id or ""),
                    detail=str(detail or "full"),
                )
            except Exception as exc:
                logger.warning("memcore entry adapter failed: %s", str(exc) or exc.__class__.__name__)
            else:
                if isinstance(result, dict):
                    return result
        return {
            "operation": "read_memory_entry",
            "ok": False,
            "status": "unavailable",
            "reason": "memcore_entry_unavailable",
            "source_id": str(source_id or ""),
            "detail": str(detail or "full"),
            "entry": None,
            "text": "",
            "backend": "memcore",
        }

    def render_tool_context(self, result: dict[str, Any]) -> str:
        legacy_service = self.legacy_service
        if str((result or {}).get("backend") or "") != "memcore":
            if legacy_service is not None:
                return legacy_service.render_tool_context(result)
            return "原始对话时间线读取失败：当前记忆时间线服务不可用。"
        status = str(result.get("status") or "")
        reason = str(result.get("reason") or "")
        anchor_source_id = str(result.get("anchor_source_id") or "")
        coverage = dict(result.get("coverage") or {})
        complete = bool(coverage.get("complete", True))
        next_cursor = str(coverage.get("next_cursor") or "").strip()
        if status in {"invalid_filter", "invalid_range"}:
            labels = {
                "timeline_modes_are_mutually_exclusive": "日期模式和 raw 锚点模式不能同时使用。",
                "timeline_selector_required": "需要给出日期或 raw 检索结果的 source_id。",
                "cursor_options_are_embedded": "继续读取时只传 next_cursor，不要重复日期、范围、投影或页面预算。",
                "invalid_cursor": "这个时间线游标无效或已被改动。",
                "invalid_cursor_scope": "这个时间线游标不属于当前授权会话。",
                "raw_anchor_required": "这个 source_id 不是 raw 原始记录，不能用来扩展附近对话。",
                "raw_anchor_requires_current_conversation": "raw 锚点只能在当前会话中读取。",
                "date_must_be_YYYY-MM-DD": "日期必须使用 YYYY-MM-DD。",
                "date_from_after_date_to": "date_from 不能晚于 date_to。",
            }
            return f"原始对话时间线读取失败：{labels.get(reason, reason or '参数无效')}"
        if status in {"failed", "unavailable"}:
            return f"原始对话时间线暂时不可用：{reason or 'memory_timeline_unavailable'}"
        continuation_lines: list[str] = []
        if not complete:
            if next_cursor:
                continuation_lines.extend(
                    [
                        "本次只返回了完整逻辑单元组成的一页，后面仍有原始证据。",
                        f"继续读取时只调用 read_memory_timeline(cursor={next_cursor})，不要重复或修改原选择器。",
                    ]
                )
            else:
                continuation_lines.append(
                    "时间线报告仍有未返回证据，但没有提供可执行游标；请明确说明读取链路不完整，不要猜测。"
                )
        if anchor_source_id:
            text = str(result.get("text") or "").strip()
            if status == "empty" or not text:
                return "\n".join(
                    [
                        "这个 raw 记忆锚点附近本页没有可读取的原始对话。",
                        *continuation_lines,
                    ]
                )
            return "\n".join(
                [
                    "【raw 记忆锚点附近的完整对话 turn】",
                    "下面是由 MemCore 按 raw source_id 读取的前后完整 turn；工具并行调用不会被截成半轮。",
                    text,
                    *continuation_lines,
                    "请综合这些原始记录回答；缺失的细节不要猜。",
                ]
            )
        selector_mode = str(result.get("selector_mode") or "")
        time_range = dict(result.get("time_range") or {})
        if selector_mode == "time_range" or (time_range and not result.get("date_from")):
            start_at = str(time_range.get("start_at") or "")
            end_at = str(time_range.get("end_at") or "")
            text = str(result.get("text") or "").strip()
            range_label = f"{start_at} 至 {end_at}（终点不包含）"
            if status == "empty" or not text:
                return "\n".join(
                    [
                        f"原始对话时间线：{range_label} 本页没有留下可读取的对话记录。",
                        *continuation_lines,
                    ]
                )
            return "\n".join(
                [
                    f"【原始对话时间线：{range_label}】",
                    "下面是由 MemCore 按数据库时间精确读取的原始记录，不是摘要或长期语义记忆。",
                    text,
                    *continuation_lines,
                    "请只根据这些已加载的原始记录回答；缺失的细节不要凭印象补写。",
                ]
            )
        date_from = str(result.get("date_from") or "")
        date_to = str(result.get("date_to") or "")
        periods = list(result.get("time_periods") or [])
        range_label = date_from if date_from == date_to else f"{date_from} 至 {date_to}"
        period_label = "、".join(TIME_PERIOD_LABELS.get(str(item), str(item)) for item in periods) or "全天"
        if status == "empty":
            return "\n".join(
                [
                    f"原始对话时间线：{range_label}（{period_label}）本页没有留下可读取的对话记录。",
                    *continuation_lines,
                ]
            )
        text = str(result.get("text") or "").strip()
        if not text:
            return "\n".join(
                [
                    f"原始对话时间线：{range_label}（{period_label}）本页没有留下可读取的对话记录。",
                    *continuation_lines,
                ]
            )
        return "\n".join(
            [
                f"【原始对话时间线：{range_label}（{period_label}）】",
                "下面是由 memcore 按数据库时间精确读取的原始对话，不是摘要或长期语义记忆。",
                text,
                *continuation_lines,
                "请只根据这些已加载的原始记录回答；缺失的细节不要凭印象补写。",
            ]
        )

    def render_entry_context(self, result: dict[str, Any]) -> str:
        status = str((result or {}).get("status") or "")
        reason = str((result or {}).get("reason") or "")
        source_id = str((result or {}).get("source_id") or "")
        if status in {"failed", "unavailable", "invalid_filter"}:
            return f"原始记忆条目读取失败：{reason or 'memory_entry_unavailable'}"
        text = str((result or {}).get("text") or "").strip()
        if status == "empty" or not text:
            return (
                f"原始记忆条目 {source_id or 'unknown'} 在当前授权会话中不可用；"
                "不要把它当作已读取证据，也不要猜测正文。"
            )
        return "\n".join(
            [
                f"【原始记忆条目：{source_id}】",
                text,
                "这是 MemCore 返回的当前会话原始证据；请结合问题回答，不要重复展开同一 source_id。",
            ]
        )

    def build_acquaintance_prompt(self, **kwargs: Any) -> str:
        manager = self.memcore_manager
        if manager is not None and manager.available:
            acquaintance_note = getattr(manager, "acquaintance_note", None)
            if not callable(acquaintance_note):
                return ""
            profile_user_id = str(kwargs.get("profile_user_id") or "").strip()
            result = acquaintance_note(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=str(kwargs.get("character_pack_id") or "").strip(),
                now_ts=kwargs.get("now_ts"),
            )
            if result:
                return result
        legacy_service = self.legacy_service
        return legacy_service.build_acquaintance_prompt(**kwargs) if legacy_service is not None else ""
