from __future__ import annotations

import ast
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

        self.assertIn("After LD006, the promptpack ownership decision is closed for now", doc)
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

    def test_charpack_compat_layer_does_not_import_private_helpers(self) -> None:
        source_path = ROOT / "companion_v01" / "desktop_pet_character_resources.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        private_imports: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "charpack_core.character_resources":
                continue
            private_imports.extend(alias.name for alias in node.names if alias.name.startswith("_"))

        self.assertEqual(private_imports, [])

    def test_mcp_adapter_wrapper_does_not_call_package_private_core_methods(self) -> None:
        source_path = ROOT / "companion_v01" / "capability_adapters" / "mcp_stdio.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        private_core_attrs: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
                continue
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "_core":
                private_core_attrs.append(node.attr)

        self.assertEqual(private_core_attrs, [])

    def test_dynamic_mcp_handler_builder_uses_public_descriptor_api(self) -> None:
        source = (ROOT / "companion_v01" / "engine_services" / "tool_rounds.py").read_text(encoding="utf-8")

        self.assertIn("adapter.descriptor_for_tool(tool)", source)
        self.assertNotIn("adapter._descriptor_for_tool(tool)", source)

    def test_ld006_records_promptpack_ownership_decision(self) -> None:
        doc = (ROOT / "docs" / "akane_lean_down_ld006_promptpack_ownership.md").read_text(encoding="utf-8")
        policy = (ROOT / "docs" / "package_reintegration_policy_m63.md").read_text(encoding="utf-8")

        self.assertIn("remains the owner of Akane final chat prompt assembly", policy)
        self.assertIn("PromptBuilder.build_final_generation_context()", doc)
        self.assertIn("remains the reusable primitive layer", doc)
        self.assertIn("must not add a second final chat prompt assembler", doc)

    def test_companion_runtime_does_not_add_promptpack_assembler_parallel_path(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "companion_v01").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "PromptAssembler" in source:
                offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
