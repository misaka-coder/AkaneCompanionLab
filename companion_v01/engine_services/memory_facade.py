"""Memory service facades extracted from engine.py."""

from __future__ import annotations
from typing import Any, Callable
from ..retrieval_service import RetrievalService
from ..memory_compaction_service import MemoryCompactionService


def get_retrieval_service(*, store, vector_store, llm, prompt_builder, cached=None):
    if cached is not None:
        return cached
    return RetrievalService(store=store, vector_store=vector_store, llm=llm, prompt_builder=prompt_builder)


def get_compaction_service(*, store, vector_store, llm, prompt_builder, persona_context_provider, cached=None):
    if cached is not None:
        return cached
    return MemoryCompactionService(
        store=store,
        vector_store=vector_store,
        llm=llm,
        prompt_builder=prompt_builder,
        persona_context_provider=persona_context_provider,
    )


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


def schedule_summary_cycle(*, profile_user_id, session_id, character_pack_id, compaction_service):
    compaction_service.schedule_summary_cycle(
        profile_user_id=profile_user_id, session_id=session_id, character_pack_id=character_pack_id
    )


def run_summary_cycle(*, profile_user_id, session_id, character_pack_id, compaction_service):
    compaction_service.run_summary_cycle(
        profile_user_id=profile_user_id, session_id=session_id, character_pack_id=character_pack_id
    )
