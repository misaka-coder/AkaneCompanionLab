from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_capability_release import load_required_capabilities, validate_capability_catalog


class CapabilityReleaseGateTests(unittest.TestCase):
    def test_requirements_are_bot_scoped_and_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "requirements.json"
            path.write_text(
                '{"schema_version":1,"bots":{"personal":{"required":["tool.send_file","tool.send_file"]}}}',
                encoding="utf-8",
            )

            self.assertEqual(load_required_capabilities(path, bot_id="personal"), ["tool.send_file"])
            with self.assertRaisesRegex(ValueError, "bot_requirements_missing"):
                load_required_capabilities(path, bot_id="finance")

    def test_gate_rejects_missing_or_unready_required_capabilities(self) -> None:
        failures = validate_capability_catalog(
            {
                "capabilities": [
                    {
                        "id": "tool.send_file",
                        "kind": "tool",
                        "enabled": False,
                        "status": "unavailable",
                        "reason": "delivery_not_bound",
                    }
                ]
            },
            required_ids=["tool.send_file", "tool.generate_image"],
        )

        self.assertEqual(
            failures,
            [
                {"id": "tool.generate_image", "reason": "not_registered"},
                {"id": "tool.send_file", "reason": "delivery_not_bound"},
            ],
        )

    def test_gate_rejects_ready_prompt_module_with_unready_tool(self) -> None:
        failures = validate_capability_catalog(
            {
                "capabilities": [
                    {
                        "id": "prompt_module.image_generation",
                        "kind": "prompt_module",
                        "enabled": True,
                        "status": "ready",
                        "toolTypes": ["generate_image"],
                    },
                    {
                        "id": "tool.generate_image",
                        "kind": "tool",
                        "enabled": False,
                        "status": "unavailable",
                    },
                    {
                        "id": "tool.send_file",
                        "kind": "tool",
                        "enabled": True,
                        "status": "ready",
                    },
                ]
            },
            required_ids=["tool.send_file"],
        )

        self.assertEqual(
            failures,
            [
                {
                    "id": "prompt_module.image_generation",
                    "reason": "ready_prompt_references_unready_tool:generate_image",
                }
            ],
        )

    def test_gate_accepts_ready_required_capabilities_and_consistent_prompt_modules(self) -> None:
        failures = validate_capability_catalog(
            {
                "capabilities": [
                    {
                        "id": "prompt_module.image_generation",
                        "kind": "prompt_module",
                        "enabled": True,
                        "status": "ready",
                        "toolTypes": ["generate_image"],
                    },
                    {
                        "id": "tool.generate_image",
                        "kind": "tool",
                        "enabled": True,
                        "status": "ready",
                    },
                ]
            },
            required_ids=["tool.generate_image"],
        )

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
