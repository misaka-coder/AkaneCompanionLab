from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from services.market_data import FinanceSubscription, MarketEvent


IMPORTANCE_LEVELS = ("archive", "digest", "notify", "alert")
_LEVEL_RANK = {level: index for index, level in enumerate(IMPORTANCE_LEVELS)}

_CONTENT_TYPE_BASE_SCORES = {
    "companynews": 0.45,
    "news": 0.40,
    "report": 0.68,
    "regularreport": 0.72,
    "tradeinfo": 0.68,
    "announcement": 0.70,
    "quote_move": 0.52,
    "daily_close": 0.34,
    "news_flash": 0.58,
}
_ALERT_KEYWORDS = (
    "重大资产重组",
    "停牌",
    "复牌",
    "退市",
    "立案",
    "处罚",
    "违约",
    "爆雷",
    "控制权变更",
)
_MATERIAL_KEYWORDS = (
    "业绩",
    "季度",
    "经营数据",
    "回购",
    "增持",
    "减持",
    "分红",
    "订单",
    "中标",
    "公告",
)
_MATERIAL_LABELS = frozenset(
    {
        "announcement",
        "buyback",
        "earnings",
        "insider",
        "penalty",
        "regularreport",
        "suspension",
        "tradeinfo",
    }
)


@dataclass(frozen=True)
class ImportanceDecision:
    level: str
    score: float
    should_analyze: bool
    should_deliver: bool
    minimum_level: str
    reasons: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "score": self.score,
            "should_analyze": self.should_analyze,
            "should_deliver": self.should_deliver,
            "minimum_level": self.minimum_level,
            "reasons": list(self.reasons),
        }


class FinanceEventImportancePolicy:
    def evaluate(
        self,
        *,
        event: MarketEvent,
        subscription: FinanceSubscription,
        watchlist_priority: float = 0.0,
        cluster_already_delivered: bool = False,
    ) -> ImportanceDecision:
        minimum_level = self._minimum_level(subscription)
        if cluster_already_delivered:
            return ImportanceDecision(
                level="archive",
                score=0.0,
                should_analyze=False,
                should_deliver=False,
                minimum_level=minimum_level,
                reasons=("same_cluster_already_delivered",),
            )

        content_type = str(event.content_type or "").strip().lower()
        score = _CONTENT_TYPE_BASE_SCORES.get(content_type, 0.30)
        reasons = [f"content_type:{content_type or 'unknown'}"]
        title = str(event.title or "")

        if self._contains_any(title, _ALERT_KEYWORDS):
            score += 0.30
            reasons.append("alert_keyword")
        elif self._contains_any(title, _MATERIAL_KEYWORDS):
            score += 0.18
            reasons.append("material_keyword")

        labels = {str(item or "").strip().lower() for item in event.labels if str(item or "").strip()}
        if labels.intersection(_MATERIAL_LABELS):
            score += 0.12
            reasons.append("material_label")
        if "move_ge_5pct" in labels:
            score += 0.24
            reasons.append("deterministic_move_ge_5pct")
        elif "move_ge_3pct" in labels:
            score += 0.18
            reasons.append("deterministic_move_ge_3pct")
        if "direct_relay" in labels:
            score = max(score, 0.58)
            reasons.append("authorized_direct_news_relay")

        priority = max(0.0, min(1.0, float(watchlist_priority or 0.0)))
        if priority > 0:
            score += 0.15 * priority
            reasons.append(f"watchlist_priority:{priority:.2f}")

        if str(event.sentiment or "").strip().lower() not in {"", "unknown", "neutral", "none"}:
            score += 0.04
            reasons.append("non_neutral_sentiment_field")

        score = round(max(0.0, min(1.0, score)), 4)
        level = self._score_level(score)
        immediate_policy = minimum_level in {"notify", "alert"}
        qualifies = immediate_policy and _LEVEL_RANK[level] >= _LEVEL_RANK[minimum_level]
        should_deliver = qualifies and level in {"notify", "alert"}
        return ImportanceDecision(
            level=level,
            score=score,
            should_analyze=should_deliver,
            should_deliver=should_deliver,
            minimum_level=minimum_level,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _minimum_level(subscription: FinanceSubscription) -> str:
        policy = dict(subscription.delivery_policy or {})
        raw = str(policy.get("minimum_level") or policy.get("level") or "notify").strip().lower()
        return raw if raw in IMPORTANCE_LEVELS else "notify"

    @staticmethod
    def _score_level(score: float) -> str:
        if score >= 0.82:
            return "alert"
        if score >= 0.55:
            return "notify"
        if score >= 0.35:
            return "digest"
        return "archive"

    @staticmethod
    def _contains_any(text: str, needles: Iterable[str]) -> bool:
        return any(needle and needle in text for needle in needles)
