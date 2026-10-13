from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from companion_v01.local_capabilities import storage
from companion_v01.local_capability_config import (
    save_workflow_file,
    write_capability_config,
    write_host_mcp_server_configs,
)


class LocalCapabilityStorageTests(unittest.TestCase):
    def writers(self, root):
        workflow = {"1": {"inputs": {"image": "肖像.png"}}}
        return (
            (
                "profile",
                root / "owner/capabilities/capabilities.yaml",
                partial(
                    write_capability_config,
                    base_dir=root,
                    profile_user_id="owner",
                    config={"providers": {"provider.comfyui.local": {"enabled": True, "endpoint": "http://127.0.0.1:8188"}}},
                ),
                lambda data: self.assertEqual(data["providers"]["provider.comfyui.local"]["endpoint"], "http://127.0.0.1:8188"),
            ),
            (
                "host",
                root / "capabilities/mcp_servers.yaml",
                partial(
                    write_host_mcp_server_configs,
                    base_dir=root,
                    mcp_servers={"demo": {"transport": "stdio", "command": "python", "enabled": True}},
                ),
                lambda data: self.assertEqual(data["mcpServers"]["demo"]["command"], "python"),
            ),
            (
                "workflow",
                root / "owner/capabilities/workflows/custom.json",
                partial(
                    save_workflow_file,
                    base_dir=root,
                    profile_user_id="owner",
                    workflow_id="workflow.workshop.portrait.cutout",
                    payload={"workflowPath": "workflows/custom.json", "workflowJson": json.dumps(workflow, ensure_ascii=False)},
                ),
                lambda data: self.assertEqual(data, workflow),
            ),
        )

    def existing_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"original": true}\n')
        return path.read_bytes(), set(path.parent.iterdir())

    def test_failed_replacements_preserve_original_files_and_remove_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, path, write, _check in self.writers(Path(directory)):
                with self.subTest(writer=name):
                    original, children = self.existing_file(path)

                    def fail_replace(temporary, target):
                        self.assertEqual(temporary.parent, path.parent)
                        self.assertEqual(target, path)
                        self.assertNotEqual(temporary, target)
                        self.assertTrue(temporary.is_file())
                        raise OSError("replace-failed")

                    with patch.object(Path, "replace", autospec=True, side_effect=fail_replace):
                        with self.assertRaisesRegex(OSError, "replace-failed"):
                            write()
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(set(path.parent.iterdir()), children)

    def test_failed_temporary_writes_preserve_original_files_and_clean_partial_output(self):
        create_temporary_file = tempfile.NamedTemporaryFile

        @contextmanager
        def fail_write(*args, **kwargs):
            with create_temporary_file(*args, **kwargs) as handle:
                handle.write("partial-output")
                yield SimpleNamespace(name=handle.name, write=Mock(side_effect=OSError("write-failed")))

        with tempfile.TemporaryDirectory() as directory:
            for name, path, write, _check in self.writers(Path(directory)):
                with self.subTest(writer=name):
                    original, children = self.existing_file(path)
                    with patch.object(storage.tempfile, "NamedTemporaryFile", side_effect=fail_write):
                        with self.assertRaisesRegex(OSError, "write-failed"):
                            write()
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(set(path.parent.iterdir()), children)

    def test_successful_writes_replace_files_without_leaving_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, path, write, check in self.writers(Path(directory)):
                with self.subTest(writer=name):
                    _original, children = self.existing_file(path)
                    result = write()
                    if name == "workflow":
                        self.assertTrue(result["ok"])
                        self.assertFalse(result["executionReady"])
                    text = path.read_text(encoding="utf-8")
                    self.assertTrue(text.endswith("\n"))
                    check(json.loads(text))
                    self.assertEqual(set(path.parent.iterdir()), children)


if __name__ == "__main__":
    unittest.main()
