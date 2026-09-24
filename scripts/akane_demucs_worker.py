"""Compatibility CLI for the independent audio-separation package."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.akane_separation_package import local_demucs_class


async def execute(args):
    runtime = local_demucs_class()(python=sys.executable, package_root=args.package_root, model=args.model)
    try:
        if args.probe:
            return await runtime.probe()
        if not args.source or not args.output_root:
            return {"ok": False, "status": "failed", "reason": "demucs_worker_input_missing"}
        result = await runtime.separate_media(source=Path(args.source), output_root=Path(args.output_root))
        return {**result, "device": result.get("device_used", "")}
    finally:
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", default="")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--source", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--model", default="htdemucs", choices=("htdemucs", "htdemucs_ft"))
    args = parser.parse_args()
    try:
        payload = asyncio.run(execute(args))
    except Exception:
        payload = {"ok": False, "status": "failed", "reason": "demucs_worker_failed"}
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
