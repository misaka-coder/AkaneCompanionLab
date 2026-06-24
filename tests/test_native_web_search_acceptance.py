from __future__ import annotations

import unittest

from scripts.tools.run_native_web_search_acceptance import gate_eval_summary, gate_smoke_summary


class NativeWebSearchAcceptanceGateTests(unittest.TestCase):
    def test_eval_gate_accepts_native_equal_or_better_than_legacy(self) -> None:
        gate = gate_eval_summary(
            {
                "modes": {
                    "legacy": {
                        "eligible_expectation_match_rate": 0.8,
                        "fallback_hit_count": 0,
                    },
                    "native": {
                        "provider_unsupported_count": 0,
                        "native_degraded_count": 0,
                        "eligible_expectation_match_rate": 1.0,
                        "fallback_hit_count": 0,
                        "validation_success_rate": 1.0,
                    },
                }
            }
        )

        self.assertEqual(gate["status"], "pass")
        self.assertEqual(gate["failures"], [])

    def test_eval_gate_rejects_degraded_native(self) -> None:
        gate = gate_eval_summary(
            {
                "modes": {
                    "legacy": {
                        "eligible_expectation_match_rate": 1.0,
                        "fallback_hit_count": 0,
                    },
                    "native": {
                        "provider_unsupported_count": 0,
                        "native_degraded_count": 1,
                        "eligible_expectation_match_rate": 1.0,
                        "fallback_hit_count": 0,
                        "validation_success_rate": 1.0,
                    },
                }
            }
        )

        self.assertEqual(gate["status"], "fail")
        self.assertIn("native_degraded", gate["failures"])

    def test_stream_smoke_gate_requires_working_event_and_successful_tool(self) -> None:
        gate = gate_smoke_summary(
            {
                "native_tool_call_extracted_delta": 1,
                "tool_event_count": 1,
                "assistant_working_count": 1,
                "chat_json_fallbacks_delta": 0,
                "native_tool_provider_unsupported_delta": 0,
                "unavailable_tool_event_count": 0,
                "speech": "查到了。",
            },
            stream=True,
        )

        self.assertEqual(gate["status"], "pass")

    def test_smoke_gate_rejects_unavailable_tool(self) -> None:
        gate = gate_smoke_summary(
            {
                "native_tool_call_extracted_delta": 1,
                "tool_event_count": 1,
                "assistant_working_count": 1,
                "chat_json_fallbacks_delta": 0,
                "native_tool_provider_unsupported_delta": 0,
                "unavailable_tool_event_count": 1,
                "speech": "工具不可用。",
            },
            stream=True,
        )

        self.assertEqual(gate["status"], "fail")
        self.assertIn("tool_unavailable", gate["failures"])


if __name__ == "__main__":
    unittest.main()
