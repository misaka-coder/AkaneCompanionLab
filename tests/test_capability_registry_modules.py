from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from companion_v01 import capability_registry as legacy


class CapabilityRegistryModuleTests(unittest.TestCase):
    def test_runtime_exports_keep_shared_types_and_the_existing_broker(self):
        owners = {
            "models": ("CapabilitySnapshot", "CapabilityModule", "CapabilitySelection",
                       "CapabilityDisclosure", "resolve_capability_disclosures"),
            "offers": ("ServerLocalOfferIndex",),
            "selection": ("CapabilityRegistry",),
        }
        for owner, names in owners.items():
            module = importlib.import_module(f"companion_v01.capability_registry_runtime.{owner}")
            for name in names:
                with self.subTest(owner=owner, name=name):
                    self.assertIn(name, legacy.__all__)
                    self.assertIs(getattr(legacy, name), getattr(module, name))
        broker = importlib.import_module("companion_v01.executor_broker")
        for name in ("ExecutionReceipt", "BrokerExecutionResult", "ServerLocalBrokerResult",
                     "CapabilityOfferSource", "ExecutorBroker"):
            with self.subTest(broker=name):
                self.assertIs(getattr(legacy, name), getattr(broker, name))
        self.assertEqual(len(set(legacy.__all__)), len(legacy.__all__))
        self.assertTrue(all(hasattr(legacy, name) for name in legacy.__all__))

    def test_domain_contracts_keep_one_canonical_object_at_the_public_facade(self):
        contracts = {}
        for owner in (
            "memory_contracts", "browser_contracts", "attachment_contracts",
            "workspace_contracts", "delivery_contracts", "media_contracts",
        ):
            module = importlib.import_module(f"companion_v01.capability_registry_runtime.{owner}")
            for name, spec in vars(module).items():
                if not isinstance(spec, legacy.CapabilityToolSpec):
                    continue
                with self.subTest(owner=owner, name=name):
                    self.assertNotIn(name, contracts)
                    self.assertIn(name, legacy.__all__)
                    self.assertIs(getattr(legacy, name), spec)
                    contracts[name] = spec
        public = {name for name, value in vars(legacy).items()
                  if isinstance(value, legacy.CapabilityToolSpec)}
        self.assertEqual(set(contracts), public)
        self.assertEqual(len({spec.capability_id for spec in contracts.values()}), len(contracts))

    def test_implementation_dependencies_are_acyclic_and_do_not_reenter_the_facade(self):
        package = importlib.import_module("companion_v01.capability_registry_runtime")
        trees = {path.stem: ast.parse(path.read_text(encoding="utf-8"))
                 for path in Path(package.__file__).parent.glob("*.py")
                 if path.stem != "__init__"}
        graph = {name: set() for name in trees}
        for name, tree in trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported = node.module or ""
                    self.assertNotEqual(imported.split(".")[-1], "capability_registry", name)
                    if node.level == 1:
                        self.assertIn(imported, trees, name)
                        graph[name].add(imported)
                    if name.endswith("_contracts"):
                        self.assertNotIn(imported.split(".")[-1],
                                         {"config", "engine", "tool_runtime", "selection", "offers"}, name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[-1], "capability_registry", name)

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
