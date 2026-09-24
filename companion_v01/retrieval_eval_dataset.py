from __future__ import annotations

import json
import random
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .llm_runtime import LLMRuntime
from .store import MemoryStore
from .text_utils import normalize_text, render_chat_line, render_chat_timeline


@dataclass(frozen=True)
class EvalSourceRecord:
    source_id: str
    entry_type: str
    profile_user_id: str
    session_id: str
    timestamp: int
    date_label: str
    time_of_day: str
    tags: list[str]
    source_preview: str
    prompt_memory: str
    review_memory: str


def collect_eval_source_records(
    *,
    store: MemoryStore,
    profile_user_id: str | None = None,
    include_raw: bool = True,
    include_summary: bool = True,
    include_semantic: bool = True,
    raw_context_window: int = 1,
) -> list[EvalSourceRecord]:
    records: list[EvalSourceRecord] = []
    if include_raw:
        for batch in store.iter_messages_for_vector_reindex(batch_size=128):
            for record in batch:
                if profile_user_id and str(record.get("profile_user_id") or "") != profile_user_id:
                    continue
                candidate = _build_raw_eval_source(store=store, record=record, context_window=raw_context_window)
                if candidate is not None:
                    records.append(candidate)
    if include_summary:
        for batch in store.iter_summaries_for_vector_reindex(batch_size=128):
            for record in batch:
                if profile_user_id and str(record.get("profile_user_id") or "") != profile_user_id:
                    continue
                candidate = _build_summary_eval_source(record)
                if candidate is not None:
                    records.append(candidate)
    if include_semantic:
        for batch in store.iter_semantic_summaries_for_vector_reindex(batch_size=128):
            for record in batch:
                if profile_user_id and str(record.get("profile_user_id") or "") != profile_user_id:
                    continue
                candidate = _build_semantic_eval_source(record)
                if candidate is not None:
                    records.append(candidate)
    return records


def sample_eval_source_records(
    records: Sequence[EvalSourceRecord],
    *,
    total_count: int,
    seed: int = 42,
    target_counts: dict[str, int] | None = None,
    allow_repeat_for: set[str] | None = None,
) -> list[EvalSourceRecord]:
    normalized_total = max(0, int(total_count))
    if not records:
        return []

    grouped: dict[str, list[EvalSourceRecord]] = {}
    for record in records:
        grouped.setdefault(record.entry_type, []).append(record)

    rng = random.Random(int(seed))
    entry_types = sorted(grouped.keys())
    for bucket in grouped.values():
        rng.shuffle(bucket)

    if target_counts:
        quotas = {
            entry_type: max(0, int(target_counts.get(entry_type, 0)))
            for entry_type in entry_types
        }
    else:
        if normalized_total <= 0:
            return []
        quotas = _balanced_quotas(
            available_counts={entry_type: len(grouped[entry_type]) for entry_type in entry_types},
            total_count=normalized_total,
        )

    allow_repeat = set(allow_repeat_for or set())
    sampled: list[EvalSourceRecord] = []
    for entry_type in entry_types:
        quota = max(0, int(quotas.get(entry_type, 0)))
        if quota <= 0:
            continue
        bucket = grouped[entry_type]
        sampled.extend(bucket[:quota])
        if quota > len(bucket) and entry_type in allow_repeat and bucket:
            sampled.extend(rng.choice(bucket) for _ in range(quota - len(bucket)))

    rng.shuffle(sampled)
    if target_counts:
        return sampled
    return sampled[:normalized_total]


def count_eval_source_records(records: Sequence[EvalSourceRecord]) -> dict[str, int]:
    counts = Counter(record.entry_type for record in records)
    return {entry_type: int(counts.get(entry_type, 0)) for entry_type in ("raw", "summary", "semantic_summary")}


