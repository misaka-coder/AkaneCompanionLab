#!/usr/bin/env python3
"""Verify an extracted Akane cloud bundle before it is installed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prepare_akane_cloud_bundle import CloudBundleError, verify_prepared_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an extracted Akane cloud bundle")
    parser.add_argument("--bundle-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_prepared_bundle(args.bundle_dir)
    except CloudBundleError as exc:
        print(json.dumps({"ok": False, "status": "failed", "reason": exc.reason}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
