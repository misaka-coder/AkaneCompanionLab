"""Build a platform-native offline backend bundle from verified public sources."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_extracted_package_wheelhouse import build_wheelhouse
from scripts.package_supply import public_package_sources

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with public_package_sources(ROOT) as sources:
        build_wheelhouse(source_root=sources, output_dir=args.output_dir,
                         profile="backend", download_runtime=True)
    print("AKANE_PUBLIC_BACKEND_WHEELHOUSE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
