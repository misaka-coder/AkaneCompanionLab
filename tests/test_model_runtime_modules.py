"""Guards for the model runtime's static boundaries and compatibility entry."""
import ast
import builtins
import symtable
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01 import llm_runtime
from companion_v01.model_runtime import models

ROOT = Path(__file__).resolve().parents[1] / 'companion_v01'
ENTRY = ROOT / 'llm_runtime.py'
PACKAGE = ROOT / 'model_runtime'


class ModelRuntimeModuleTests(unittest.TestCase):
    def test_module_contexts_have_no_unbound_global_names(self):
        for path in PACKAGE.glob('*.py'):
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
                if path.is_relative_to(PACKAGE):
                    self.assertNotEqual(dependency, ENTRY, f'{path.name} imports the runtime entry')
                if dependency.is_relative_to(PACKAGE) and dependency.exists():
                    graph[path].add(dependency)
                    visit(dependency)

        visit(ENTRY)
        expected = set(PACKAGE.glob('*.py')) - {PACKAGE / '__init__.py'}
        self.assertEqual(set(graph) - {ENTRY}, expected, 'an implementation module is not connected')
        active, complete = set(), set()

        def check(path):
            self.assertNotIn(path, active, f'cyclic runtime import at {path.name}')
            if path in complete:
                return
            active.add(path)
            for dependency in graph[path]:
                check(dependency)
            active.remove(path)
            complete.add(path)

        check(ENTRY)

    def test_entry_reexports_the_same_model_contracts(self):
        for name in ('ModelBundle', 'ModelExecutionTarget', 'ProviderToolProfile',
                     'NDJSONCallResult', 'ChatJSONResult', 'ChatJSONStreamResult', 'ChatTextResult'):
            with self.subTest(name=name):
                self.assertIs(getattr(llm_runtime, name), getattr(models, name))

    def test_method_ownership_does_not_depend_on_mixin_order(self):
        owners = {}
        for owner in llm_runtime.LLMRuntime.__mro__:
            if not owner.__module__.startswith('companion_v01.model_runtime.'):
                continue
            for name, value in vars(owner).items():
                if name.startswith('__') or not isinstance(value, (staticmethod, classmethod, property)) and not callable(value):
                    continue
                self.assertNotIn(name, owners, f'{name} has multiple runtime owners')
                owners[name] = owner
        self.assertTrue(owners)

    def test_log_directory_follows_the_supplied_instance_configuration(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            'companion_v01.llm_runtime.build_llm_client',
            side_effect=lambda **kwargs: SimpleNamespace(_akane_protocol='openai'),
        ):
            root = Path(temporary)
            first = llm_runtime.LLMRuntime(config_module=SimpleNamespace(LOG_DIR=str(root / 'first')))
            second = llm_runtime.LLMRuntime(config_module=SimpleNamespace(LOG_DIR=str(root / 'second')))
            explicit = llm_runtime.LLMRuntime(
                config_module=SimpleNamespace(LOG_DIR=str(root / 'configured')),
                log_dir=root / 'explicit',
            )
            self.assertEqual(first.log_dir, root / 'first')
            self.assertEqual(second.log_dir, root / 'second')
            self.assertEqual(explicit.log_dir, root / 'explicit')
            first._record_metric('errors')
            self.assertEqual(second.snapshot_metrics()['errors'], 0)


if __name__ == '__main__':
    unittest.main()
