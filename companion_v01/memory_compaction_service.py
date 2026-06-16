from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import config

from .llm_runtime import LLMRuntime
from . import final_output_engine
from .memory_rendering import (
    render_semantic_summary_snippet,
    render_summary_timeline,
    resolve_record_time_range,
)
from .prompt_builder import PromptBuilder
from .store import MemoryStore
from .summary_queue import SummaryTask, SummaryTaskQueue
from .text_utils import (
    extract_semantic_tags,
    infer_time_of_day,
    join_tags,
    normalize_text,
    render_chat_timeline,
    timestamp_to_date_label,
)
from .vector_entry_builder import build_semantic_summary_vector_entry, build_summary_vector_entry
from .vector_store import VectorStore


class MemoryCompactionService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        vector_store: VectorStore,
        llm: LLMRuntime,
        prompt_builder: PromptBuilder,
        persona_context_provider: Callable[..., dict[str, Any]] | None = None,
    ):
        self.store = store
        self.vector_store = vector_store
        self.llm = llm
        self.prompt_builder = prompt_builder
        self.persona_context_provider = persona_context_provider
        self._summary_generation = 0
        self._summary_generation_lock = threading.Lock()
        self.summary_queue = SummaryTaskQueue(self._process_summary_task)

    def reset(self) -> None:
        self._bump_summary_generation()
        self.summary_queue.clear_pending()

    def close(self) -> None:
        self._bump_summary_generation()
        self.summary_queue.clear_pending()
        self.summary_queue.close()

    def schedule_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        self.summary_queue.enqueue(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            generation=self._get_summary_generation(),
        )

    def run_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        self._run_summary_cycle_with_generation(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            generation=self._get_summary_generation(),
        )

    def _process_summary_task(self, task: SummaryTask) -> None:
        self._run_summary_cycle_with_generation(
            profile_user_id=task.profile_user_id,
            session_id=task.session_id,
            character_pack_id=task.character_pack_id,
            generation=task.generation,
        )

    def _run_summary_cycle_with_generation(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        generation: int,
    ) -> None:
        summary_trigger_count = max(1, int(getattr(config, "SUMMARY_TRIGGER_COUNT", 30)))
        summary_batch_size = max(1, int(getattr(config, "SUMMARY_BATCH_SIZE", 20)))
        while self.store.get_unsummarized_count(session_id, character_pack_id=character_pack_id) >= summary_trigger_count:
            if not self._is_summary_generation_current(generation):
                return
            batch = self.store.get_oldest_unsummarized_batch(
                session_id,
                limit=summary_batch_size,
                character_pack_id=character_pack_id,
            )
            if len(batch) < summary_batch_size:
                return
            summary_payload = self._summarize_batch(
                batch,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            if not self._is_summary_generation_current(generation):
                return
            summary_tags = extract_semantic_tags(
                " ".join(
                    [summary_payload.get("diary_summary", "")]
                    + list(summary_payload.get("key_events") or [])
                    + list(summary_payload.get("core_facts") or [])
                ),
                limit=8,
            )
            summary_importance = self._coerce_importance(summary_payload.get("importance", 0.5))
            summary_metadata = self._normalize_memory_metadata(
                summary_payload.get("memory_metadata"),
                fallback_keywords=summary_tags,
                fallback_importance=summary_importance,
            )
            summary_record = self.store.add_summary(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                timestamp=batch[-1]["timestamp"],
                date_label=batch[-1]["date_label"],
                time_of_day=batch[-1]["time_of_day"],
                period_label=summary_payload.get("period_label", ""),
                event_type=summary_payload.get("event_type", "日常"),
                importance=summary_importance,
                diary_summary=summary_payload.get("diary_summary", ""),
                key_events=list(summary_payload.get("key_events") or []),
                core_facts=list(summary_payload.get("core_facts") or []),
                semantic_tags=summary_tags,
                source_start_seq=batch[0]["seq_no"],
                source_end_seq=batch[-1]["seq_no"],
                source_ids=[item["source_id"] for item in batch],
                memory_metadata=summary_metadata,
            )
            self.store.mark_messages_summarized([item["source_id"] for item in batch], summary_record["summary_id"])
            self._upsert_summary_record(summary_record)
        self._run_semantic_summary_cycle_with_generation(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            generation=generation,
        )

    def _run_semantic_summary_cycle_with_generation(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        generation: int,
    ) -> None:
        if not bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True)):
            return
        semantic_trigger_count = max(1, int(getattr(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 10)))
        semantic_batch_size = max(1, int(getattr(config, "EPISODIC_COMPACT_BATCH_SIZE", 5)))
        while self.store.get_unsemanticized_summary_count(session_id, character_pack_id=character_pack_id) >= semantic_trigger_count:
            if not self._is_summary_generation_current(generation):
                return
            batch = self.store.get_oldest_unsemanticized_summaries(
                session_id,
                limit=semantic_batch_size,
                character_pack_id=character_pack_id,
            )
            if len(batch) < semantic_batch_size:
                return
            semantic_payload = self._semanticize_summary_batch(
                batch,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            if not self._is_summary_generation_current(generation):
                return
            period_start_ts = min(
                int(resolve_record_time_range(item, store=self.store)[0] or item.get("timestamp") or 0)
                for item in batch
            )
            period_end_ts = max(
                int(resolve_record_time_range(item, store=self.store)[1] or item.get("timestamp") or 0)
                for item in batch
            )
            semantic_text = " ".join(
                [semantic_payload.get("semantic_summary", "")]
                + list(semantic_payload.get("stable_facts") or [])
                + list(semantic_payload.get("recurring_topics") or [])
                + list(semantic_payload.get("important_people") or [])
                + list(semantic_payload.get("open_loops") or [])
            )
            semantic_tags = extract_semantic_tags(semantic_text, limit=10)
            incoming_importance = self._coerce_importance(semantic_payload.get("importance", 0.6))
            incoming_metadata = self._normalize_memory_metadata(
                semantic_payload.get("memory_metadata"),
                fallback_keywords=semantic_tags,
                fallback_importance=incoming_importance,
            )
            incoming_record = {
                "profile_user_id": profile_user_id,
                "session_id": session_id,
                "character_pack_id": character_pack_id,
                "timestamp": period_end_ts,
                "period_start_ts": period_start_ts,
                "period_end_ts": period_end_ts,
                "date_label": timestamp_to_date_label(period_end_ts),
                "time_of_day": infer_time_of_day(period_end_ts),
                "importance": incoming_importance,
                "semantic_summary": str(semantic_payload.get("semantic_summary") or "").strip(),
                "stable_facts": list(semantic_payload.get("stable_facts") or []),
                "recurring_topics": list(semantic_payload.get("recurring_topics") or []),
                "important_people": list(semantic_payload.get("important_people") or []),
                "open_loops": list(semantic_payload.get("open_loops") or []),
                "semantic_tags": semantic_tags,
                "memory_metadata": incoming_metadata,
                "source_summary_ids": [item["summary_id"] for item in batch],
            }
            reinforcement_target = self._select_semantic_reinforcement_target(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                incoming_record=incoming_record,
            )
            if reinforcement_target:
                semantic_record = self._reinforce_semantic_summary_record(
                    existing_record=reinforcement_target,
                    incoming_record=incoming_record,
                )
            else:
                semantic_record = self.store.add_semantic_summary(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    timestamp=incoming_record["timestamp"],
                    period_start_ts=incoming_record["period_start_ts"],
                    period_end_ts=incoming_record["period_end_ts"],
                    date_label=incoming_record["date_label"],
                    time_of_day=incoming_record["time_of_day"],
                    importance=incoming_record["importance"],
                    semantic_summary=incoming_record["semantic_summary"],
                    stable_facts=incoming_record["stable_facts"],
                    recurring_topics=incoming_record["recurring_topics"],
                    important_people=incoming_record["important_people"],
                    open_loops=incoming_record["open_loops"],
                    semantic_tags=incoming_record["semantic_tags"],
                    source_summary_ids=incoming_record["source_summary_ids"],
                    memory_metadata=incoming_record["memory_metadata"],
                )
            self.store.mark_summaries_semanticized(
                [item["summary_id"] for item in batch],
                semantic_record["semantic_id"],
            )
            self._upsert_semantic_summary_record(semantic_record)

    def _get_summary_generation(self) -> int:
        with self._summary_generation_lock:
            return int(self._summary_generation)

    def _bump_summary_generation(self) -> int:
        with self._summary_generation_lock:
            self._summary_generation += 1
            return int(self._summary_generation)

    def _is_summary_generation_current(self, generation: int) -> bool:
        with self._summary_generation_lock:
            return int(generation) == int(self._summary_generation)

    def _summarize_batch(
        self,
        batch: list[dict[str, Any]],
        *,
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        transcript = render_chat_timeline(batch)
        summary_reference_text = ""
        if profile_user_id:
            summary_reference_limit = max(
                1,
                int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))),
            )
            reference_summaries = self.store.get_visible_episodic_summaries(
                profile_user_id,
                limit=summary_reference_limit,
                character_pack_id=character_pack_id,
            )
            summary_reference_text = render_summary_timeline(reference_summaries, store=self.store)
        fallback = {
            "diary_summary": self.prompt_builder.persona.build_summary_fallback_diary(
                join_tags(extract_semantic_tags(transcript, limit=4))
            ),
            "period_label": "日常片段",
            "event_type": "日常",
            "importance": 0.5,
            "key_events": [item["content"][:24] for item in batch[:3]],
            "core_facts": extract_semantic_tags(transcript, limit=4),
            "memory_metadata": {},
        }
        system_prompt, user_prompt = self.prompt_builder.build_summary_prompts(
            transcript=transcript,
            batch_size=len(batch),
            reference_summary_text=summary_reference_text,
            **self._build_memory_persona_prompt_kwargs(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            ),
        )
        result = self.llm.call_aux_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=0.2,
            prompt_cache_key="aux:summary",
        )
        if not isinstance(result.get("key_events"), list):
            result["key_events"] = fallback["key_events"]
        if not isinstance(result.get("core_facts"), list):
            result["core_facts"] = fallback["core_facts"]
        return result

    def _semanticize_summary_batch(
        self,
        batch: list[dict[str, Any]],
        *,
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        source_text = render_summary_timeline(batch, store=self.store)
        source_tags = extract_semantic_tags(source_text, limit=6)
        stable_facts = source_tags[:4]
        recurring_topics = source_tags[:4]
        fallback = {
            "semantic_summary": self.prompt_builder.persona.build_semantic_fallback_summary(join_tags(source_tags)),
            "importance": max(
                0.55,
                min(0.95, sum(float(item.get("importance") or 0.5) for item in batch) / max(1, len(batch))),
            ),
            "stable_facts": stable_facts,
            "recurring_topics": recurring_topics,
            "important_people": [],
            "open_loops": [],
            "memory_metadata": {},
        }
        system_prompt, user_prompt = self.prompt_builder.build_semantic_summary_prompts(
            source_text=source_text,
            **self._build_memory_persona_prompt_kwargs(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            ),
        )
        result = self.llm.call_aux_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=0.2,
            prompt_cache_key="aux:semantic_summary",
        )
        normalized = dict(result or {})
        normalized["semantic_summary"] = str(normalized.get("semantic_summary") or fallback["semantic_summary"]).strip() or fallback["semantic_summary"]
        normalized["importance"] = self._coerce_importance(normalized.get("importance", fallback["importance"]))
        normalized["stable_facts"] = self._normalize_string_list(normalized.get("stable_facts"), limit=6) or fallback["stable_facts"]
        normalized["recurring_topics"] = self._normalize_string_list(normalized.get("recurring_topics"), limit=6) or fallback["recurring_topics"]
        normalized["important_people"] = self._normalize_string_list(normalized.get("important_people"), limit=6)
        normalized["open_loops"] = self._normalize_string_list(normalized.get("open_loops"), limit=6)
        normalized["memory_metadata"] = normalized.get("memory_metadata") if isinstance(normalized.get("memory_metadata"), dict) else fallback["memory_metadata"]
        return normalized

    def _select_semantic_reinforcement_target(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        incoming_record: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not bool(getattr(config, "ENABLE_SEMANTIC_REINFORCEMENT", True)):
            return None
        lookback = max(1, int(getattr(config, "SEMANTIC_REINFORCEMENT_LOOKBACK", 8)))
        min_overlap = max(1, int(getattr(config, "SEMANTIC_REINFORCEMENT_MIN_OVERLAP", 2)))
        candidates = self.store.get_recent_semantic_summaries(
            profile_user_id,
            limit=lookback,
            character_pack_id=character_pack_id,
        )
        best_candidate: dict[str, Any] | None = None
        best_score = 0
        for candidate in candidates:
            score = self._score_semantic_overlap(candidate=candidate, incoming_record=incoming_record)
            if score >= min_overlap and score > best_score:
                best_candidate = candidate
                best_score = score
        return best_candidate

    def _score_semantic_overlap(
        self,
        *,
        candidate: dict[str, Any],
        incoming_record: dict[str, Any],
    ) -> int:
        candidate_tags = {normalize_text(item) for item in list(candidate.get("semantic_tags") or []) if normalize_text(item)}
        incoming_tags = {normalize_text(item) for item in list(incoming_record.get("semantic_tags") or []) if normalize_text(item)}
        candidate_topics = {normalize_text(item) for item in list(candidate.get("recurring_topics") or []) if normalize_text(item)}
        incoming_topics = {normalize_text(item) for item in list(incoming_record.get("recurring_topics") or []) if normalize_text(item)}
        candidate_people = {normalize_text(item) for item in list(candidate.get("important_people") or []) if normalize_text(item)}
        incoming_people = {normalize_text(item) for item in list(incoming_record.get("important_people") or []) if normalize_text(item)}
        candidate_facts = {normalize_text(item) for item in list(candidate.get("stable_facts") or []) if normalize_text(item)}
        incoming_facts = {normalize_text(item) for item in list(incoming_record.get("stable_facts") or []) if normalize_text(item)}

        score = 0
        score += len(candidate_tags.intersection(incoming_tags))
        score += len(candidate_topics.intersection(incoming_topics))
        score += len(candidate_people.intersection(incoming_people))
        if candidate_facts.intersection(incoming_facts):
            score += 1
        return score

    def _reinforce_semantic_summary_record(
        self,
        *,
        existing_record: dict[str, Any],
        incoming_record: dict[str, Any],
    ) -> dict[str, Any]:
        merged_stable_facts = self._merge_unique_strings(
            list(existing_record.get("stable_facts") or []),
            list(incoming_record.get("stable_facts") or []),
            limit=8,
        )
        merged_recurring_topics = self._merge_unique_strings(
            list(existing_record.get("recurring_topics") or []),
            list(incoming_record.get("recurring_topics") or []),
            limit=8,
        )
        merged_important_people = self._merge_unique_strings(
            list(existing_record.get("important_people") or []),
            list(incoming_record.get("important_people") or []),
            limit=8,
        )
        merged_open_loops = self._merge_unique_strings(
            list(existing_record.get("open_loops") or []),
            list(incoming_record.get("open_loops") or []),
            limit=8,
        )
        merged_source_summary_ids = self._merge_unique_strings(
            list(existing_record.get("source_summary_ids") or []),
            list(incoming_record.get("source_summary_ids") or []),
            limit=32,
            max_length=120,
        )
        existing_count = max(1, int(existing_record.get("reinforcement_count") or 1))
        blended_importance = (
            (float(existing_record.get("importance") or 0.5) * existing_count)
            + float(incoming_record.get("importance") or 0.5)
        ) / (existing_count + 1)
        fallback = {
            "semantic_summary": str(existing_record.get("semantic_summary") or incoming_record.get("semantic_summary") or "").strip(),
            "importance": max(
                float(existing_record.get("importance") or 0.5),
                float(incoming_record.get("importance") or 0.5),
                blended_importance,
            ),
            "stable_facts": merged_stable_facts,
            "recurring_topics": merged_recurring_topics,
            "important_people": merged_important_people,
            "open_loops": merged_open_loops,
            "memory_metadata": dict(existing_record.get("memory_metadata") or {}),
        }
        existing_text = render_semantic_summary_snippet(existing_record, store=self.store)
        incoming_text = render_semantic_summary_snippet(incoming_record, store=self.store)
        system_prompt, user_prompt = self.prompt_builder.build_semantic_reinforcement_prompts(
            existing_text=existing_text,
            incoming_text=incoming_text,
            **self._build_memory_persona_prompt_kwargs(
                profile_user_id=str(existing_record.get("profile_user_id") or incoming_record.get("profile_user_id") or ""),
                session_id=str(existing_record.get("session_id") or incoming_record.get("session_id") or ""),
                character_pack_id=str(existing_record.get("character_pack_id") or incoming_record.get("character_pack_id") or ""),
            ),
        )
        result = self.llm.call_aux_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            temperature=0.2,
            prompt_cache_key="aux:semantic_reinforcement",
        )
        normalized = dict(result or {})
        normalized["semantic_summary"] = str(normalized.get("semantic_summary") or fallback["semantic_summary"]).strip() or fallback["semantic_summary"]
        normalized["importance"] = self._coerce_importance(normalized.get("importance", fallback["importance"]))
        normalized["stable_facts"] = self._normalize_string_list(normalized.get("stable_facts"), limit=8) or fallback["stable_facts"]
        normalized["recurring_topics"] = self._normalize_string_list(normalized.get("recurring_topics"), limit=8) or fallback["recurring_topics"]
        normalized["important_people"] = self._normalize_string_list(normalized.get("important_people"), limit=8) or fallback["important_people"]
        normalized["open_loops"] = self._normalize_string_list(normalized.get("open_loops"), limit=8) or fallback["open_loops"]
        normalized["memory_metadata"] = normalized.get("memory_metadata") if isinstance(normalized.get("memory_metadata"), dict) else fallback["memory_metadata"]

        merged_period_start = min(
            int(existing_record.get("period_start_ts") or existing_record.get("timestamp") or incoming_record["period_start_ts"]),
            int(incoming_record.get("period_start_ts") or incoming_record.get("timestamp") or existing_record["timestamp"]),
        )
        merged_period_end = max(
            int(existing_record.get("period_end_ts") or existing_record.get("timestamp") or incoming_record["period_end_ts"]),
            int(incoming_record.get("period_end_ts") or incoming_record.get("timestamp") or existing_record["timestamp"]),
        )
        merged_text = " ".join(
            [normalized["semantic_summary"]]
            + list(normalized.get("stable_facts") or [])
            + list(normalized.get("recurring_topics") or [])
            + list(normalized.get("important_people") or [])
            + list(normalized.get("open_loops") or [])
        )
        semantic_tags = extract_semantic_tags(merged_text, limit=10)
        normalized_metadata = self._normalize_memory_metadata(
            normalized.get("memory_metadata"),
            fallback_keywords=semantic_tags,
            fallback_importance=normalized["importance"],
        )
        updated = self.store.update_semantic_summary(
            semantic_id=existing_record["semantic_id"],
            timestamp=merged_period_end,
            period_start_ts=merged_period_start,
            period_end_ts=merged_period_end,
            date_label=timestamp_to_date_label(merged_period_end),
            time_of_day=infer_time_of_day(merged_period_end),
            importance=normalized["importance"],
            semantic_summary=normalized["semantic_summary"],
            stable_facts=list(normalized.get("stable_facts") or []),
            recurring_topics=list(normalized.get("recurring_topics") or []),
            important_people=list(normalized.get("important_people") or []),
            open_loops=list(normalized.get("open_loops") or []),
            semantic_tags=semantic_tags,
            source_summary_ids=merged_source_summary_ids,
            reinforcement_count=existing_count + 1,
            last_reinforced_ts=int(incoming_record.get("timestamp") or merged_period_end),
            memory_metadata=normalized_metadata,
        )
        return updated or existing_record

    def _coerce_importance(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(max(0.0, min(1.0, float(value))))

        raw = str(value or "").strip().lower()
        if not raw:
            return 0.5

        mapping = {
            "低": 0.25,
            "较低": 0.35,
            "中": 0.5,
            "中等": 0.5,
            "一般": 0.5,
            "较高": 0.75,
            "高": 0.85,
            "很高": 0.95,
            "low": 0.25,
            "medium": 0.5,
            "mid": 0.5,
            "high": 0.85,
        }
        if raw in mapping:
            return mapping[raw]

        try:
            return float(max(0.0, min(1.0, float(raw))))
        except Exception:
            return 0.5

    def _normalize_string_list(
        self,
        value: Any,
        *,
        limit: int = 6,
        max_length: int = 40,
    ) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()

        if isinstance(value, str):
            parts = [segment.strip() for segment in value.split(",")]
        elif isinstance(value, list):
            parts = [str(item).strip() for item in value]
        else:
            parts = []

        for item in parts:
            if not item:
                continue
            compact = normalize_text(item).strip("[](){}\"' ")
            if not compact:
                continue
            compact = compact[:max_length]
            dedupe_key = compact.lower()
            if dedupe_key in seen:
                continue
            items.append(compact)
            seen.add(dedupe_key)
            if len(items) >= limit:
                break

        return items

    def _build_memory_persona_prompt_kwargs(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, str]:
        provider = self.persona_context_provider
        if provider is None:
            return {"persona_system_context": "", "persona_reference_context": ""}
        try:
            context = provider(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        except Exception:
            return {"persona_system_context": "", "persona_reference_context": ""}
        if not isinstance(context, dict):
            return {"persona_system_context": "", "persona_reference_context": ""}
        return {
            "persona_system_context": str(context.get("system_context") or "").strip(),
            "persona_reference_context": str(context.get("reference_context") or "").strip(),
        }

    def _normalize_memory_metadata(
        self,
        value: Any,
        *,
        fallback_keywords: list[str],
        fallback_importance: float,
        fallback_confidence: float = 0.6,
    ) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        metadata = final_output_engine.normalize_memory_metadata(
            self,
            raw,
            legacy_memory_tags=fallback_keywords,
        )
        if not metadata.get("keywords"):
            metadata["keywords"] = self._normalize_string_list(fallback_keywords, limit=4, max_length=16)
        if "importance" not in raw:
            metadata["importance"] = self._coerce_importance(fallback_importance)
        if "confidence" not in raw:
            metadata["confidence"] = float(max(0.0, min(1.0, fallback_confidence)))
        return metadata

    def _merge_unique_strings(
        self,
        existing: list[str],
        incoming: list[str],
        *,
        limit: int,
        max_length: int = 40,
    ) -> list[str]:
        return self._normalize_string_list(
            list(existing or []) + list(incoming or []),
            limit=limit,
            max_length=max_length,
        )

    def _upsert_summary_record(self, record: dict[str, Any]) -> None:
        self.vector_store.upsert_entries([build_summary_vector_entry(record)])

    def _upsert_semantic_summary_record(self, record: dict[str, Any]) -> None:
        self.vector_store.upsert_entries([build_semantic_summary_vector_entry(record)])
