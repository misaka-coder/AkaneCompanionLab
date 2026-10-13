"""Guards for the Memcore manager's static modules and compatibility entry."""
import ast
import builtins
import symtable
import unittest
from pathlib import Path

from companion_v01 import memcore_integration
from companion_v01.memcore_integration import contracts, manager

PACKAGE = Path(__file__).resolve().parents[1] / 'companion_v01' / 'memcore_integration'
ENTRY = PACKAGE / 'manager.py'
IMPLEMENTATION_MODULES = (
    'contracts', 'lifecycle', 'compaction', 'support', 'input_assembly', 'turns',
    'voice_projection', 'materials', 'catalog', 'projections', 'retrieval', 'completed_history', 'history_notice',
)


class MemcoreManagerModuleTests(unittest.TestCase):
    def test_module_contexts_have_no_unbound_global_names(self):
        for name in ('manager', *IMPLEMENTATION_MODULES):
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

    def test_implementation_is_reachable_without_importing_the_entry(self):
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
                    self.assertNotEqual(dependency, ENTRY, f'{path.name} imports the manager entry')
                if dependency.is_relative_to(PACKAGE) and dependency.exists():
                    graph[path].add(dependency)
                    visit(dependency)

        visit(ENTRY)
        expected = {PACKAGE / f'{name}.py' for name in IMPLEMENTATION_MODULES}
        self.assertTrue(expected.issubset(graph), 'an implementation module is not connected')
        active, complete = set(), set()

        def check(path):
            self.assertNotIn(path, active, f'cyclic manager import at {path.name}')
            if path in complete:
                return
            active.add(path)
            for dependency in graph[path]:
                check(dependency)
            active.remove(path)
            complete.add(path)

        check(ENTRY)

    def test_entry_and_package_reexport_the_same_contracts(self):
        for name in ('SUPPORTED_MEMORY_BACKENDS', 'MEMCORE_PROVIDER_PROFILE_ALIASES',
                     'MemcoreRuntimeStatus', 'normalize_memory_backend', 'normalize_visible_scope',
                     'resolve_memcore_provider_profile'):
            with self.subTest(name=name):
                self.assertIs(getattr(manager, name), getattr(contracts, name))
        for name in ('MemcoreManager', 'MemcoreRuntimeStatus', 'normalize_memory_backend'):
            with self.subTest(name=name):
                self.assertIs(getattr(memcore_integration, name), getattr(manager, name))

    def test_method_ownership_does_not_depend_on_mixin_order(self):
        owners = {}
        for owner in manager.MemcoreManager.__mro__:
            if not owner.__module__.startswith('companion_v01.memcore_integration.'):
                continue
            for name, value in vars(owner).items():
                if name.startswith('__') or not isinstance(value, (staticmethod, classmethod, property)) and not callable(value):
                    continue
                self.assertNotIn(name, owners, f'{name} has multiple manager owners')
                owners[name] = owner
        self.assertTrue(owners)

    def test_implementation_modules_remain_bounded(self):
        for name in IMPLEMENTATION_MODULES:
            path = PACKAGE / f'{name}.py'
            self.assertLessEqual(len(path.read_text(encoding='utf-8').splitlines()), 1100, path.name)

    def test_entry_keeps_only_construction_and_status(self):
        source = ENTRY.read_text(encoding='utf-8')
        self.assertLessEqual(len(source.splitlines()), 200)
        definition = next(node for node in ast.parse(source).body
                          if isinstance(node, ast.ClassDef) and node.name == 'MemcoreManager')
        methods = {node.name for node in definition.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(methods, {'__init__', 'from_engine', 'enabled', 'available', 'retrieval_mode', 'status'})

    def test_memcore_imports_remain_lazy_for_legacy_mode(self):
        for name in ('manager', *IMPLEMENTATION_MODULES):
            path = PACKAGE / f'{name}.py'
            for node in ast.parse(path.read_text(encoding='utf-8')).body:
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [node.module or '']
                else:
                    continue
                self.assertFalse(any(module == 'memcore' or module.startswith('memcore.')
                                     for module in modules), f'{path.name} eagerly imports Memcore')


if __name__ == '__main__':
    unittest.main()
