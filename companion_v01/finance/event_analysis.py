from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .event_contracts import FinanceAnalysisRequest, FinanceAnalysisResult


_INCOMPLETE_FINAL_MARKERS = (
    "还没处理完",
    "尚未处理完",
    "未处理完",
    "正在处理中",
    "稍后再回复",
    "稍后给你结果",
)
_STRUCTURE_MARKERS = ("已确认事实", "客观数据与时间", "分析推断", "待验证")


class AkaneFinanceAnalysisClient:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def analyze(self, request: FinanceAnalysisRequest) -> FinanceAnalysisResult:
        try:
            raw_frame = self.engine.process_turn(request.to_turn_payload())
        except Exception as exc:
            return FinanceAnalysisResult(
                ok=False,
                status="analysis_failed",
                analysis_id=request.analysis_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(raw_frame, dict):
            return FinanceAnalysisResult(
                ok=False,
                status="invalid_analysis_output",
                analysis_id=request.analysis_id,
                reason="engine result must be an object",
            )

        original_text = _frame_text(raw_frame)
        if not original_text:
            return FinanceAnalysisResult(
                ok=False,
                status="empty_analysis",
                analysis_id=request.analysis_id,
                reason="analysis produced no user-facing speech",
            )
        compact = "".join(original_text.split())
        if len(compact) <= 80 and any(marker in compact for marker in _INCOMPLETE_FINAL_MARKERS):
            return FinanceAnalysisResult(
                ok=False,
                status="incomplete_analysis",
                analysis_id=request.analysis_id,
                reason="analysis stopped at a progress placeholder",
            )

        messages = ensure_market_push_contract(request=request, frame=raw_frame)
        if not messages:
            return FinanceAnalysisResult(
                ok=False,
                status="empty_analysis",
                analysis_id=request.analysis_id,
                reason="analysis normalization produced no messages",
            )
        frame = dict(raw_frame)
        frame["speech"] = "\n".join(messages)
        frame["speech_segments"] = list(messages) if len(messages) > 1 else []
        memory_status = self._record_memory(request=request, messages=messages)
        return FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id=request.analysis_id,
            messages=messages,
            frame=frame,
            memory_status=memory_status,
        )

    def _record_memory(
        self,
        *,
        request: FinanceAnalysisRequest,
        messages: tuple[str, ...],
    ) -> dict[str, Any]:
        manager = getattr(self.engine, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False):
            return {"ok": False, "status": "disabled", "reason": "memcore_not_enabled"}

        event = request.event_record.event
        keywords = [event.code, event.content_type, event.source or event.provider]
        evidence_status = manager.record_tool_exchange(
            tool_name="market_feed",
            tool_call_id=event.event_id,
            tool_input={
                "subscription_id": request.subscription.subscription_id,
                "attempt_count": request.attempt_count,
                "importance": request.importance.to_public_dict(),
            },
            result=request.event_record.to_public_dict(),
            source=event.source or event.provider,
            timestamp=event.published_at,
            source_id_prefix=request.analysis_id,
            keywords=keywords,
            importance=min(0.9, max(0.2, request.importance.score)),
            confidence=1.0,
            profile_user_id=request.subscription.profile_user_id,
            session_id=request.subscription.session_id,
            character_pack_id=request.subscription.character_pack_id,
        )
        confidence = 0.75 if event.source and event.url else 0.55
        analysis_status = manager.record_assistant_turn(
            {
                "source_id": request.analysis_id,
                "content": "\n".join(messages),
                "timestamp": request.requested_at,
                "memory_metadata": {
                    "categories": ["market_analysis"],
                    "keywords": keywords,
                    "subject_scopes": ["assistant"],
                    "importance": request.importance.score,
                    "confidence": confidence,
                },
            },
            profile_user_id=request.subscription.profile_user_id,
            session_id=request.subscription.session_id,
            character_pack_id=request.subscription.character_pack_id,
        )
        compaction_status = manager.compact_due_background(
            profile_user_id=request.subscription.profile_user_id,
            session_id=request.subscription.session_id,
            character_pack_id=request.subscription.character_pack_id,
        )
        return {
            "ok": bool(evidence_status.get("ok")) and bool(analysis_status.get("ok")),
            "status": "recorded" if bool(evidence_status.get("ok")) and bool(analysis_status.get("ok")) else "degraded",
            "evidence": evidence_status,
            "analysis": analysis_status,
            "compaction": compaction_status,
        }


def ensure_market_push_contract(
    *,
    request: FinanceAnalysisRequest,
    frame: dict[str, Any],
    max_message_chars: int = 1750,
) -> tuple[str, ...]:
    event = request.event_record.event
    body = _frame_text(frame)
    published_iso = _event_time_iso(event.published_at)
    header_time = datetime.fromtimestamp(event.published_at, tz=ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
    header = f"【市场快讯｜{header_time}】"
    source_name = event.source or event.provider or "unknown"
    source_line = f"来源：{source_name}｜发布时间：{published_iso}"
    if event.url:
        source_line += f"｜{event.url}"

    if not all(marker in body for marker in _STRUCTURE_MARKERS):
        body = "\n".join(
            [
                f"已确认事实：{event.title}",
                f"客观数据与时间：证券代码 {event.code}；事件发布时间 {published_iso}。",
                f"分析推断：{body}",
                "尚待验证与风险：当前直接证据仅包含事件结构化字段，标题不等于完整正文；"
                "如事件与行情同时出现，也不能据此确认因果。",
                "接下来观察：等待完整公告或报道正文，并结合带 as_of 的行情与后续披露继续核验。",
            ]
        )
    if not body.startswith("【市场快讯"):
        body = f"{header}\n{body}"
    if "来源：" not in body or "发布时间：" not in body:
        body = f"{body}\n{source_line}"
    return _split_messages(body, max_chars=max(200, min(1800, int(max_message_chars))))


def _frame_text(frame: dict[str, Any]) -> str:
    segments = frame.get("speech_segments")
    if isinstance(segments, list):
        clean_segments = [str(item or "").strip() for item in segments if str(item or "").strip()]
        if clean_segments:
            return "\n".join(clean_segments)
    return str(frame.get("speech") or "").strip()


def _event_time_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=ZoneInfo("Asia/Shanghai")).isoformat()


def _split_messages(text: str, *, max_chars: int) -> tuple[str, ...]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    messages: list[str] = []
    current = ""
    for line in lines:
        if len(line) > max_chars:
            if current:
                messages.append(current)
                current = ""
            messages.extend(line[index : index + max_chars] for index in range(0, len(line), max_chars))
            continue
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= max_chars:
            current = candidate
            continue
        messages.append(current)
        current = line
    if current:
        messages.append(current)
    return tuple(message for message in messages if message)
