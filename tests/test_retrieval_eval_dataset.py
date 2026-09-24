from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.retrieval_eval_dataset import (
    build_fallback_query,
    count_eval_source_records,
    collect_eval_source_records,
    generate_eval_dataset_rows,
    sample_eval_source_records,
)
from companion_v01.store import MemoryStore


class StubLLMRuntime:
    def call_aux_json(self, **kwargs):
        return {
            "query": "我之前提到的那件事后来到底怎么了？",
            "difficulty": "medium",
            "rationale": "mocked",
        }


class RetrievalEvalDatasetTests(unittest.TestCase):
    def test_sample_eval_source_records_balances_entry_types(self) -> None:
        records = [
            _make_source(f"raw-{idx}", "raw") for idx in range(4)
        ] + [
            _make_source(f"summary-{idx}", "summary") for idx in range(4)
        ] + [
            _make_source(f"semantic-{idx}", "semantic_summary") for idx in range(4)
        ]

        sampled = sample_eval_source_records(records, total_count=6, seed=123)

        self.assertEqual(len(sampled), 6)
        counts = {entry_type: 0 for entry_type in ("raw", "summary", "semantic_summary")}
        for item in sampled:
            counts[item.entry_type] += 1
        self.assertEqual(counts, {"raw": 2, "summary": 2, "semantic_summary": 2})

    def test_collect_eval_source_records_mixes_raw_summary_and_semantic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_message(
                profile_user_id="user-1",
                session_id="session-1",
                role="user",
                content="我昨天吃糖葫芦把牙崩了一下。",
                timestamp=100,
                semantic_tags=["糖葫芦", "牙"],
            )
            summary = store.add_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=200,
                date_label="2026-04-10",
                time_of_day="night",
                period_label="放学后",
                event_type="日常",
                importance=0.7,
                diary_summary="主人放学后提到吃糖葫芦把牙磕到了。",
                key_events=["吃糖葫芦"],
                core_facts=["牙崩了一下"],
                semantic_tags=["糖葫芦", "牙"],
                source_start_seq=1,
                source_end_seq=1,
                source_ids=["msg-1"],
            )
            store.add_semantic_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=300,
                period_start_ts=100,
                period_end_ts=300,
                date_label="2026-04-10",
                time_of_day="night",
                importance=0.8,
                semantic_summary="主人最近常提甜食和牙齿的小意外。",
                stable_facts=["最近爱吃甜食"],
                recurring_topics=["糖葫芦", "牙齿"],
                important_people=[],
                open_loops=["要不要去看牙"],
                semantic_tags=["甜食", "牙齿"],
                source_summary_ids=[summary["summary_id"]],
            )

            records = collect_eval_source_records(store=store)

        entry_types = {record.entry_type for record in records}
        self.assertEqual(entry_types, {"raw", "summary", "semantic_summary"})

    def test_count_eval_source_records_reports_inventory_by_layer(self) -> None:
        records = [
            _make_source("raw-1", "raw"),
            _make_source("summary-1", "summary"),
            _make_source("semantic-1", "semantic_summary"),
            _make_source("raw-2", "raw"),
        ]

        counts = count_eval_source_records(records)

        self.assertEqual(
            counts,
            {
                "raw": 2,
                "summary": 1,
                "semantic_summary": 1,
            },
        )

    def test_generate_eval_dataset_rows_marks_pending_review(self) -> None:
        rows = generate_eval_dataset_rows(
            sources=[_make_source("semantic-1", "semantic_summary")],
            llm=StubLLMRuntime(),
            use_llm=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_source_id"], "semantic-1")
        self.assertEqual(rows[0]["review_status"], "pending")
        self.assertEqual(rows[0]["generation_mode"], "llm")

    def test_generate_eval_dataset_rows_tracks_variant_index_for_duplicate_sources(self) -> None:
        rows = generate_eval_dataset_rows(
            sources=[
                _make_source("semantic-1", "semantic_summary"),
                _make_source("semantic-1", "semantic_summary"),
            ],
            llm=StubLLMRuntime(),
            use_llm=True,
        )

        self.assertEqual([row["variant_index"] for row in rows], [1, 2])

    def test_build_fallback_query_varies_by_entry_type(self) -> None:
        raw_query = build_fallback_query(_make_source("raw-1", "raw", tags=["糖葫芦", "牙"]))
        summary_query = build_fallback_query(_make_source("summary-1", "summary", tags=["课程"]))
        semantic_query = build_fallback_query(_make_source("semantic-1", "semantic_summary", tags=["复习"]))

        self.assertIn("糖葫芦", raw_query)
        self.assertIn("总结", summary_query)
        self.assertIn("主线", semantic_query)

    def test_sample_eval_source_records_supports_target_counts_with_repeat_for_scarce_layers(self) -> None:
        records = [
            _make_source("raw-1", "raw"),
            _make_source("raw-2", "raw"),
            _make_source("summary-1", "summary"),
            _make_source("semantic-1", "semantic_summary"),
        ]

        sampled = sample_eval_source_records(
            records,
            total_count=6,
            seed=123,
            target_counts={
                "raw": 2,
                "summary": 2,
                "semantic_summary": 2,
            },
            allow_repeat_for={"summary", "semantic_summary"},
        )

        counts = {"raw": 0, "summary": 0, "semantic_summary": 0}
        source_ids = []
        for item in sampled:
            counts[item.entry_type] += 1
            source_ids.append(item.source_id)
        self.assertEqual(counts, {"raw": 2, "summary": 2, "semantic_summary": 2})
        self.assertGreater(source_ids.count("summary-1"), 1)
        self.assertGreater(source_ids.count("semantic-1"), 1)


def _make_source(source_id: str, entry_type: str, tags: list[str] | None = None):
    from companion_v01.retrieval_eval_dataset import EvalSourceRecord

    return EvalSourceRecord(
        source_id=source_id,
        entry_type=entry_type,
        profile_user_id="user-1",
        session_id="session-1",
        timestamp=1,
        date_label="2026-04-10",
        time_of_day="morning",
        tags=list(tags or ["课程", "复习"]),
        source_preview="preview",
        prompt_memory="prompt memory",
        review_memory="review memory",
    )


if __name__ == "__main__":
    unittest.main()
