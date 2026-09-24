"""Keep the documented V1 migration inventory from growing during V2 work."""

import ast
from pathlib import Path
import unittest


class LegacyMigrationInventoryTests(unittest.TestCase):
    def test_first_party_tools_use_result_followup_declarations(self):
        root = Path(__file__).resolve().parents[1]
        for folder in (root / "plugins", root / "examples/plugins"):
            for path in folder.glob("*/src/**/*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Call):
                        for keyword in node.keywords:
                            if keyword.arg == "raw" and isinstance(keyword.value, ast.Dict):
                                fields = {key.value for key in keyword.value.keys if isinstance(key, ast.Constant)}
                                self.assertFalse(fields & {"completion_mode", "model_followup"}, path)

    def test_first_party_legacy_registration_does_not_expand(self):
        root = Path(__file__).resolve().parents[1]
        inventory = {}
        for folder in (root / "plugins", root / "examples/plugins"):
            for path in folder.glob("*/src/**/*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
                        "add_event_handler", "get_agent_event_port",
                    }:
                        key = (path.relative_to(root).as_posix(), node.func.attr)
                        inventory[key] = inventory.get(key, 0) + 1
        self.assertEqual(
            inventory, {},
            "First-party plugins use the public SDK; do not reintroduce V1 registration",
        )
