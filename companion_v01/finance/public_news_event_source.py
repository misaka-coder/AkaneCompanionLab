from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from services.market_data import (
    EastmoneyFastNewsAdapter,
    MarketEvent,
    MarketEventPollResult,
    MarketEventStore,
    PublicNewsFetchResult,
    PublicNewsItem,
)


GLOBAL_MARKET_CODE = "GLOBAL.MARKET"

DEFAULT_BLOCKED_DOMESTIC_POLITICAL_TERMS = (
    "习近平",
    "总书记",
    "中共中央",
    "党中央",
    "中央政治局",
    "政治局常委",
    "中央军委",
)
DEFAULT_ALLOWED_CENTRAL_PHRASES = (
    "中央银行",
    "欧洲中央银行",
    "中央气象台",
    "中央结算",
    "中央国债登记结算",
)


@dataclass(frozen=True)
class FinanceNewsRelayDecision:
    allowed: bool
    reason: str = ""
    matched_term: str = ""


class FinanceNewsRelayPolicy:
    """Deterministic pre-LLM forwarding policy for public fast news."""

    def __init__(
        self,
        *,
        blocked_terms: Iterable[str] = DEFAULT_BLOCKED_DOMESTIC_POLITICAL_TERMS,
        allowed_central_phrases: Iterable[str] = DEFAULT_ALLOWED_CENTRAL_PHRASES,
        block_remaining_central: bool = True,
    ) -> None:
        self.blocked_terms = tuple(dict.fromkeys(str(item).strip() for item in blocked_terms if str(item).strip()))
        self.allowed_central_phrases = tuple(
            dict.fromkeys(str(item).strip() for item in allowed_central_phrases if str(item).strip())
        )
        self.block_remaining_central = bool(block_remaining_central)

    def evaluate(self, item: PublicNewsItem) -> FinanceNewsRelayDecision:
        return self.evaluate_text(item.title, item.summary)

    def evaluate_text(self, title: str, summary: str = "") -> FinanceNewsRelayDecision:
        """Apply the same deterministic gate to source text or generated analysis."""

        text = f"{str(title or '')}\n{str(summary or '')}"
        for term in self.blocked_terms:
            if term in text:
                return FinanceNewsRelayDecision(False, "blocked_domestic_political_term", term)
        if self.block_remaining_central and "中央" in text:
            residual = text
            for phrase in self.allowed_central_phrases:
                residual = residual.replace(phrase, "")
            if "中央" in residual:
                return FinanceNewsRelayDecision(False, "blocked_standalone_central_term", "中央")
        return FinanceNewsRelayDecision(True)


