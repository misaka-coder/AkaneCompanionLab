"""Standalone service binding; never activates an uninstalled model tool."""

from pathlib import Path
import sys


def cover_business():
    try:
        import akane_cover_song
    except ModuleNotFoundError as exc:
        if exc.name != "akane_cover_song":
            raise RuntimeError("cover_package_unavailable") from None
        source = Path(__file__).resolve().parents[1] / "plugins/akane_cover_song/src"
        if not (source / "akane_cover_song/__init__.py").is_file():
            raise RuntimeError("cover_package_missing") from None
        sys.path.insert(0, str(source))
        import akane_cover_song
    return akane_cover_song
