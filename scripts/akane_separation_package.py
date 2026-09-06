"""Standalone service/CLI binding, never a model-tool activation fallback."""

from __future__ import annotations

from pathlib import Path
import sys

from companion_v01.plugin_subprocess import run_completed  # noqa: F401


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
