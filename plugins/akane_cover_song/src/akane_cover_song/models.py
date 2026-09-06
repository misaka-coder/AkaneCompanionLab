"""Shared voice-name matching for online inference and offline recordings."""

from pathlib import Path
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit


def endpoint_namespace(base_url, root_dir=""):
    parsed = urlsplit(base_url)
    # Normalize loopback aliases without exposing the endpoint or model root.
    identity = (
        parsed.scheme,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        parsed.path.rstrip("/"),
        str(root_dir or ""),
    )
    return hashlib.sha256(repr(identity).encode()).hexdigest()


def normalize_model_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def matching_models(models, requested):
    names = list(dict.fromkeys(models))
    lowered = requested.lower()
    exact = [name for name in names if name.lower() == lowered or Path(name).stem.lower() == Path(lowered).stem.lower()]
    normalized = normalize_model_key(requested)
    return exact or [name for name in names if normalized and normalized in normalize_model_key(name)]


def safe_model_fingerprint(path: Path, *, indices: list[Path] | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"name": path.name, "missing": True}
    index_cards = []
    for index_path in list(indices or [])[:12]:
        try:
            index_stat = index_path.stat()
        except OSError:
            continue
        index_cards.append(
            {
                "name": index_path.name,
                "size": int(index_stat.st_size),
                "mtime_ns": int(index_stat.st_mtime_ns),
            }
        )
    return {
        "name": path.name,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256_hint": hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16],
        "indices": index_cards,
    }
