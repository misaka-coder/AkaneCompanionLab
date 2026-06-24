from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_GLOBS = (
    "companion_v01/**/*.py",
    "tests/**/*.py",
    "desktop_pet_next/src/**/*.js",
    "desktop_pet_next/src-tauri/src/**/*.rs",
)


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for pattern in TEXT_GLOBS:
        files.extend(path for path in ROOT.glob(pattern) if path.is_file())
    return sorted(set(files))


class RepositoryHygieneTests(unittest.TestCase):
    def test_source_files_do_not_contain_double_crlf(self) -> None:
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in _iter_text_files()
            if b"\r\r\n" in path.read_bytes()
        ]
        self.assertEqual(offenders, [], "CRCRLF line endings found: " + ", ".join(offenders[:12]))

    def test_python_files_do_not_use_smart_quotes_as_syntax(self) -> None:
        offenders: list[str] = []
        for path in sorted((ROOT / "companion_v01").glob("**/*.py")) + sorted((ROOT / "tests").glob("**/*.py")):
            if not path.is_file():
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.lstrip()
                if stripped.startswith(("“", "”")):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line_no}")
                    break
        self.assertEqual(offenders, [], "Python lines start with smart quotes: " + ", ".join(offenders[:12]))


if __name__ == "__main__":
    unittest.main()
