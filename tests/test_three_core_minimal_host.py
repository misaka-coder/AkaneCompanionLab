from __future__ import annotations

import ast
import unittest
from pathlib import Path

from examples.three_core_minimal_host import run_demo


ROOT = Path(__file__).resolve().parents[1]


class ThreeCoreMinimalHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run_demo()

    def test_reference_host_uses_only_public_packages(self) -> None:
        source = (ROOT / "examples" / "three_core_minimal_host.py").read_text(encoding="utf-8")
        imported_modules = {
            alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names
        }
        imported_modules.update(
            node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)
        )

        self.assertFalse(any(name.startswith("companion_v01") for name in imported_modules))
        self.assertTrue(self.report["imports_companion_v01"] is False)
        self.assertTrue(self.report["demo_embedding_only"])

    def test_inbound_tool_memory_and_outbound_loop_is_real(self) -> None:
        report = self.report

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["inbound"]["trigger_reason"], "group_mention")
        self.assertEqual(report["inbound"]["actor"]["display_name"], "伙伴")
        self.assertEqual(report["capability_id"], "python.demo.inspect_three_core")
        self.assertEqual(report["tool_call_id"], "call_three_core_1")
        self.assertTrue(report["open_turn_tool_wire_exact"])
        self.assertEqual(report["outbound_action"], "send_group_msg")
        self.assertEqual(
            report["outbound_params"]["message"][0],
            {"type": "reply", "data": {"id": "onebot-demo-1"}},
        )

    def test_memcore_settlement_is_reloadable_without_shadow_result(self) -> None:
        report = self.report

        self.assertEqual(report["settlement_status"], "settled")
        self.assertTrue(report["settled_has_compact_history"])
        self.assertIn("source_id: onebot-demo-1:tool-result", report["compact_card"])
        self.assertTrue(report["exact_tool_result_reloaded"])
        self.assertEqual(
            report["memory_tool_names"],
            ["retrieve_for_turn", "browse_memory", "open_memory", "read_timeline"],
        )

    def test_stable_prefix_is_byte_deterministic(self) -> None:
        second_report = run_demo()

        self.assertEqual(
            self.report["stable_prefix_hash"],
            self.report["stable_prefix_repeat_hash"],
        )
        self.assertEqual(
            self.report["stable_prefix_hash"],
            second_report["stable_prefix_hash"],
        )


if __name__ == "__main__":
    unittest.main()
