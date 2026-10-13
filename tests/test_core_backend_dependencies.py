from __future__ import annotations

import importlib.abc
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CoreBackendDependencyTests(unittest.TestCase):
    def test_legacy_vector_module_does_not_import_optional_chroma(self) -> None:
        script = '''
import importlib.abc, sys, tempfile
from pathlib import Path
class WithoutChroma(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'chromadb' or fullname.startswith('chromadb.'):
            raise ModuleNotFoundError('chromadb', name='chromadb')
sys.meta_path.insert(0, WithoutChroma())
from companion_v01.vector_store import VectorStore
assert 'chromadb' not in sys.modules
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'unused-index'
    try:
        VectorStore(target)
    except RuntimeError as exc:
        assert 'vector_memory_dependency_missing' in str(exc)
    else:
        raise AssertionError('absent Chroma must not look available')
    assert not target.exists()
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_profile_excludes_heavy_optional_packages(self) -> None:
        runtime = (ROOT / "requirements-backend-runtime.txt").read_text(encoding="utf-8")
        packages = {line.split("#", 1)[0].strip().split("=")[0].split(">=")[0]
                    for line in runtime.splitlines() if line.strip() and not line.startswith("#")}
        self.assertTrue({"fastapi", "openai", "pydantic-settings"}.issubset(packages))
        self.assertFalse({"chromadb", "torch", "playwright", "edge-tts", "python-docx"} & packages)


if __name__ == "__main__":
    unittest.main()
