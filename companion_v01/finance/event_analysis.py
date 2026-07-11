from __future__ import annotations

from datetime import datetime
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

import config

from .event_contracts import FinanceAnalysisRequest, FinanceAnalysisResult
from .public_news_event_source import FinanceNewsRelayPolicy


_INCOMPLETE_FINAL_MARKERS = (
    "还没处理完",
    "尚未处理完",
    "未处理完",
    "正在处理中",
    "稍后再回复",
    "稍后给你结果",
)
_TRANSIENT_FALLBACK_MARKERS = (
    "我在认真听你说",
    "要不要再多告诉我一点",
)
_STRUCTURE_MARKERS = ("已确认事实", "客观数据与时间", "分析推断", "待验证")
_ANALYSIS_REFUSAL_MARKERS = ("无法分析", "不能分析", "无法提供", "不能提供", "抱歉")
_NEWS_OUTPUT_POLICY = FinanceNewsRelayPolicy()


class AkaneFinanceAnalysisClient:
    def __init__(
        self,
        engine: Any,
        *,
        max_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.engine = engine
        configured_attempts = (
            getattr(config, "FINANCE_ANALYSIS_MAX_ATTEMPTS", 3) if max_attempts is None else max_attempts
        )
        configured_backoff = (
            getattr(config, "FINANCE_ANALYSIS_RETRY_BACKOFF_SECONDS", 0.5)
            if retry_backoff_seconds is None
            else retry_backoff_seconds
        )
        self.max_attempts = max(1, min(5, int(configured_attempts)))
        self.retry_backoff_seconds = max(0.0, min(5.0, float(configured_backoff)))
        self._sleeper = sleeper

    def analyze(self, request: FinanceAnalysisRequest) -> FinanceAnalysisResult:
        direct_relay_message = _direct_news_relay_message(request)
        if direct_relay_message and not bool(
            getattr(config, "FINANCE_PUBLIC_NEWS_MODEL_ANALYSIS_ENABLED", True)
        ):
            return FinanceAnalysisResult(
                ok=True,
                status="relayed_without_analysis",
                analysis_id=request.analysis_id,
                messages=(direct_relay_message,),
                frame={"speech": direct_relay_message, "speech_segments": [direct_relay_message]},
                reason="optional news model analysis disabled",
            )
        last_status = "analysis_failed"
        last_reason = "analysis did not run"
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_frame = self.engine.process_turn(request.to_turn_payload())
            except Exception as exc:
                last_status = "analysis_failed"
                last_reason = f"{type(exc).__name__}: {exc}"
            else:
                validation = self._validate_frame(request=request, raw_frame=raw_frame)
                if validation.ok:
                    messages = validation.messages
                    frame = dict(validation.frame)
                    return FinanceAnalysisResult(
                        ok=True,
                        status="analyzed" if attempt == 1 else "analyzed_after_retry",
                        analysis_id=request.analysis_id,
                        messages=messages,
                        frame=frame,
                        memory_status=self._record_memory(request=request, messages=messages),
                        analysis_attempts=attempt,
                    )
                last_status = validation.status
                last_reason = validation.reason
            if attempt < self.max_attempts and self.retry_backoff_seconds > 0:
                self._sleeper(self.retry_backoff_seconds * attempt)
        if direct_relay_message:
            return FinanceAnalysisResult(
                ok=True,
                status="relayed_without_analysis",
                analysis_id=request.analysis_id,
                messages=(direct_relay_message,),
                frame={"speech": direct_relay_message, "speech_segments": [direct_relay_message]},
                reason=f"{last_reason}; analysis omitted after {self.max_attempts} attempt(s)",
                analysis_attempts=self.max_attempts,
            )
        return FinanceAnalysisResult(
            ok=False,
            status=last_status,
            analysis_id=request.analysis_id,
            reason=f"{last_reason}; exhausted {self.max_attempts} analysis attempt(s)",
            analysis_attempts=self.max_attempts,
        )

    @staticmethod
    def _validate_frame(
        *,
        request: FinanceAnalysisRequest,
        raw_frame: Any,
    ) -> FinanceAnalysisResult:
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
        if "direct_relay" in request.event_record.event.labels and any(
            marker in compact for marker in _ANALYSIS_REFUSAL_MARKERS
        ):
            return FinanceAnalysisResult(
                ok=False,
                status="analysis_refused",
                analysis_id=request.analysis_id,
                reason="optional analysis returned refusal language",
            )
        if "direct_relay" in request.event_record.event.labels:
            relay_decision = _NEWS_OUTPUT_POLICY.evaluate_text(original_text)
            if not relay_decision.allowed:
                return FinanceAnalysisResult(
                    ok=False,
                    status="analysis_policy_blocked",
                    analysis_id=request.analysis_id,
                    reason=(
                        "optional analysis reintroduced blocked content:"
                        f"{relay_decision.reason}:{relay_decision.matched_term}"
                    ),
                )
        if len(compact) <= 160 and any(marker in compact for marker in _TRANSIENT_FALLBACK_MARKERS):
            return FinanceAnalysisResult(
                ok=False,
                status="transient_fallback_analysis",
                analysis_id=request.analysis_id,
                reason="analysis returned the transient persona fallback",
            )
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
        return FinanceAnalysisResult(
            ok=True,
            status="validated",
            analysis_id=request.analysis_id,
            messages=messages,
            frame=frame,
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
        batch_records = (request.event_record, *request.related_event_records)
        keywords = list(
            dict.fromkeys(
                item
                for record in batch_records
                for item in (
                    record.event.code,
                    record.event.content_type,
                    record.event.source or record.event.provider,
                )
                if item
            )
        )
        evidence_status = manager.record_tool_exchange(
            tool_name="market_feed",
            tool_call_id=event.event_id,
            tool_input={
                "subscription_id": request.subscription.subscription_id,
                "attempt_count": request.attempt_count,
                "importance": request.importance.to_public_dict(),
                "batch_kind": request.batch_kind,
                "event_count": len(batch_records),
            },
            result={
                "batch_kind": request.batch_kind,
                "primary_event": request.event_record.to_public_dict(),
                "related_events": [record.to_public_dict() for record in request.related_event_records],
            },
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
    is_source_report = "source_report_only" in event.labels
    if is_source_report:
        clean_body = _clean_source_report_body(body, source_url=event.url)
        if not clean_body:
            return ()
        clean_body = f"【财经快讯｜{header_time}】\n{clean_body}"
        if event.url:
            clean_body = f"{clean_body}\n原文链接：{event.url}"
        return _split_messages(
            clean_body,
            max_chars=max(200, min(1800, int(max_message_chars))),
        )
    source_line = f"来源：{source_name}｜发布时间：{published_iso}"
    if event.url:
        source_line += f"｜{event.url}"
    if request.related_event_records:
        related_sources = "；".join(
            f"{record.event.source or record.event.provider}｜"
            f"{_event_time_iso(record.event.published_at)}｜{record.event.url or '无 URL'}"
            for record in request.related_event_records[:8]
        )
        source_line += f"\n补充来源：{related_sources}"
    related_titles = "；".join(record.event.title for record in request.related_event_records[:8])
    confirmed_fact = event.title
    if related_titles:
        confirmed_fact += f"；同批次补充事件：{related_titles}"
    code_text = "、".join(
        dict.fromkeys(
            [event.code, *(record.event.code for record in request.related_event_records if record.event.code)]
        )
    )

    validated_quote = event.content_type in {"quote_move", "daily_close"} and "validated_quote" in event.labels
    if validated_quote:
        body = "\n".join(
            [
                f"已确认事实：{confirmed_fact}",
                f"客观数据与时间：证券代码 {code_text}；行情事实与 as_of 均来自程序质量门禁，事件进入分析时间 {published_iso}。",
                f"分析推断：{body}",
                "尚待验证与风险：模型解释不能修改上述价格、昨收、涨跌幅或数据时间；"
                "补充新闻若与行情事实冲突，应放弃该新闻推断，而不是覆盖行情数据。",
                "接下来观察：等待下一次通过同等质量门禁的独立行情观察，并核验是否跨越新的涨跌档位或形成新收盘日线。",
            ]
        )
    elif not all(marker in body for marker in _STRUCTURE_MARKERS):
        body = "\n".join(
            [
                f"已确认事实：{confirmed_fact}",
                f"客观数据与时间：证券代码 {code_text}；主事件发布时间 {published_iso}。",
                f"分析推断：{body}",
                "尚待验证与风险：当前直接证据仅包含事件结构化字段，标题不等于完整正文；"
                "如事件与行情同时出现，也不能据此确认因果。",
                "接下来观察：等待完整公告或报道正文，并结合带 as_of 的行情与后续披露继续核验。",
            ]
        )
    if not body.startswith("【市场快讯"):
        body = f"{header}\n{body}"
    if (
        "来源：" not in body
        or "发布时间：" not in body
        or (event.url and event.url not in body)
        or (request.related_event_records and "补充来源：" not in body)
    ):
        body = f"{body}\n{source_line}"
    return _split_messages(body, max_chars=max(200, min(1800, int(max_message_chars))))


def _direct_news_relay_message(request: FinanceAnalysisRequest) -> str:
    event = request.event_record.event
    if "direct_relay" not in event.labels:
        return ""
    published_iso = _event_time_iso(event.published_at)
    lines = [
        "【东方财富 7×24 快讯原文转发】",
        event.title,
        f"来源：{event.source or event.provider}｜发布时间：{published_iso}",
    ]
    if event.url:
        lines.append(f"原文：{event.url}")
    lines.append("说明：这是来源原文/摘要转发，不代表相关事项已经获得官方确认。")
    return "\n".join(lines)


def _clean_source_report_body(text: str, *, source_url: str) -> str:
    removable_prefixes = (
        "已确认事实：",
        "已确认事实:",
        "客观数据与时间：",
        "客观数据与时间:",
        "分析推断：",
        "分析推断:",
        "尚待验证与风险：",
        "尚待验证与风险:",
    )
    discard_prefixes = ("接下来观察：", "接下来观察:")
    discard_fragments = (
        "当前直接证据仅包含事件结构化字段",
        "等待完整公告或报道正文",
    )
    metadata_prefixes = (
        "来源：",
        "来源:",
        "发布时间：",
        "发布时间:",
        "原文链接：",
        "原文链接:",
        "原文：",
        "原文:",
    )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith(("【市场快讯", "【财经快讯")) or re.fullmatch(
            r"【[^】]*快讯[^】]*】",
            line,
        ):
            continue
        if line.startswith(metadata_prefixes):
            continue
        if line.startswith(discard_prefixes) or any(fragment in line for fragment in discard_fragments):
            continue
        if source_url:
            line = line.replace(source_url, "").strip(" ｜|：:，,；;。")
        for prefix in removable_prefixes:
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if not line or line in seen:
            continue
        seen.add(line)
        cleaned.append(line)
    return "\n".join(cleaned)


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
