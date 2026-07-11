from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from services.market_data import PublicNewsItem


@dataclass(frozen=True)
class FinanceNewsModerationDecision:
    allowed: bool
    reason: str
    confidence: float = 0.0


class FinanceNewsModerationClient:
    """Batch LLM moderation with fail-closed parsing."""

    def __init__(self, llm_runtime: Any) -> None:
        self.llm_runtime = llm_runtime

    def moderate_items(
        self,
        items: Iterable[PublicNewsItem],
    ) -> dict[str, FinanceNewsModerationDecision]:
        bounded = tuple(items)[:30]
        if not bounded:
            return {}
        fallback = {
            "decisions": [
                {
                    "item_id": item.item_id,
                    "decision": "block",
                    "reason": "moderation_unavailable",
                    "confidence": 0.0,
                }
                for item in bounded
            ]
        }
        payload = [
            {
                "item_id": item.item_id,
                "source": item.source,
                "title": item.title,
                "summary": item.summary,
            }
            for item in bounded
        ]
        try:
            result = self.llm_runtime.call_chat_json(
                system_prompt=(
                    "你是财经快讯转发前的内容审核器，只做分类，不改写、不总结、不调用工具。"
                    "若内容实质涉及中国国内高级政治人物、中共中央/党中央、中央政治局、政治局常委、"
                    "总书记、国家主席、中央军委、党和国家领导人活动或同义改写，decision=block。"
                    "国外政治人物与外国政府新闻本身允许，例如特朗普、高市早苗；但同一条同时涉及上述"
                    "中国国内敏感主体时仍 block。中央银行、欧洲中央银行、中央气象台等非政治语义本身不阻断。"
                    "不确定、信息不足、存在隐晦指代时 block。仅输出 JSON："
                    '{"decisions":[{"item_id":"...","decision":"allow|block",'
                    '"reason":"短原因","confidence":0.0}]}。'
                ),
                user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                fallback=fallback,
                temperature=0.0,
                prompt_cache_key="finance_news_moderation_v1",
            )
        except Exception:
            result = fallback
        rows = result.get("decisions") if isinstance(result, dict) else None
        by_id: dict[str, FinanceNewsModerationDecision] = {}
        if isinstance(rows, list):
            expected_ids = {item.item_id for item in bounded}
            for row in rows[: len(bounded) * 2]:
                if not isinstance(row, dict):
                    continue
                item_id = str(row.get("item_id") or "").strip()
                if item_id not in expected_ids or item_id in by_id:
                    continue
                decision = str(row.get("decision") or "").strip().lower()
                reason = str(row.get("reason") or "moderation_unspecified").strip()[:300]
                try:
                    confidence = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
                except (TypeError, ValueError):
                    confidence = 0.0
                by_id[item_id] = FinanceNewsModerationDecision(
                    allowed=decision == "allow",
                    reason=reason if decision in {"allow", "block"} else "invalid_moderation_decision",
                    confidence=confidence,
                )
        for item in bounded:
            by_id.setdefault(
                item.item_id,
                FinanceNewsModerationDecision(False, "missing_moderation_decision", 0.0),
            )
        return by_id


__all__ = ["FinanceNewsModerationClient", "FinanceNewsModerationDecision"]
