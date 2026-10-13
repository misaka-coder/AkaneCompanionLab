from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from companion_v01 import local_capability_config as legacy
from companion_v01.local_capabilities import storage


class LocalCapabilityModuleTests(unittest.TestCase):
    def test_legacy_api_uses_the_same_objects_and_write_lock_as_owners(self):
        owners = {
            "definitions": ("ProviderConfigSpec", "WorkflowConfigSpec", "CONFIGURABLE_PROVIDER_BY_ID"),
            "storage": ("load_capability_config", "write_capability_config", "plugin_connection_config", "tool_exposure_config"),
            "approval": ("get_approval_policy_config", "save_capability_approval_modes"),
            "approval_policy": ("normalize_approval_policy_config", "apply_approval_policy_to_entry"),
            "providers": ("save_provider_config", "check_provider_health"),
            "provider_validation": ("normalize_provider_config_payload", "normalize_local_http_endpoint"),
            "voice_profiles": ("save_voice_profile_config", "get_voice_profile_runtime_config"),
            "voice_validation": ("normalize_voice_profile_config_payload",),
            "mcp": ("get_effective_mcp_server_configs", "save_mcp_server_config"),
            "mcp_validation": ("normalize_mcp_server_config_payload",),
            "workflows": ("save_workflow_file", "preflight_workflow_execution"),
            "workflow_validation": ("normalize_workflow_path",),
        }
        for owner, names in owners.items():
            module = importlib.import_module(f"companion_v01.local_capabilities.{owner}")
            for name in names:
                with self.subTest(owner=owner, name=name):
                    self.assertIn(name, legacy.__all__)
                    self.assertIs(getattr(legacy, name), getattr(module, name))
        self.assertIs(legacy._CONFIG_WRITE_LOCK, storage._CONFIG_WRITE_LOCK)

    def test_implementation_dependencies_are_acyclic_and_keep_validation_pure(self):
        package_dir = Path(storage.__file__).parent
        trees = {
            path.stem: ast.parse(path.read_text(encoding="utf-8"))
            for path in package_dir.glob("*.py")
            if path.stem != "__init__"
        }
        graph = {name: set() for name in trees}
        locks = []
        for name, tree in trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("local_capability_config", node.module or "", name)
                    if node.level == 1:
                        self.assertIn(node.module, trees, name)
                        graph[name].add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("local_capability_config", alias.name, name)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "RLock":
                        locks.append(name)
        self.assertEqual(locks, ["storage"])
        operations = {"approval", "providers", "voice_profiles", "mcp", "workflows"}
        for name, dependencies in graph.items():
            if name.endswith("_validation") or name in {"common", "definitions", "approval_policy", "storage"}:
                self.assertFalse(dependencies & operations, name)
            if name.endswith("_validation"):
                self.assertNotIn("storage", dependencies, name)

        def visit(name, active, done):
            self.assertNotIn(name, active, f"Dependency cycle: {active} -> {name}")
            if name in done:
                return
            for dependency in graph[name]:
                visit(dependency, [*active, name], done)
            done.add(name)

        done = set()
        for name in graph:
            visit(name, [], done)


if __name__ == "__main__":
    unittest.main()
