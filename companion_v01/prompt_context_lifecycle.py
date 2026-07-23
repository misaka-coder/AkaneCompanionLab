from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class PromptContextLifecycle(str, Enum):
    """How a host context contribution reaches the model over time."""

    STABLE = "stable"
    TURN = "turn"
    EVENT_BACKED = "event_backed"

    @classmethod
    def coerce(cls, value: Any) -> "PromptContextLifecycle":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            # Unknown providers stay visible for the current turn.  Silently
            # dropping an undeclared contribution would trade UX for cache.
            return cls.TURN


@dataclass(frozen=True)
class PromptContextContribution:
    name: str
    content: str | Callable[[], Any]
    lifecycle: PromptContextLifecycle | str = PromptContextLifecycle.TURN
    enabled: bool = True


@dataclass(frozen=True)
class MaterializedPromptContexts:
    sections: tuple[tuple[str, str, bool], ...]
    skipped_event_backed: tuple[str, ...]


def materialize_prompt_contexts(
    contributions: list[PromptContextContribution] | tuple[PromptContextContribution, ...],
    *,
    event_timeline_authoritative: bool,
) -> MaterializedPromptContexts:
    """Resolve context lazily without knowing any feature or Bot names.

    ``EVENT_BACKED`` means the producer already emits state transitions into
    the authoritative append-only timeline.  Such a producer is skipped only
    while that projection is active; all other modes fall back to a visible
    per-turn contribution.
    """

    sections: list[tuple[str, str, bool]] = []
    skipped: list[str] = []
    for contribution in contributions:
        name = str(contribution.name or "").strip()
        if not contribution.enabled or not name:
            continue
        lifecycle = PromptContextLifecycle.coerce(contribution.lifecycle)
        if lifecycle is PromptContextLifecycle.EVENT_BACKED and event_timeline_authoritative:
            skipped.append(name)
            continue
        raw = contribution.content() if callable(contribution.content) else contribution.content
        text = str(raw or "").strip()
        if not text:
            continue
        sections.append((name, text, lifecycle is not PromptContextLifecycle.STABLE))
    return MaterializedPromptContexts(
        sections=tuple(sections),
        skipped_event_backed=tuple(skipped),
    )
