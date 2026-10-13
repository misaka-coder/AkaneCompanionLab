"""Static response-stage boundaries and compatibility contracts."""

from __future__ import annotations

import ast
import builtins
import inspect
from pathlib import Path
import symtable
import unittest

from companion_v01.engine_services import (
    response_assembly,
    response_budget,
    response_builder,
    response_catalog,
    response_materials,
    response_projection,
)


PACKAGE = Path(__file__).resolve().parents[1] / "companion_v01/engine_services"
ENTRY = PACKAGE / "response_builder.py"
MODULES = (
    "response_contracts",
    "response_materials",
    "response_projection",
    "response_catalog",
    "response_exposure",
    "response_assembly",
    "response_budget",
)


class ResponseBuilderModuleTests(unittest.TestCase):
    def test_stages_have_no_unbound_globals(self):
        for name in ("response_builder", *MODULES):
            path = PACKAGE / f"{name}.py"
            table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
            bound = {
                symbol.get_name()
                for symbol in table.get_symbols()
                if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
            }
            bound.update(dir(builtins))
            bound.update({"__name__", "__package__", "__file__", "__doc__", "__builtins__"})

            def check(scope):
                for symbol in scope.get_symbols():
                    if symbol.is_referenced() and symbol.is_global():
                        self.assertIn(symbol.get_name(), bound, f"{path.name}: unbound global")
                for child in scope.get_children():
                    check(child)

            check(table)

    def test_all_stages_are_reachable_without_reverse_or_cyclic_imports(self):
        expected = {PACKAGE / f"{name}.py" for name in MODULES}
        graph = {}

        def visit(path):
            if path in graph:
                return
            graph[path] = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                self.assertTrue(all(alias.name != "*" for alias in node.names), f"{path.name}: wildcard import")
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                dependency = base.joinpath(*(node.module or "").split(".")).with_suffix(".py")
                if path != ENTRY:
                    self.assertNotEqual(dependency, ENTRY, f"{path.name}: reverse entry import")
                if dependency in expected or dependency == ENTRY:
                    graph[path].add(dependency)
                    visit(dependency)

        visit(ENTRY)
        self.assertEqual(set(graph) - {ENTRY}, expected)
        active, complete = set(), set()

        def check(path):
            self.assertNotIn(path, active, f"cyclic response import at {path.name}")
            if path in complete:
                return
            active.add(path)
            for dependency in graph[path]:
                check(dependency)
            active.remove(path)
            complete.add(path)

        check(ENTRY)

    def test_entry_and_stage_implementations_remain_bounded(self):
        entry_source = ENTRY.read_text(encoding="utf-8")
        self.assertLessEqual(len(entry_source.splitlines()), 180)
        entry = next(
            node
            for node in ast.parse(entry_source).body
            if isinstance(node, ast.FunctionDef) and node.name == "prepare_context"
        )
        self.assertFalse(
            any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                for statement in entry.body
                for node in ast.walk(statement)
            )
        )
        for name in MODULES:
            source = (PACKAGE / f"{name}.py").read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 750, name)
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertLessEqual(node.end_lineno - node.lineno + 1, 300, f"{name}.{node.name}")

    def test_legacy_prepare_signature_and_defaults_are_preserved(self):
        required = (
            "engine",
            "session_id",
            "user_message",
            "recent_raw",
            "recent_episodic_summaries",
            "recent_semantic_summaries",
            "confirmed_snippets",
            "now_ts",
            "profile_user_id",
        )
        defaults = {
            "current_visual_payload": None,
            "extra_user_context": "",
            "stable_system_context": "",
            "client_context": None,
            "resource_manifest": None,
            "character_pack_id": "",
            "allow_tool_call": True,
            "final_debug_enabled": None,
            "enable_native_tools": False,
            "chat_model_override": "",
            "execution_target": None,
            "post_user_turns": None,
            "prompt_exclude_source_ids": None,
            "domain_profile_id": "",
            "prompt_scope": "",
            "current_user_source_id": "",
            "current_input_transient": False,
            "current_actor_relation": "",
            "authorization_profile_user_id": "",
            "actor_stable_id": "",
            "actor_profile_user_id": "",
            "request_projection_state": None,
            "native_user_images": None,
        }
        parameters = inspect.signature(response_builder.prepare_context).parameters
        self.assertEqual(tuple(parameters), (*required, *defaults))
        for name, parameter in parameters.items():
            self.assertEqual(parameter.default, defaults.get(name, inspect.Parameter.empty), name)
            expected = inspect.Parameter.POSITIONAL_OR_KEYWORD if name == "engine" else inspect.Parameter.KEYWORD_ONLY
            self.assertEqual(parameter.kind, expected, name)

    def test_compatible_helpers_resolve_to_single_implementation_owners(self):
        owners = {
            response_catalog: (
                "_capability_catalog_source",
                "_catalog_message_text",
                "_shape_capability_catalog_projection",
                "_split_active_projection_turns",
                "_visible_projection_source_ids",
                "_overlay_ephemeral_provider_evidence",
            ),
            response_projection: (
                "PROJECTION_READ_MIGRATION_REASONS",
                "_safe_projection_failure_code",
                "_compare_memcore_projection_shadow",
                "_projected_current_message_text",
                "_build_memcore_provider_history",
                "_projection_failure_context",
                "_memory_backend",
            ),
            response_budget: (
                "_estimate_generation_context_tokens",
                "_drop_oldest_prompt_lines",
                "_trim_oldest_provider_history_group",
                "_trim_oldest_prompt_raw_record",
            ),
            response_assembly: (
                "COMPACT_READBACK_STABLE_HINT",
                "build_compact_readback_hint",
                "with_compact_readback_hint",
                "with_current_actor_relation",
            ),
            response_materials: ("_declared_activity_context_lifecycle",),
        }
        for module, names in owners.items():
            for name in names:
                self.assertIs(getattr(response_builder, name), getattr(module, name), name)


if __name__ == "__main__":
    unittest.main()
