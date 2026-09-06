"""Build a real static release directory, usable locally or on an HTTPS server."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_market(output: Path, *, manifest: Path | None = None) -> Path:
    manifest = manifest or PROJECT_ROOT / "plugins" / "market.toml"
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    entries = []
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for declaration in data["plugins"]:
        source = (manifest.parent / declaration["source"]).resolve()
        source.relative_to(manifest.parent.resolve())
        with tempfile.TemporaryDirectory(prefix="akane-market-build-") as temporary:
            root = Path(temporary)
            project = root / "project"
            shutil.copytree(
                source, project, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist")
            )
            metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
            wheelhouse = root / "wheels"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-index",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheelhouse),
                    str(project),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=600,
            )
            if completed.returncode:
                raise RuntimeError("plugin_market_build_failed")
            wheels = list(wheelhouse.glob("*.whl"))
            if len(wheels) != 1:
                raise RuntimeError("plugin_market_wheel_count_invalid")
            wheel = wheels[0]
            with wheel.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            destination = output / digest / wheel.name
            destination.parent.mkdir(exist_ok=True)
            if destination.exists():
                with destination.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise RuntimeError("plugin_market_existing_artifact_mismatch")
            else:
                temp_wheel = destination.with_suffix(".tmp")
                shutil.copyfile(wheel, temp_wheel)
                temp_wheel.replace(destination)
            entry = {key: value for key, value in declaration.items() if key != "source"}
            entry.update(
                version=metadata["version"],
                wheel=destination.relative_to(output).as_posix(),
                sha256=digest,
                size_bytes=destination.stat().st_size,
            )
            entries.append(entry)
    index = output / "index.json"
    raw = json.dumps(dict(schema_version=1, plugins=entries), ensure_ascii=False, indent=2)
    sys.path.insert(0, str(PROJECT_ROOT))
    from companion_v01.plugin_market import parse_index

    parse_index(raw.encode("utf-8"))
    temporary_index = index.with_suffix(".tmp")
    with temporary_index.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(raw + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary_index.replace(index)
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".plugin-market")
    args = parser.parse_args()
    result = build_market(args.output)
    print(f"Built static plugin market: {result}")
