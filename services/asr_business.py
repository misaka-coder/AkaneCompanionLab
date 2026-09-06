"""Bundled ASR library binding; never activates an optional model-facing tool.

Source releases ship this library for the existing realtime voice API and local
media service. Installing/enabling the file transcription plugin is a separate
operation; its discovery still comes exclusively from the plugin runtime.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


def module(name):
    if name not in ("local", "compatibility"):
        raise ValueError("asr_business_module_invalid")
    try:
        return importlib.import_module("akane_file_transcription." + name)
    except ModuleNotFoundError as exc:
        if exc.name != "akane_file_transcription":
            raise RuntimeError("asr_business_unavailable") from None
    root = Path(__file__).resolve().parents[1] / "plugins/akane_file_transcription/src"
    if not (root / "akane_file_transcription" / (name + ".py")).is_file():
        raise RuntimeError("asr_business_missing")
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module("akane_file_transcription." + name)
    except ImportError:
        raise RuntimeError("asr_business_unavailable") from None
