"""Standalone service/CLI binding, never a model-tool activation fallback."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys


def local_demucs_class():
    try:
        from akane_audio_separation.local import LocalDemucs
    except ModuleNotFoundError as exc:
        if exc.name != "akane_audio_separation":
            raise RuntimeError("separation_package_unavailable") from None
        # Source deployments of this standalone service may use the checked-in
        # package. Akane's model runtime must use marketplace installation only.
        source = Path(__file__).resolve().parents[1] / "plugins/akane_audio_separation/src"
        if not (source / "akane_audio_separation/local.py").is_file():
            raise RuntimeError("separation_package_missing") from None
        sys.path.insert(0, str(source))
        from akane_audio_separation.local import LocalDemucs
    return LocalDemucs


def run_completed(factory):
    """Bridge legacy blocking routes; return only after all child work is drained.

    No background job, retry, or cancellation acknowledgement is created here.
    The synchronous HTTP contract still finishes inference before responding.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()
