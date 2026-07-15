from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from companion_v01.instance_profile import resolve_instance_context
from companion_v01.instance_runtime import InstanceRuntimeError, bind_instance_runtime


MANIFEST = """\
schema_version = 1
instance_id = "akane-personal"
character_pack_id = "akane_v1"

[features]
care = true

[channels.qq]
enabled = true
profile_ref = "qq.personal"
"""


class InstanceRuntimeTests(unittest.TestCase):
    def _context(self, root: Path, *, instance_id: str = "akane-personal"):
        manifest_dir = root / "instances" / instance_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_dir.joinpath("instance.toml").write_text(
            MANIFEST.replace("akane-personal", instance_id),
            encoding="utf-8",
        )
        return resolve_instance_context(data_root=root, selected_instance_id=instance_id)

    def test_named_instance_requires_process_environment_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = self._context(root)
            with self.assertRaises(InstanceRuntimeError) as raised:
                bind_instance_runtime(context, data_root=root, explicit_data_root=False)

            self.assertEqual(raised.exception.status, "invalid_config")
            self.assertEqual(raised.exception.reason, "named_instance_requires_explicit_data_root")
            self.assertFalse((root / "instance-binding.json").exists())
            self.assertNotIn(temp_dir, str(raised.exception))

    def test_binding_file_and_public_health_are_safe_and_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lease = bind_instance_runtime(
                self._context(root),
                data_root=root,
                explicit_data_root=True,
            )
            try:
                self.assertEqual(
                    lease.public_health_snapshot(),
                    {
                        "status": "ok",
                        "instance_id": "akane-personal",
                        "root_binding": "valid",
                    },
                )
                binding = json.loads((root / "instance-binding.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    binding,
                    {"instance_id": "akane-personal", "schema_version": 1},
                )
                self.assertNotIn(str(root), json.dumps(binding))
                self.assertNotIn("profile_ref", binding)
            finally:
                lease.release()

            self.assertEqual(lease.root_binding_status, "released")
            lease.release()

    def test_same_root_cannot_be_leased_twice_in_one_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = self._context(root)
            first = bind_instance_runtime(context, data_root=root, explicit_data_root=True)
            try:
                with self.assertRaises(InstanceRuntimeError) as raised:
                    bind_instance_runtime(context, data_root=root, explicit_data_root=True)
                self.assertEqual(raised.exception.reason, "instance_root_locked")
            finally:
                first.release()

    def test_root_binding_cannot_change_instance_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = bind_instance_runtime(
                self._context(root),
                data_root=root,
                explicit_data_root=True,
            )
            first.release()

            with self.assertRaises(InstanceRuntimeError) as raised:
                bind_instance_runtime(
                    self._context(root, instance_id="finance-prod"),
                    data_root=root,
                    explicit_data_root=True,
                )
            self.assertEqual(raised.exception.status, "conflict")
            self.assertEqual(raised.exception.reason, "instance_root_bound_to_other_instance")

    def test_os_lock_rejects_a_second_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._context(root)
            script = """
import sys
from pathlib import Path
from companion_v01.instance_profile import resolve_instance_context
from companion_v01.instance_runtime import bind_instance_runtime
root = Path(sys.argv[1])
context = resolve_instance_context(data_root=root, selected_instance_id='akane-personal')
lease = bind_instance_runtime(context, data_root=root, explicit_data_root=True)
print('ready', flush=True)
sys.stdin.readline()
lease.release()
"""
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", script, str(root)],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = process.stdout.readline().strip() if process.stdout is not None else ""
                if ready != "ready":
                    stderr = process.stderr.read() if process.stderr is not None else ""
                    self.fail(f"lock helper failed: {stderr}")
                with self.assertRaises(InstanceRuntimeError) as raised:
                    bind_instance_runtime(
                        self._context(root),
                        data_root=root,
                        explicit_data_root=True,
                    )
                self.assertEqual(raised.exception.reason, "instance_root_locked")
            finally:
                if process.stdin is not None:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
