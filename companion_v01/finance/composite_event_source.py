from __future__ import annotations

from typing import Iterable

from services.market_data import MarketEventPollResult


class FinanceCompositeEventSource:
    """Poll independent event sources without letting one outage hide the others."""

    def __init__(self, *, sources: Iterable[object], provider_id: str = "public_market") -> None:
        self.sources = tuple(sources)
        if not self.sources:
            raise ValueError("at least one finance event source is required")
        self.provider_id = str(provider_id or "public_market").strip() or "public_market"

    def poll_market_events(self, *, limit: int = 100) -> MarketEventPollResult:
        bounded_limit = max(1, min(1000, int(limit)))
        events = []
        event_ids: set[str] = set()
        raw_count = 0
        ignored_count = 0
        ok_sources = 0
        failed_sources = 0
        sources: list[str] = []
        reasons: list[str] = []
        for source in self.sources:
            if len(events) >= bounded_limit:
                break
            try:
                result = source.poll_market_events(limit=bounded_limit - len(events))
            except Exception as exc:
                failed_sources += 1
                reasons.append(f"{type(source).__name__}:{type(exc).__name__}")
                continue
            sources.append(str(result.source or ""))
            raw_count += int(result.raw_event_count)
            ignored_count += int(result.ignored_count)
            if not result.ok:
                failed_sources += 1
                reasons.append(result.reason or result.status)
                continue
            ok_sources += 1
            for event in result.events:
                if event.event_id in event_ids:
                    ignored_count += 1
                    continue
                event_ids.add(event.event_id)
                events.append(event)
                if len(events) >= bounded_limit:
                    break
        if ok_sources == 0:
            return MarketEventPollResult(
                ok=False,
                status="unavailable",
                provider=self.provider_id,
                source=" + ".join(dict.fromkeys(item for item in sources if item)) or "Finance Composite Source",
                events=(),
                raw_event_count=raw_count,
                ignored_count=ignored_count,
                reason=";".join(dict.fromkeys(reasons))[:1000] or "all_event_sources_unavailable",
            )
        return MarketEventPollResult(
            ok=True,
            status="ok" if events else "empty",
            provider=self.provider_id,
            source=" + ".join(dict.fromkeys(item for item in sources if item)) or "Finance Composite Source",
            events=tuple(events),
            raw_event_count=raw_count,
            ignored_count=ignored_count,
            reason=(
                "partial_event_source_failure:" + ";".join(dict.fromkeys(reasons))[:900]
                if failed_sources
                else ""
            ),
        )


__all__ = ["FinanceCompositeEventSource"]