def generate_eval_dataset_rows(
    *,
    sources: Sequence[EvalSourceRecord],
    llm: LLMRuntime | None,
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_variant_counts: dict[str, int] = {}
    for source in sources:
        variant_index = int(source_variant_counts.get(source.source_id, 0)) + 1
        source_variant_counts[source.source_id] = variant_index
        generated = generate_eval_query(
            source=source,
            llm=llm,
            use_llm=use_llm,
            variant_index=variant_index,
        )
        rows.append(
            {
                "eval_id": f"eval::{uuid.uuid4()}",
                "query": generated["query"],
                "difficulty": generated["difficulty"],
                "generation_mode": generated["generation_mode"],
                "rationale": generated["rationale"],
                "variant_index": variant_index,
                "target_source_id": source.source_id,
                "entry_type": source.entry_type,
                "profile_user_id": source.profile_user_id,
                "session_id": source.session_id,
                "timestamp": int(source.timestamp),
                "date_label": source.date_label,
                "time_of_day": source.time_of_day,
                "source_tags": list(source.tags),
                "source_preview": source.source_preview,
                "review_memory": source.review_memory,
                "review_status": "pending",
                "created_at": int(time.time()),
            }
        )
    return rows


def generate_eval_query(
    *,
    source: EvalSourceRecord,
    llm: LLMRuntime | None,
    use_llm: bool = True,
    variant_index: int = 1,
) -> dict[str, str]:
    fallback = {
        "query": build_fallback_query(source, variant_index=variant_index),
        "difficulty": "medium",
        "rationale": "fallback",
    }
    if not use_llm or llm is None:
        return {
            **fallback,
            "generation_mode": "fallback",
        }

    system_prompt, user_prompt = build_generation_prompts(source, variant_index=variant_index)
    result = llm.call_aux_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback=fallback,
        temperature=0.4,
        prompt_cache_key="aux:retrieval_eval_query",
    )
    query = normalize_text(result.get("query") or fallback["query"])
    if not query:
        query = fallback["query"]
    difficulty = str(result.get("difficulty") or fallback["difficulty"]).strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = fallback["difficulty"]
    rationale = normalize_text(result.get("rationale") or fallback["rationale"]) or fallback["rationale"]
    generation_mode = "llm" if query != fallback["query"] or rationale != fallback["rationale"] else "fallback"
    return {
        "query": query,
        "difficulty": difficulty,
        "rationale": rationale,
        "generation_mode": generation_mode,
    }


