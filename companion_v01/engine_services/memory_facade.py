"""Memory service facades extracted from engine.py."""

from __future__ import annotations
from typing import Any, Callable


def collect_visible_context_source_ids(
    *, recent_raw, recent_episodic_summaries, recent_semantic_summaries, extra_source_ids=None
):
    from .. import retrieval_engine

    return retrieval_engine.collect_visible_context_source_ids(
        recent_raw=recent_raw,
        recent_episodic_summaries=recent_episodic_summaries,
        recent_semantic_summaries=recent_semantic_summaries,
        extra_source_ids=extra_source_ids,
    )
