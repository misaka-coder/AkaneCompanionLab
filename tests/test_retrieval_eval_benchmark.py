from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from companion_v01.retrieval_eval_benchmark import (
    RetrievalBenchmarkCaseResult,
    _expand_context_source_ids,
    build_benchmark_case_result,
    default_benchmark_output_paths,
    load_eval_dataset_rows,
    summarize_benchmark_results,
)
from companion_v01.retrieval_types import RetrievalPipelineResult


class RetrievalEvalBenchmarkTests(unittest.TestCase):
    def test_load_eval_dataset_rows_filters_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "eval.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "eval_id": "eval-1",
                                "query": "第一条",
                                "target_source_id": "memory-1",
                                "review_status": "pending",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "eval_id": "eval-2",
                                "query": "第二条",
                                "target_source_id": "memory-2",
                                "review_status": "approved",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            rows = load_eval_dataset_rows(dataset_path, review_statuses={"approved"})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["eval_id"], "eval-2")

    def test_build_benchmark_case_result_tracks_first_and_final_hits(self) -> None:
        row = {
            "eval_id": "eval-1",
            "query": "我之前那件事后来怎么样了？",
            "target_source_id": "memory-target",
            "entry_type": "summary",
            "profile_user_id": "user-1",
            "review_status": "pending",
        }
        pipeline_result = RetrievalPipelineResult(
            used_retrieval=True,
            confirmed_snippets=["snippet"],
            router_output={"route": "memory_search"},
            router_timing={"ready_at_ms": 12.5},
            retrieval_result={
                "filtered_candidate_count": 4,
                "fused_hits": [
                    {"source_id": "memory-a"},
                    {"source_id": "memory-target"},
                    {"source_id": "memory-b"},
                ],
            },
            verifier_output={"match_result": "match"},
            verifier_timing={
                "selected_attempt": 2,
                "attempts": [
                    {
                        "retrieved_source_ids": ["memory-target", "memory-a"],
                        "verifier_timing": {"ready_at_ms": 21.0},
                    },
                    {
                        "retrieved_source_ids": ["memory-a", "memory-target", "memory-b"],
                        "verifier_timing": {"ready_at_ms": 33.0},
                    },
                ],
            },
        )

        result = build_benchmark_case_result(
            row=row,
            pipeline_result=pipeline_result,
            elapsed_ms=88.88,
            benchmark_force_retrieval=True,
            original_router_output={"need_retrieval": False, "route": "direct_answer"},
            final_context_source_ids=["memory-target", "memory-a", "memory-b"],
            attempt_context_source_ids=[
                ["memory-target", "memory-a"],
                ["memory-a", "memory-target", "memory-b"],
            ],
        )

        self.assertTrue(result.benchmark_force_retrieval)
        self.assertFalse(result.original_router_need_retrieval)
        self.assertEqual(result.original_router_route, "direct_answer")
        self.assertEqual(result.target_rank, 2)
        self.assertEqual(result.first_target_rank, 1)
        self.assertEqual(result.best_target_rank, 1)
        self.assertEqual(result.context_target_rank, 1)
        self.assertEqual(result.first_context_target_rank, 1)
        self.assertEqual(result.best_context_target_rank, 1)
        self.assertTrue(result.top4_hit)
        self.assertTrue(result.first_top4_hit)
        self.assertTrue(result.ever_top4_hit)
        self.assertTrue(result.context_top4_hit)
        self.assertTrue(result.first_context_top4_hit)
        self.assertTrue(result.ever_context_top4_hit)
        self.assertEqual(result.verifier_ready_at_ms, 33.0)

    def test_summarize_benchmark_results_reports_overall_and_layer_metrics(self) -> None:
        results = [
            RetrievalBenchmarkCaseResult(
                eval_id="eval-1",
                query="q1",
                target_source_id="memory-1",
                entry_type="raw",
                profile_user_id="user-1",
                review_status="approved",
                benchmark_force_retrieval=True,
                original_router_need_retrieval=False,
                original_router_route="direct_answer",
                used_retrieval=True,
                router_route="memory_search",
                router_ready_at_ms=10.0,
                match_result="match",
                retry_triggered=False,
                verifier_ready_at_ms=20.0,
                filtered_candidate_count=4,
                elapsed_ms=100.0,
                target_rank=1,
                first_target_rank=1,
                best_target_rank=1,
                context_target_rank=1,
                first_context_target_rank=1,
                best_context_target_rank=1,
                top1_hit=True,
                top4_hit=True,
                first_top4_hit=True,
                ever_top4_hit=True,
                context_top1_hit=True,
                context_top4_hit=True,
                first_context_top4_hit=True,
                ever_context_top4_hit=True,
                final_source_ids=["memory-1"],
                attempt_source_ids=[["memory-1"]],
                final_context_source_ids=["memory-1"],
                attempt_context_source_ids=[["memory-1"]],
            ),
            RetrievalBenchmarkCaseResult(
                eval_id="eval-2",
                query="q2",
                target_source_id="memory-2",
                entry_type="summary",
                profile_user_id="user-1",
                review_status="pending",
                benchmark_force_retrieval=True,
                original_router_need_retrieval=True,
                original_router_route="memory_search",
                used_retrieval=False,
                router_route="direct_answer",
                router_ready_at_ms=5.0,
                match_result="skip",
                retry_triggered=False,
                verifier_ready_at_ms=None,
                filtered_candidate_count=0,
                elapsed_ms=50.0,
                target_rank=None,
                first_target_rank=None,
                best_target_rank=None,
                context_target_rank=1,
                first_context_target_rank=1,
                best_context_target_rank=1,
                top1_hit=False,
                top4_hit=False,
                first_top4_hit=False,
                ever_top4_hit=False,
                context_top1_hit=True,
                context_top4_hit=True,
                first_context_top4_hit=True,
                ever_context_top4_hit=True,
                final_source_ids=[],
                attempt_source_ids=[],
                final_context_source_ids=["memory-2"],
                attempt_context_source_ids=[["memory-2"]],
            ),
        ]

        class StubProvider:
            name = "hashed"
            version = "v1"
            dimension = 128

        class StubVectorStore:
            collection_name = "akane_memory_v01"

            def count_entries(self) -> int:
                return 20

        class StubStore:
            def count_vectorizable_records(self) -> int:
                return 20

        class StubRuntime:
            embedding_provider = StubProvider()
            provider_requested = "hashed"
            provider_fallback_reason = ""
            vector_store = StubVectorStore()
            store = StubStore()

        summary = summarize_benchmark_results(
            results,
            dataset_path=Path("dataset.jsonl"),
            runtime=StubRuntime(),
            force_retrieval=True,
        )

        self.assertEqual(summary["benchmark_mode"], "force_retrieval")
        self.assertEqual(summary["overall"]["count"], 2)
        self.assertEqual(summary["overall"]["benchmark_force_retrieval_count"], 2)
        self.assertEqual(summary["overall"]["original_router_positive_count"], 1)
        self.assertEqual(summary["overall"]["top1_hits"], 1)
        self.assertEqual(summary["overall"]["top4_hits"], 1)
        self.assertEqual(summary["overall"]["context_top1_hits"], 2)
        self.assertEqual(summary["overall"]["context_top4_hits"], 2)
        self.assertEqual(summary["overall"]["used_retrieval_count"], 1)
        self.assertEqual(summary["by_entry_type"]["raw"]["top1_hits"], 1)
        self.assertEqual(summary["by_entry_type"]["summary"]["count"], 1)

    def test_default_benchmark_output_paths_replaces_candidates_stem(self) -> None:
        summary_path, details_path = default_benchmark_output_paths(
            Path("documents/projects/retrieval_eval_candidates_20260410_212043.jsonl")
        )

        self.assertEqual(summary_path.name, "retrieval_eval_benchmark_20260410_212043.json")
        self.assertEqual(details_path.name, "retrieval_eval_benchmark_20260410_212043.details.jsonl")

    def test_expand_context_source_ids_includes_raw_window_rows(self) -> None:
        class StubStore:
            def get_record_by_source_id(self, source_id: str):
                if source_id == "raw-2":
                    return {
                        "source_id": "raw-2",
                        "entry_type": "raw",
                        "profile_user_id": "user-1",
                        "session_id": "session-1",
                        "character_pack_id": "akane",
                        "seq_no": 2,
                        "content": "你还记得吗？",
                    }
                return None

            def get_context_slice(
                self,
                session_id: str,
                center_seq_no: int,
                window: int = 1,
                *,
                profile_user_id: str = "",
                character_pack_id: str | None = None,
            ):
                self.last_window = window
                self.last_scope = (profile_user_id, character_pack_id)
                return [
                    {"source_id": "raw-1"},
                    {"source_id": "raw-2"},
                    {"source_id": "raw-3"},
                ]

        class StubRetrievalService:
            def __init__(self):
                self.store = StubStore()

            def _is_question_like(self, text: str) -> bool:
                return True

        retrieval_service = StubRetrievalService()
        expanded = _expand_context_source_ids(
            source_ids=["raw-2"],
            retrieval_service=retrieval_service,
        )

        self.assertEqual(expanded, ["raw-1", "raw-2", "raw-3"])
        self.assertEqual(retrieval_service.store.last_scope, ("user-1", "akane"))


if __name__ == "__main__":
    unittest.main()
