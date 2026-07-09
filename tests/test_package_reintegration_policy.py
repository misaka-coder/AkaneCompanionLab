from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackageReintegrationPolicyTests(unittest.TestCase):
    def test_m63_policy_records_reintegration_as_replacement(self) -> None:
        doc = (ROOT / "docs" / "package_reintegration_policy_m63.md").read_text(encoding="utf-8")

        self.assertIn("Package Reintegration Policy M63", doc)
        self.assertIn("Package reintegration must reduce owning implementations.", doc)
        self.assertIn("Applying a package back to Akane is a replacement", doc)
        self.assertIn("deleted", doc)
        self.assertIn("thin adapter", doc)
        self.assertIn("documented migration window", doc)
        self.assertIn("Not allowed", doc)
        self.assertIn("old and new implementations both own business logic indefinitely", doc)
        self.assertIn("Reintegration Gate", doc)
        self.assertIn("Find the old implementation entry points with `rg`", doc)
        self.assertIn("Pick one authority implementation", doc)
        self.assertIn("public-hard", doc)
        self.assertIn("optional-runtime", doc)
        self.assertIn("dev-only", doc)
        self.assertIn("incubating", doc)
        self.assertIn("Out of scope for M63", doc)
        self.assertIn("`memcore`", doc)

    def test_m63_policy_audits_non_memcore_package_statuses(self) -> None:
        doc = (ROOT / "docs" / "package_reintegration_policy_m63.md").read_text(encoding="utf-8")

        expected_packages = (
            "promptpack-core",
            "charpack-core",
            "capcore",
            "capcore-provider-openai",
            "capcore-provider-native-tools",
            "capcore-provider-anthropic",
            "capcore-host-utils",
            "capcore-adapter-python",
            "capcore-adapter-mcp",
            "capcore-adapter-speech",
            "capcore-adapter-comfyui",
            "petcore-protocol",
            "petdesk-character-host",
            "petdesk-runtime",
            "petdesk-live2d-pixi-driver",
        )
        for package_name in expected_packages:
            self.assertIn(package_name, doc)

        self.assertIn("promptpack-core reintegration cleanup", doc)
        self.assertIn("PromptBuilder.build_final_generation_context()", doc)
        self.assertIn("old `ResourceManifest` implementation is no longer Akane-owned", doc)
        self.assertIn("Legacy JSON `tool_call` and provider-native tool calls coexist", doc)
        self.assertIn("Akane `/pet/*`", doc)
        self.assertIn("must remain a bridge, not a second runtime", doc)
        self.assertIn("Live2D Productization L1 pending", doc)

    def test_agents_records_package_reintegration_guardrail(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("抽包回填", agents)
        self.assertIn("必须减少权威实现数量", agents)
        self.assertIn("docs/package_reintegration_policy_m63.md", agents)
        self.assertIn("deleted / thin adapter / documented migration window", agents)


if __name__ == "__main__":
    unittest.main()
