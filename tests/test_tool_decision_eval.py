from __future__ import annotations

import unittest

from companion_v01.tool_decision_eval import (
    DEFAULT_MEMORY_EVAL_CASES,
    DEFAULT_READ_TIER_EVAL_CASES,
    DEFAULT_WEB_SEARCH_EVAL_CASES,
    LiveLLMToolDecisionResponseProvider,
    LiveLLMWebSearchResponseProvider,
    ToolDecisionEvalCase,
    ToolDecisionModelResponse,
    build_dry_run_eval_engine,
    build_dry_run_memory_eval_engine,
    build_dry_run_web_search_eval_engine,
    run_tool_decision_eval,
    scripted_tool_decision_response_provider,
    scripted_web_search_response_provider,
    summarize_tool_decision_eval_results,
    tool_decision_results_as_dicts,
)
from companion_v01.tool_invocation import NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_SOURCE_FIELD


class ToolDecisionEvalTests(unittest.TestCase):
    def test_default_web_search_suite_runs_legacy_and_native(self) -> None:
        engine = build_dry_run_web_search_eval_engine()

        results = run_tool_decision_eval(
            cases=DEFAULT_WEB_SEARCH_EVAL_CASES,
            engine=engine,
            response_provider=scripted_web_search_response_provider,
            execute_tools=True,
        )
        summary = summarize_tool_decision_eval_results(results)

        self.assertEqual(len(results), len(DEFAULT_WEB_SEARCH_EVAL_CASES) * 2)
        self.assertEqual(summary["modes"]["legacy"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["native_source_call_count"], 4)
        self.assertEqual(summary["modes"]["legacy"]["legacy_source_call_count"], 4)
        self.assertEqual(summary["modes"]["native"]["execution_success_rate"], 1.0)
        self.assertEqual(summary["comparison"]["native_vs_legacy_fallback_hit_delta"], 0)

    def test_memory_suite_runs_native_and_legacy_through_real_handlers(self) -> None:
        engine = build_dry_run_memory_eval_engine()

        results = run_tool_decision_eval(
            cases=DEFAULT_MEMORY_EVAL_CASES,
            engine=engine,
            response_provider=scripted_tool_decision_response_provider,
            execute_tools=True,
        )
        summary = summarize_tool_decision_eval_results(results)

        self.assertEqual(len(results), len(DEFAULT_MEMORY_EVAL_CASES) * 2)
        # Both memory tools normalize/validate/execute cleanly in both channels.
        self.assertEqual(summary["modes"]["legacy"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["execution_success_rate"], 1.0)
        self.assertEqual(summary["modes"]["legacy"]["validation_success_rate"], 1.0)
        self.assertEqual(summary["comparison"]["native_vs_legacy_fallback_hit_delta"], 0)
        # Three expected-tool cases (retrieve x2, timeline x1) carry the source tag.
        self.assertEqual(summary["modes"]["native"]["native_source_call_count"], 3)
        self.assertEqual(summary["modes"]["legacy"]["legacy_source_call_count"], 3)
        # No web_search calls leaked into the memory suite.
        self.assertEqual(summary["modes"]["native"]["web_search_call_count"], 0)

        native_names = {
            result.tool_name
            for result in results
            if result.mode == "native" and result.called_tool
        }
        self.assertEqual(native_names, {"retrieve_memory", "read_memory_timeline"})

    def test_read_tier_suite_runs_native_and_legacy_through_real_handlers(self) -> None:
        # N3-prep: list_reminders / check_inventory / inspect_media_info were added
        # to the default native allowlist (6b) without eval coverage. The combined
        # dry-run engine must route/normalize/validate/execute them in both channels.
        engine = build_dry_run_eval_engine()

        results = run_tool_decision_eval(
            cases=DEFAULT_READ_TIER_EVAL_CASES,
            engine=engine,
            response_provider=scripted_tool_decision_response_provider,
            execute_tools=True,
        )
        summary = summarize_tool_decision_eval_results(results)

        self.assertEqual(len(results), len(DEFAULT_READ_TIER_EVAL_CASES) * 2)
        self.assertEqual(summary["modes"]["legacy"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["expectation_match_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["execution_success_rate"], 1.0)
        self.assertEqual(summary["modes"]["native"]["validation_success_rate"], 1.0)
        self.assertEqual(summary["comparison"]["native_vs_legacy_fallback_hit_delta"], 0)
        # Three positive cases (one per tool) carry the native source tag.
        self.assertEqual(summary["modes"]["native"]["native_source_call_count"], 3)

        native_names = {
            result.tool_name
            for result in results
            if result.mode == "native" and result.called_tool
        }
        self.assertEqual(
            native_names, {"list_reminders", "check_inventory", "inspect_media_info"}
        )

    def test_bad_tool_arguments_are_counted_as_validation_failure(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [
            ToolDecisionEvalCase(
                eval_id="bad_web_search",
                user_prompt="查一下今天新闻。",
                expect_tool=True,
                expected_action="search",
            )
        ]

        def provider(_case: ToolDecisionEvalCase, mode: str) -> ToolDecisionModelResponse:
            tool_call = {"type": "web_search", "action": "search"}
            if mode == "native":
                tool_call[TOOL_SOURCE_FIELD] = NATIVE_OPENAI
            return ToolDecisionModelResponse(final_output={"tool_call": tool_call})

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            execute_tools=True,
        )
        summary = summarize_tool_decision_eval_results(results)

        self.assertFalse(results[0].normalized_ok)
        self.assertFalse(results[0].validation_ok)
        self.assertEqual(results[0].error_code, "bad_args")
        self.assertEqual(summary["modes"]["legacy"]["validation_errors"], {"bad_args": 1})
        self.assertEqual(summary["modes"]["native"]["validation_errors"], {"bad_args": 1})
        self.assertEqual(summary["modes"]["legacy"]["expectation_match_rate"], 0.0)

    def test_fallback_hits_are_reported_separately_from_tool_mismatch(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [
            ToolDecisionEvalCase(
                eval_id="fallback_no_tool",
                user_prompt="查一下最新模型价格。",
                expect_tool=True,
                expected_action="search",
            )
        ]

        def provider(_case: ToolDecisionEvalCase, _mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={"speech": "兜底回复", "tool_call": None},
                fallback_hit=True,
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            modes=("legacy",),
        )
        summary = summarize_tool_decision_eval_results(results)

        self.assertFalse(results[0].called_tool)
        self.assertFalse(results[0].expectation_met)
        self.assertEqual(summary["modes"]["legacy"]["fallback_hit_count"], 1)
        self.assertEqual(summary["modes"]["legacy"]["metric_totals"], {})
        self.assertEqual(summary["modes"]["legacy"]["mismatch_ids"], ["fallback_no_tool"])

    def test_metric_delta_is_preserved_in_details_and_summary(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [
            ToolDecisionEvalCase(
                eval_id="fallback_error",
                user_prompt="查一下最新消息。",
                expect_tool=True,
            )
        ]

        def provider(_case: ToolDecisionEvalCase, _mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={"speech": "", "tool_call": None},
                fallback_hit=True,
                metrics={"errors": 1, "chat_json_fallbacks": 1},
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            modes=("legacy",),
        )
        summary = summarize_tool_decision_eval_results(results)
        rows = tool_decision_results_as_dicts(results)

        self.assertEqual(rows[0]["metric_delta"], {"errors": 1, "chat_json_fallbacks": 1})
        self.assertEqual(summary["modes"]["legacy"]["llm_error_count"], 1)
        self.assertEqual(summary["modes"]["legacy"]["chat_json_fallback_metric_count"], 1)

    def test_llm_error_detail_is_preserved_in_details_and_summary(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [
            ToolDecisionEvalCase(
                eval_id="fallback_error_detail",
                user_prompt="查一下最新消息。",
                expect_tool=True,
            )
        ]

        def provider(_case: ToolDecisionEvalCase, _mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={"speech": "", "tool_call": None},
                fallback_hit=True,
                metrics={"errors": 1, "chat_json_fallbacks": 1},
                error_detail={"phase": "call_json", "type": "RuntimeError", "message": "boom"},
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            modes=("legacy",),
        )
        summary = summarize_tool_decision_eval_results(results)
        rows = tool_decision_results_as_dicts(results)

        self.assertEqual(rows[0]["error_detail"]["type"], "RuntimeError")
        self.assertEqual(summary["modes"]["legacy"]["llm_error_types"], {"RuntimeError": 1})

    def test_results_can_be_serialized_as_plain_dicts(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        results = run_tool_decision_eval(
            cases=DEFAULT_WEB_SEARCH_EVAL_CASES[:1],
            engine=engine,
            response_provider=scripted_web_search_response_provider,
            modes=("native",),
        )

        rows = tool_decision_results_as_dicts(results)

        self.assertEqual(rows[0]["mode"], "native")
        self.assertEqual(rows[0]["tool_source"], NATIVE_OPENAI)
        self.assertEqual(rows[0]["normalized_tool_call"]["type"], "web_search")

    def test_native_internal_carrier_is_evaluated_as_tool_call(self) -> None:
        engine = build_dry_run_memory_eval_engine()
        cases = [DEFAULT_MEMORY_EVAL_CASES[0]]

        def provider(_case: ToolDecisionEvalCase, _mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={
                    NATIVE_TOOL_CALL_FIELD: {
                        "type": "retrieve_memory",
                        "query": "我最喜欢的咖啡",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    },
                    "tool_call": None,
                },
                native_sent=True,
                native_extracted=True,
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            modes=("native",),
            execute_tools=True,
        )

        self.assertTrue(results[0].called_tool)
        self.assertEqual(results[0].tool_name, "retrieve_memory")
        self.assertEqual(results[0].tool_source, NATIVE_OPENAI)
        self.assertTrue(results[0].validation_ok)
        self.assertEqual(results[0].execution_status, "ok")

    def test_live_provider_uses_native_schema_and_metric_diff(self) -> None:
        runtime = FakeRuntime(
            result={
                "tool_call": {
                    "type": "web_search",
                    "action": "search",
                    "query": "OpenAI API latest changes",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                }
            },
            metric_delta={"native_tool_decision_sent": 1, "native_tool_call_extracted": 1},
        )
        provider = LiveLLMWebSearchResponseProvider(runtime=runtime)

        response = provider(DEFAULT_WEB_SEARCH_EVAL_CASES[0], "native")

        self.assertTrue(response.native_sent)
        self.assertTrue(response.native_extracted)
        self.assertFalse(response.fallback_hit)
        self.assertEqual(runtime.calls[0]["native_tools"][0]["function"]["name"], "web_search")
        self.assertNotIn("搜索格式为", runtime.calls[0]["system_prompt"])

    def test_live_provider_can_send_memory_native_schemas(self) -> None:
        runtime = FakeRuntime(
            result={
                NATIVE_TOOL_CALL_FIELD: {
                    "type": "retrieve_memory",
                    "query": "我最喜欢的咖啡",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                },
                "tool_call": None,
            },
            metric_delta={"native_tool_decision_sent": 1, "native_tool_call_extracted": 1},
        )
        provider = LiveLLMToolDecisionResponseProvider(runtime=runtime, toolset="memory")

        response = provider(DEFAULT_MEMORY_EVAL_CASES[0], "native")

        self.assertTrue(response.native_sent)
        self.assertTrue(response.native_extracted)
        self.assertEqual(
            [item["function"]["name"] for item in runtime.calls[0]["native_tools"]],
            ["retrieve_memory", "read_memory_timeline"],
        )
        self.assertIn("retrieve_memory", runtime.calls[0]["system_prompt"])
        self.assertIn("provider native tool_calls", runtime.calls[0]["system_prompt"])
        self.assertNotIn("web_search 只用于", runtime.calls[0]["system_prompt"])
        self.assertNotIn("格式为", runtime.calls[0]["system_prompt"])
        self.assertEqual(response.final_output[NATIVE_TOOL_CALL_FIELD]["type"], "retrieve_memory")

    def test_live_provider_captures_last_error_detail_when_runtime_errors(self) -> None:
        runtime = FakeRuntime(
            result={"speech": "", "tool_call": None},
            metric_delta={"errors": 1, "chat_json_fallbacks": 1},
            error_detail={"phase": "call_json", "type": "BadRequestError", "message": "bad payload"},
        )
        provider = LiveLLMWebSearchResponseProvider(runtime=runtime)

        response = provider(DEFAULT_WEB_SEARCH_EVAL_CASES[0], "legacy")

        self.assertTrue(response.fallback_hit)
        self.assertEqual(response.error_detail["type"], "BadRequestError")


    def test_live_provider_legacy_prompt_keeps_legacy_tool_instruction(self) -> None:
        runtime = FakeRuntime(result={"speech": "", "tool_call": None})
        provider = LiveLLMWebSearchResponseProvider(runtime=runtime)

        provider(DEFAULT_WEB_SEARCH_EVAL_CASES[0], "legacy")

        self.assertIsNone(runtime.calls[0]["native_tools"])
        self.assertIn("搜索格式为", runtime.calls[0]["system_prompt"])

    def test_live_provider_memory_legacy_prompt_keeps_memory_instructions(self) -> None:
        runtime = FakeRuntime(result={"speech": "", "tool_call": None})
        provider = LiveLLMToolDecisionResponseProvider(runtime=runtime, toolset="memory")

        provider(DEFAULT_MEMORY_EVAL_CASES[0], "legacy")

        self.assertIsNone(runtime.calls[0]["native_tools"])
        self.assertIn("retrieve_memory", runtime.calls[0]["system_prompt"])
        self.assertIn("read_memory_timeline", runtime.calls[0]["system_prompt"])
        self.assertIn("格式为", runtime.calls[0]["system_prompt"])

    def test_native_degraded_case_is_excluded_from_comparison(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [DEFAULT_WEB_SEARCH_EVAL_CASES[0]]

        def provider(_case: ToolDecisionEvalCase, mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={
                    "tool_call": {
                        "type": "web_search",
                        "action": "search",
                        "query": "OpenAI API latest changes",
                    }
                },
                native_sent=(mode == "native"),
                native_extracted=False,
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
        )
        summary = summarize_tool_decision_eval_results(results)
        native_result = [result for result in results if result.mode == "native"][0]

        self.assertTrue(native_result.native_degraded)
        self.assertFalse(native_result.comparison_eligible)
        self.assertEqual(summary["modes"]["native"]["comparison_excluded_ids"], [cases[0].eval_id])
        self.assertEqual(summary["comparison"]["native_comparison_excluded_ids"], [cases[0].eval_id])

    def test_native_handwritten_tool_shape_is_degraded_even_when_not_normalized(self) -> None:
        engine = build_dry_run_web_search_eval_engine()
        cases = [DEFAULT_WEB_SEARCH_EVAL_CASES[1]]

        def provider(_case: ToolDecisionEvalCase, _mode: str) -> ToolDecisionModelResponse:
            return ToolDecisionModelResponse(
                final_output={
                    "speech": "",
                    "tool_call": {
                        "name": "web_search",
                        "arguments": {"action": "extract", "url": "https://example.com/news"},
                    },
                },
                native_sent=True,
                native_extracted=False,
            )

        results = run_tool_decision_eval(
            cases=cases,
            engine=engine,
            response_provider=provider,
            modes=("native",),
        )
        result = results[0]

        self.assertFalse(result.called_tool)
        self.assertTrue(result.native_degraded)
        self.assertFalse(result.comparison_eligible)


class FakeRuntime:
    def __init__(
        self,
        *,
        result: dict,
        metric_delta: dict[str, int] | None = None,
        error_detail: dict[str, str] | None = None,
    ) -> None:
        self.result = result
        self.metric_delta = dict(metric_delta or {})
        self.error_detail = dict(error_detail or {})
        self.calls: list[dict] = []
        self._metrics: dict[str, int] = {}
        self._snapshot_count = 0

    def snapshot_metrics(self) -> dict[str, int]:
        self._snapshot_count += 1
        if self._snapshot_count >= 2:
            return dict(self.metric_delta)
        return dict(self._metrics)

    def call_chat_json(self, **kwargs):
        self.calls.append(dict(kwargs))
        return dict(self.result)

    def snapshot_last_error(self) -> dict[str, str]:
        return dict(self.error_detail)


if __name__ == "__main__":
    unittest.main()
