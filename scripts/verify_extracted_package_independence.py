from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify(wheelhouse: Path) -> None:
    wheelhouse = wheelhouse.resolve()
    manifest_path = wheelhouse / "akane-package-wheelhouse.json"
    if not manifest_path.is_file():
        raise RuntimeError("wheelhouse_manifest_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not bool(manifest.get("runtime_dependencies_downloaded")):
        raise RuntimeError("wheelhouse_runtime_dependency_closure_missing")

    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean_env["PYTHONNOUSERSITE"] = "1"

    with tempfile.TemporaryDirectory(prefix="akane-package-wheel-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        venv = temp_root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=temp_root, env=clean_env)
        python = _venv_python(venv)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "-r",
                str(ROOT / "requirements.txt"),
            ],
            cwd=temp_root,
            env=clean_env,
        )
        _run([str(python), str(ROOT / "scripts" / "check_packaged_dependencies.py")], cwd=temp_root, env=clean_env)
        _run([str(python), "-m", "pip", "check"], cwd=temp_root, env=clean_env)
        _run(
            [str(python), str(ROOT / "scripts" / "smoke_extracted_package_ecosystem.py")], cwd=temp_root, env=clean_env
        )
        _run([str(python), str(ROOT / "scripts" / "smoke_ai_product_host_turn.py")], cwd=temp_root, env=clean_env)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install Akane-owned wheels into a source-blind empty venv and run composition smokes.",
    )
    parser.add_argument("--wheelhouse", type=Path, default=ROOT / "package_wheels")
    args = parser.parse_args()
    try:
        verify(args.wheelhouse)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"AKANE_PACKAGE_INDEPENDENCE_FAILED:{exc}")
        return 1
    print("AKANE_PACKAGE_INDEPENDENCE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
