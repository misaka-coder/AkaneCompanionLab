from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from companion_v01.plugin_implementation_revision import distribution_revision


class PluginImplementationRevisionTests(unittest.TestCase):
    def test_editable_source_changes_but_process_noise_does_not_change_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src/sample_plugin"
            source.mkdir(parents=True)
            body = source / "__init__.py"
            body.write_text("def invoke(): return 1\n")
            distribution = SimpleNamespace(files=[], locate_file=lambda name: root / name,
                read_text=lambda name: json.dumps({"url": root.as_uri(), "dir_info": {"editable": True}}))
            entry = SimpleNamespace(value="sample_plugin:create_plugin", dist=distribution)
            initial = distribution_revision(entry)
            self.assertTrue(initial)
            cache = source / "__pycache__"
            cache.mkdir()
            (cache / "sample.pyc").write_bytes(b"different process")
            self.assertEqual(distribution_revision(entry), initial)
            body.write_text("def invoke(): return 2\n")
            self.assertNotEqual(distribution_revision(entry), initial)

    def test_normal_distribution_revision_is_stable_across_new_distribution_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plugin.py").write_text("value = 1")
            def entry():
                return SimpleNamespace(value="plugin:create_plugin", dist=SimpleNamespace(
                    files=[Path("plugin.py")], locate_file=lambda name: root / name, read_text=lambda name: None))
            before = distribution_revision(entry())
            self.assertEqual(distribution_revision(entry()), before)
            (root / "plugin.py").write_text("value = 2")
            self.assertNotEqual(distribution_revision(entry()), before)

    def test_unknown_source_does_not_invent_a_stable_implementation_revision(self):
        self.assertEqual(distribution_revision(SimpleNamespace()), "")