class FinancePublicNewsEventSource:
    """Normalize one or more public news adapters into persisted market events."""

    provider_id = "public_market"

    def __init__(
        self,
        *,
        store: MarketEventStore,
        adapters: Iterable[Any] | None = None,
        minimum_poll_interval_seconds: int = 15,
        max_event_age_seconds: int = 30 * 60,
        future_tolerance_seconds: int = 5 * 60,
        seen_item_limit: int = 500,
        moderation_defer_max_attempts: int = 3,
        relay_policy: FinanceNewsRelayPolicy | None = None,
        moderator: Any | None = None,
        require_llm_moderation: bool = False,
        clock=time.time,
    ) -> None:
        self.store = store
        self.adapters = tuple(adapters or (EastmoneyFastNewsAdapter(),))
        if not self.adapters:
            raise ValueError("at least one public news adapter is required")
        self.minimum_poll_interval_seconds = max(5, min(10 * 60, int(minimum_poll_interval_seconds)))
        self.max_event_age_seconds = max(60, min(24 * 60 * 60, int(max_event_age_seconds)))
        self.future_tolerance_seconds = max(30, min(30 * 60, int(future_tolerance_seconds)))
        self.seen_item_limit = max(50, min(5000, int(seen_item_limit)))
        self.moderation_defer_max_attempts = max(1, min(20, int(moderation_defer_max_attempts)))
        self.relay_policy = relay_policy or FinanceNewsRelayPolicy()
        self.moderator = moderator
        self.require_llm_moderation = bool(require_llm_moderation)
        self._clock = clock

    def poll_market_events(self, *, limit: int = 100) -> MarketEventPollResult:
        bounded_limit = max(1, min(1000, int(limit)))
        now = max(1, int(self._clock()))
        if not self._has_interested_subscription():
            return MarketEventPollResult(
                ok=True,
                status="empty",
                provider=self.provider_id,
                source="Public Finance News",
                events=(),
                reason="no_public_news_subscription",
            )

        events: list[MarketEvent] = []
        raw_count = 0
        ignored_count = 0
        successful_sources = 0
        failed_sources = 0
        source_names: list[str] = []
        for adapter in self.adapters:
            if len(events) >= bounded_limit:
                break
            adapter_id = str(getattr(adapter, "adapter_id", "") or "").strip()
            source_name = str(getattr(adapter, "source_name", adapter_id) or adapter_id).strip()
            if not adapter_id:
                failed_sources += 1
                continue
            source_names.append(source_name)
            source_state_id = f"public_news:{adapter_id}"
            stored = self.store.get_event_source_state(source_state_id)
            state = dict(stored.state) if stored is not None else {}
            last_polled_at = max(0, int(state.get("last_polled_at") or 0))
            if last_polled_at and now - last_polled_at < self.minimum_poll_interval_seconds:
                successful_sources += 1
                continue
            try:
                fetched: PublicNewsFetchResult = adapter.fetch_latest(limit=max(bounded_limit, 100))
            except Exception as exc:
                failed_sources += 1
                self._record_rejection(
                    now=now,
                    stage="news_upstream",
                    reason=f"{adapter_id}:{type(exc).__name__}",
                )
                self._save_state(source_state_id, state, now=now)
                continue
            raw_count += len(fetched.items)
            if not fetched.ok:
                failed_sources += 1
                self._record_rejection(
                    now=now,
                    stage="news_upstream",
                    reason=f"{adapter_id}:{fetched.status}:{fetched.reason}",
                )
                self._save_state(source_state_id, state, now=now)
                continue
            successful_sources += 1
            adapter_events, adapter_ignored = self._consume_items(
                adapter_id=adapter_id,
                items=fetched.items,
                state=state,
                source_state_id=source_state_id,
                now=now,
                limit=bounded_limit - len(events),
            )
            events.extend(adapter_events)
            ignored_count += adapter_ignored

        if successful_sources == 0 and failed_sources > 0:
            return MarketEventPollResult(
                ok=False,
                status="unavailable",
                provider=self.provider_id,
                source=" + ".join(dict.fromkeys(source_names)) or "Public Finance News",
                events=(),
                raw_event_count=raw_count,
                ignored_count=ignored_count,
                reason="all_public_news_sources_unavailable",
            )
        return MarketEventPollResult(
            ok=True,
            status="ok" if events else "empty",
            provider=self.provider_id,
            source=" + ".join(dict.fromkeys(source_names)) or "Public Finance News",
            events=tuple(events),
            raw_event_count=raw_count,
            ignored_count=ignored_count,
            reason="partial_public_news_failure" if failed_sources else "",
        )

    def _consume_items(
        self,
        *,
        adapter_id: str,
        items: tuple[PublicNewsItem, ...],
        state: dict[str, Any],
        source_state_id: str,
        now: int,
        limit: int,
    ) -> tuple[list[MarketEvent], int]:
        previous_seen = [str(item) for item in state.get("seen_item_ids", []) if str(item).strip()]
        seen = set(previous_seen)
        current_ids = [item.item_id for item in items]
        moderation_attempts = {
            str(item_id): max(0, int(attempts or 0))
            for item_id, attempts in dict(state.get("moderation_attempts") or {}).items()
            if str(item_id).strip()
        }
        if not state.get("baseline_seeded"):
            self._save_state(
                source_state_id,
                {
                    "baseline_seeded": True,
                    "seen_item_ids": current_ids[: self.seen_item_limit],
                    "latest_published_at": max((item.published_at for item in items), default=0),
                },
                now=now,
            )
            return [], len(items)

        emitted: list[MarketEvent] = []
        ignored = 0
        candidates: list[PublicNewsItem] = []
        finalized_ids: list[str] = []
        for item in sorted(items, key=lambda value: (value.published_at, value.item_id)):
            if item.item_id in seen:
                ignored += 1
                continue
            if item.published_at > now + self.future_tolerance_seconds:
                ignored += 1
                self._record_rejection(
                    now=now,
                    stage="news_quality",
                    reason=f"{adapter_id}:future_published_at",
                    payload=item.to_public_dict(),
                )
                continue
            if now - item.published_at > self.max_event_age_seconds:
                ignored += 1
                finalized_ids.append(item.item_id)
                moderation_attempts.pop(item.item_id, None)
                continue
            relay_decision = self.relay_policy.evaluate(item)
            if not relay_decision.allowed:
                ignored += 1
                self._record_rejection(
                    now=now,
                    stage="news_policy",
                    reason=f"{relay_decision.reason}:{relay_decision.matched_term}",
                    payload=item.to_public_dict(),
                )
                finalized_ids.append(item.item_id)
                moderation_attempts.pop(item.item_id, None)
                continue
            candidates.append(item)

        moderation_decisions: dict[str, Any] = {}
        if candidates and self.moderator is not None:
            try:
                moderation_decisions = dict(self.moderator.moderate_items(tuple(candidates)))
            except Exception:
                moderation_decisions = {}
        for item in candidates:
            moderation = moderation_decisions.get(item.item_id)
            if self.require_llm_moderation and not bool(getattr(moderation, "allowed", False)):
                ignored += 1
                reason = str(getattr(moderation, "reason", "moderation_unavailable") or "moderation_unavailable")
                retryable = bool(getattr(moderation, "retryable", moderation is None))
                if retryable:
                    attempt_count = moderation_attempts.get(item.item_id, 0) + 1
                    moderation_attempts[item.item_id] = attempt_count
                    if attempt_count < self.moderation_defer_max_attempts:
                        self._record_rejection(
                            now=now,
                            stage="news_moderation_retry",
                            reason=f"{item.adapter_id}:{reason}:attempt_{attempt_count}",
                            payload=item.to_public_dict(),
                        )
                        continue
                    reason = f"{reason}:retry_exhausted_{attempt_count}"
                self._record_rejection(
                    now=now,
                    stage="news_moderation",
                    reason=f"{item.adapter_id}:{reason}",
                    payload=item.to_public_dict(),
                )
                finalized_ids.append(item.item_id)
                moderation_attempts.pop(item.item_id, None)
                continue
            codes = self._resolve_item_codes(item)
            emitted_for_item = False
            for code in codes:
                if len(emitted) >= max(0, limit):
                    break
                emitted.append(self._build_event(item=item, code=code, now=now))
                emitted_for_item = True
            if emitted_for_item:
                finalized_ids.append(item.item_id)
                moderation_attempts.pop(item.item_id, None)
            if len(emitted) >= max(0, limit):
                break

        current_id_set = set(current_ids)
        moderation_attempts = {
            item_id: attempts
            for item_id, attempts in moderation_attempts.items()
            if item_id in current_id_set and item_id not in finalized_ids and item_id not in seen
        }
        merged_seen = list(dict.fromkeys([*finalized_ids, *previous_seen]))[: self.seen_item_limit]
        self._save_state(
            source_state_id,
            {
                "baseline_seeded": True,
                "seen_item_ids": merged_seen,
                "moderation_attempts": moderation_attempts,
                "latest_published_at": max(
                    [max((item.published_at for item in items), default=0), int(state.get("latest_published_at") or 0)]
                ),
            },
            now=now,
        )
        return emitted, ignored

    def _resolve_item_codes(self, item: PublicNewsItem) -> tuple[str, ...]:
        text = " ".join(part for part in (item.title, item.summary) if part)
        codes: list[str] = []
        for candidate in self.store.resolve_security(text, provider=self.provider_id, limit=20):
            if str(candidate.get("match_type") or "") not in {"exact", "embedded"}:
                continue
            code = str(candidate.get("code") or "").strip().upper()
            if code and code not in codes:
                codes.append(code)
        normalized_text = _normalize_alias(text)
        for subscription in self.store.list_subscriptions(enabled=True):
            if subscription.finance_mode != "push":
                continue
            for watched in self.store.list_watchlist(subscription.subscription_id):
                if watched.provider != self.provider_id:
                    continue
                aliases = (watched.code, watched.display_name, *watched.aliases)
                if any(_alias_is_embedded(alias, normalized_text) for alias in aliases):
                    if watched.code not in codes:
                        codes.append(watched.code)
        return tuple(codes[:20]) or (GLOBAL_MARKET_CODE,)

    def _build_event(self, *, item: PublicNewsItem, code: str, now: int) -> MarketEvent:
        market_wide = code == GLOBAL_MARKET_CODE
        labels = list(item.labels)
        labels.append("direct_relay")
        labels.append("optional_model_analysis")
        labels.append("market_wide" if market_wide else "security_matched")
        combined = item.title
        if item.summary and item.summary not in combined:
            combined = f"{combined}｜{item.summary}"
        title = f"东方财富7×24快讯：{combined}"[:2000]
        facts = {
            "adapter_id": item.adapter_id,
            "item_id": item.item_id,
            "published_at": item.published_at,
            "code": code,
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
        }
        material = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        event_material = f"{item.adapter_id}|{item.item_id}|{code}"
        event_id = f"public_news:{hashlib.sha256(event_material.encode('utf-8')).hexdigest()[:40]}"
        return MarketEvent(
            provider=self.provider_id,
            event_id=event_id,
            published_at=item.published_at,
            produced_at=item.fetched_at,
            received_at=now,
            code=code,
            content_type="news_flash",
            title=title,
            source=item.source,
            url=item.url,
            sentiment="unknown",
            labels=tuple(dict.fromkeys(labels)),
            sector_code="",
            raw_hash=raw_hash,
        )

    def _has_interested_subscription(self) -> bool:
        for subscription in self.store.list_subscriptions(enabled=True):
            if subscription.finance_mode != "push":
                continue
            if bool(dict(subscription.filters).get("include_market_wide")):
                return True
            if any(
                item.provider == self.provider_id for item in self.store.list_watchlist(subscription.subscription_id)
            ):
                return True
        return False

    def _save_state(self, source_id: str, state: dict[str, Any], *, now: int) -> None:
        payload = dict(state)
        payload["last_polled_at"] = now
        self.store.upsert_event_source_state(
            source_id=source_id,
            cursor=str(payload.get("latest_published_at") or ""),
            state=payload,
            now_ts=now,
        )

    def _record_rejection(
        self,
        *,
        now: int,
        stage: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        payload_hash = ""
        if payload:
            material = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            payload_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self.store.record_market_data_rejection(
            provider=self.provider_id,
            code=GLOBAL_MARKET_CODE,
            observed_at=now,
            stage=stage,
            reason=str(reason or "unknown_news_rejection")[:1000],
            payload_hash=payload_hash,
            now_ts=now,
        )


def _normalize_alias(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _alias_is_embedded(alias: str, normalized_text: str) -> bool:
    normalized = _normalize_alias(alias)
    minimum = 2 if any("\u3400" <= char <= "\u9fff" for char in normalized) else 3
    return len(normalized) >= minimum and normalized in normalized_text


__all__ = [
    "DEFAULT_ALLOWED_CENTRAL_PHRASES",
    "DEFAULT_BLOCKED_DOMESTIC_POLITICAL_TERMS",
    "FinanceNewsRelayDecision",
    "FinanceNewsRelayPolicy",
    "FinancePublicNewsEventSource",
    "GLOBAL_MARKET_CODE",
]
