"""Shared voice-name matching for online inference and offline recordings."""

from pathlib import Path
import re


def normalize_model_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def matching_models(models, requested):
    names = list(dict.fromkeys(models))
    lowered = requested.lower()
    exact = [name for name in names if name.lower() == lowered or Path(name).stem.lower() == Path(lowered).stem.lower()]
    normalized = normalize_model_key(requested)
    return exact or [name for name in names if normalized and normalized in normalize_model_key(name)]
