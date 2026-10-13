"""Prevent orchestration modules and low-level device code from recoupling."""

from __future__ import annotations

import ast
import unittest
from importlib.util import resolve_name
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def imported_modules(path: Path) -> set[str]:
    package = ".".join(path.relative_to(ROOT).parent.parts)
    imports = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.add(resolve_name(module, package) if node.level else module)
    return imports


class ToolOrchestrationBoundaryTests(unittest.TestCase):
    def test_compatibility_entry_point_contains_no_implementation(self):
        path = ROOT / "companion_v01/tool_orchestration_engine.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        implementations = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertEqual(implementations, [])

    def test_implementations_do_not_import_compatibility_facades(self):
        blocked = {
            "companion_v01.tool_orchestration_engine",
            "companion_v01.tool_runtime",
        }
        for path in (ROOT / "companion_v01/tool_orchestration").glob("*.py"):
            with self.subTest(module=path.name):
                self.assertFalse(imported_modules(path).intersection(blocked))

    def test_device_layers_do_not_import_the_orchestration_entry_point(self):
        for name in ("computer_use/dispatch.py", "personal_browser.py"):
            with self.subTest(module=name):
                self.assertNotIn(
                    "companion_v01.tool_orchestration_engine",
                    imported_modules(ROOT / "companion_v01" / name),
                )


if __name__ == "__main__":
    unittest.main()
