"""Guards for static tool execution boundaries and the engine compatibility API."""
import ast
import builtins
import inspect
import symtable
import unittest
from pathlib import Path

from companion_v01 import engine
from companion_v01.engine_services import tool_execution, tool_prompt_context

PACKAGE = Path(__file__).resolve().parents[1] / 'companion_v01' / 'engine_services'
ENTRY = PACKAGE / 'tool_execution.py'
IMPLEMENTATION_MODULES = (
    'tool_trace', 'tool_history_projection', 'tool_protocol', 'tool_batch_orchestration',
    'tool_capabilities', 'tool_prompt_context', 'tool_hooks',
)


class ToolExecutionModuleTests(unittest.TestCase):
    def test_module_contexts_have_no_unbound_global_names(self):
        for name in ('tool_execution', *IMPLEMENTATION_MODULES):
            path = PACKAGE / f'{name}.py'
            table = symtable.symtable(path.read_text(encoding='utf-8'), str(path), 'exec')
            bound = {symbol.get_name() for symbol in table.get_symbols()
                     if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()}
            bound.update(dir(builtins))
            bound.update({'__name__', '__package__', '__file__', '__doc__', '__builtins__'})

            def check(scope):
                for symbol in scope.get_symbols():
                    if symbol.is_referenced() and symbol.is_global():
                        self.assertIn(symbol.get_name(), bound, f'{path.name}: unbound global')
                for child in scope.get_children():
                    check(child)

            check(table)

    def test_static_implementations_are_reachable_without_reverse_imports(self):
        expected = {PACKAGE / f'{name}.py' for name in IMPLEMENTATION_MODULES}
        graph = {}

        def visit(path):
            if path in graph:
                return
            graph[path] = set()
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                dependency = base.joinpath(*(node.module or '').split('.')).with_suffix('.py')
                if path != ENTRY:
                    self.assertNotEqual(dependency, ENTRY, f'{path.name} imports the compatibility entry')
                if dependency in expected or dependency == ENTRY:
                    graph[path].add(dependency)
                    visit(dependency)

        visit(ENTRY)
        self.assertEqual(set(graph) - {ENTRY}, expected, 'an implementation module is disconnected')
        active, complete = set(), set()

        def check(path):
            self.assertNotIn(path, active, f'cyclic tool implementation import at {path.name}')
            if path in complete:
                return
            active.add(path)
            for dependency in graph[path]:
                check(dependency)
            active.remove(path)
            complete.add(path)

        check(ENTRY)

    def test_engine_keeps_the_same_entry_and_single_owner_methods(self):
        self.assertIs(engine.ToolExecutionMixin, tool_execution.ToolExecutionMixin)
        self.assertIs(engine.MEDIA_PRESET_ROUTING, tool_execution.MEDIA_PRESET_ROUTING)
        self.assertTrue(issubclass(engine.AkaneMemoryEngine, tool_execution.ToolExecutionMixin))
        owners = {}
        for owner in tool_execution.ToolExecutionMixin.__mro__:
            if owner is object:
                continue
            for name, value in vars(owner).items():
                if name.startswith('__') or not isinstance(value, (staticmethod, classmethod, property)) and not callable(value):
                    continue
                self.assertNotIn(name, owners, f'{name} has multiple tool implementation owners')
                owners[name] = owner
                self.assertIs(inspect.getattr_static(engine.AkaneMemoryEngine, name), value,
                              f'{name} no longer resolves to the intended implementation')
        self.assertTrue(owners)

    def test_implementation_modules_remain_bounded(self):
        for name in IMPLEMENTATION_MODULES:
            path = PACKAGE / f'{name}.py'
            self.assertLessEqual(len(path.read_text(encoding='utf-8').splitlines()), 800, path.name)

    def test_entry_remains_a_static_composition(self):
        source = ENTRY.read_text(encoding='utf-8')
        self.assertLessEqual(len(source.splitlines()), 60)
        definition = next(node for node in ast.parse(source).body
                          if isinstance(node, ast.ClassDef) and node.name == 'ToolExecutionMixin')
        self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                             for node in definition.body))
        self.assertEqual(len(definition.bases), len(IMPLEMENTATION_MODULES))

    def test_media_routing_reexports_the_same_constant(self):
        self.assertIs(tool_execution.MEDIA_PRESET_ROUTING, tool_prompt_context.MEDIA_PRESET_ROUTING)


if __name__ == '__main__':
    unittest.main()