def write_eval_dataset_jsonl(rows: Iterable[dict[str, Any]], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return output_path


def build_generation_prompts(source: EvalSourceRecord, *, variant_index: int = 1) -> tuple[str, str]:
    system_prompt = (
        "你是检索评测集生成器。你的任务是基于给定记忆，生成一条用户真实可能说出的中文提问。"
        "问题要自然、口语化、略带模糊或同义改写，但仍应主要指向这条记忆。"
        "不要直接照抄原句，不要出现超过4个连续字与记忆原文完全相同。"
        "优先使用第一人称口吻，长度控制在10到28个中文字符左右。"
        '只输出 JSON，对象字段固定为 query, difficulty, rationale。difficulty 只能是 easy, medium, hard。'
    )
    user_prompt = "\n".join(
        [
            f"目标记忆类型: {source.entry_type}",
            f"目标 source_id: {source.source_id}",
            f"当前问法变体序号: {max(1, int(variant_index))}",
            "以下内容只供你理解记忆，不会展示给用户：",
            source.prompt_memory,
            "",
            "请生成一条用户口吻的提问，让检索系统有机会回忆到这条记忆。",
            "如果变体序号大于1，请刻意换一种说法，避免和前一种问法只是换一个词。",
        ]
    )
    return system_prompt, user_prompt


def build_fallback_query(source: EvalSourceRecord, *, variant_index: int = 1) -> str:
    tags = [normalize_text(tag) for tag in source.tags if normalize_text(tag)]
    lead = "、".join(tags[:2]) if tags else "那件事"
    variant_templates = (
        "{lead}",
        "那次和{lead}有关的事",
        "之前围绕{lead}发生的情况",
    )
    lead_text = variant_templates[(max(1, int(variant_index)) - 1) % len(variant_templates)].format(lead=lead)
    if source.entry_type == "raw":
        return f"我之前提到{lead_text}具体是怎么回事来着？"
    if source.entry_type == "summary":
        return f"我们之前那段关于{lead_text}的经历后来总结成什么来着？"
    return f"我最近老提的{lead_text}这条主线你还记得重点吗？"


def default_output_path(base_dir: Path) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return Path(base_dir) / "documents" / "projects" / f"retrieval_eval_candidates_{timestamp}.jsonl"


def _balanced_quotas(
    *,
    available_counts: dict[str, int],
    total_count: int,
) -> dict[str, int]:
    remaining_total = max(0, int(total_count))
    quotas = {key: 0 for key in available_counts}
    active = [key for key, count in available_counts.items() if int(count) > 0]
    while remaining_total > 0 and active:
        base_take = max(1, remaining_total // max(1, len(active)))
        progressed = False
        next_active: list[str] = []
        for key in active:
            capacity = max(0, int(available_counts[key]) - int(quotas[key]))
            if capacity <= 0:
                continue
            take = min(capacity, base_take, remaining_total)
            if take <= 0 and remaining_total > 0:
                take = min(capacity, 1, remaining_total)
            if take <= 0:
                continue
            quotas[key] += take
            remaining_total -= take
            progressed = True
            if int(available_counts[key]) > int(quotas[key]):
                next_active.append(key)
            if remaining_total <= 0:
                break
        if not progressed:
            break
        active = next_active or [key for key in available_counts if int(available_counts[key]) > int(quotas[key])]
    return quotas


def _build_raw_eval_source(
    *,
    store: MemoryStore,
    record: dict[str, Any],
    context_window: int,
) -> EvalSourceRecord | None:
    content = normalize_text(record.get("content") or "")
    if len(content) < 8:
        return None
    context_rows = store.get_context_slice(
        session_id=str(record.get("session_id") or ""),
        center_seq_no=int(record.get("seq_no") or 0),
        window=max(0, int(context_window)),
        profile_user_id=str(record.get("profile_user_id") or ""),
        character_pack_id=str(record.get("character_pack_id") or ""),
    )
    focus_line = render_chat_line(
        role=str(record.get("role") or "user"),
        content=content,
        timestamp=record.get("timestamp"),
    )
    context_text = render_chat_timeline(context_rows) if context_rows else focus_line
    prompt_memory = "\n".join(
        [
            "【聚焦消息】",
            focus_line,
            "",
            "【相邻上下文】",
            context_text,
        ]
    )
    return EvalSourceRecord(
        source_id=str(record["source_id"]),
        entry_type="raw",
        profile_user_id=str(record["profile_user_id"]),
        session_id=str(record["session_id"]),
        timestamp=int(record["timestamp"]),
        date_label=str(record["date_label"]),
        time_of_day=str(record["time_of_day"]),
        tags=list(record.get("semantic_tags") or []),
        source_preview=content[:160],
        prompt_memory=prompt_memory,
        review_memory=prompt_memory,
    )


def _build_summary_eval_source(record: dict[str, Any]) -> EvalSourceRecord | None:
    diary = normalize_text(record.get("diary_summary") or "")
    if len(diary) < 8:
        return None
    key_events = [normalize_text(item) for item in list(record.get("key_events") or []) if normalize_text(item)]
    core_facts = [normalize_text(item) for item in list(record.get("core_facts") or []) if normalize_text(item)]
    prompt_memory = "\n".join(
        [
            "【摘要】",
            diary,
            f"【阶段】{record.get('period_label') or '未标注'} / {record.get('event_type') or '未标注'}",
            f"【关键事件】{'；'.join(key_events) if key_events else '无'}",
            f"【核心事实】{'；'.join(core_facts) if core_facts else '无'}",
        ]
    )
    return EvalSourceRecord(
        source_id=str(record["summary_id"]),
        entry_type="summary",
        profile_user_id=str(record["profile_user_id"]),
        session_id=str(record["session_id"]),
        timestamp=int(record["timestamp"]),
        date_label=str(record["date_label"]),
        time_of_day=str(record["time_of_day"]),
        tags=list(record.get("semantic_tags") or []),
        source_preview=diary[:160],
        prompt_memory=prompt_memory,
        review_memory=prompt_memory,
    )


def _build_semantic_eval_source(record: dict[str, Any]) -> EvalSourceRecord | None:
    summary = normalize_text(record.get("semantic_summary") or "")
    if len(summary) < 8:
        return None
    stable_facts = [normalize_text(item) for item in list(record.get("stable_facts") or []) if normalize_text(item)]
    recurring_topics = [normalize_text(item) for item in list(record.get("recurring_topics") or []) if normalize_text(item)]
    important_people = [normalize_text(item) for item in list(record.get("important_people") or []) if normalize_text(item)]
    open_loops = [normalize_text(item) for item in list(record.get("open_loops") or []) if normalize_text(item)]
    prompt_memory = "\n".join(
        [
            "【长期语义记忆】",
            summary,
            f"【稳定事实】{'；'.join(stable_facts) if stable_facts else '无'}",
            f"【反复主题】{'；'.join(recurring_topics) if recurring_topics else '无'}",
            f"【重要人物】{'；'.join(important_people) if important_people else '无'}",
            f"【未完成线索】{'；'.join(open_loops) if open_loops else '无'}",
        ]
    )
    return EvalSourceRecord(
        source_id=str(record["semantic_id"]),
        entry_type="semantic_summary",
        profile_user_id=str(record["profile_user_id"]),
        session_id=str(record["session_id"]),
        timestamp=int(record["timestamp"]),
        date_label=str(record["date_label"]),
        time_of_day=str(record["time_of_day"]),
        tags=list(record.get("semantic_tags") or []),
        source_preview=summary[:160],
        prompt_memory=prompt_memory,
        review_memory=prompt_memory,
    )
