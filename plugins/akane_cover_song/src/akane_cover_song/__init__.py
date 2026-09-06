"""Independent cover-song business runtime (no host or plugin SDK imports)."""

from .errors import CoverSongError
from .rvc import RvcWebUiProvider

__all__ = ["CoverSongError", "RvcWebUiProvider"]
