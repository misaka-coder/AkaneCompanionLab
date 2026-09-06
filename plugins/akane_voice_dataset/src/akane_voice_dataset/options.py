"""One authority for voice material presets and bounded input options."""

from __future__ import annotations

from dataclasses import dataclass
import math


PRESETS = {
    "gpt_sovits": (44100, 3.0, 12.0, -40.0, 300, 300),
    "rvc": (40000, 3.0, 15.0, -40.0, 300, 250),
    "archive": (44100, 2.0, 30.0, -45.0, 450, 500),
}
MAX_PCM_BYTES = 512 * 1024 * 1024
MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_DURATION = 1800
MAX_SLICES = 10000


class DatasetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Options:
    profile: str = "gpt_sovits"
    target_sr: int | None = None
    mono: bool = True
    min_clip_seconds: float | None = None
    max_clip_seconds: float | None = None
    silence_threshold_db: float | None = None
    min_silence_ms: int | None = None
    max_silence_kept_ms: int | None = None
    clean_first: bool = False
    normalize_volume: bool = False

    def __post_init__(self):
        if not isinstance(self.profile, str) or self.profile not in PRESETS:
            raise DatasetError("dataset_profile_invalid")
        for name in ("mono", "clean_first", "normalize_volume"):
            if not isinstance(getattr(self, name), bool):
                raise DatasetError("dataset_boolean_invalid")
        names = (
            ("target_sr", 8000, 96000, int),
            ("min_clip_seconds", 0.5, 60.0, float),
            ("max_clip_seconds", 1.0, 120.0, float),
            ("silence_threshold_db", -80.0, -10.0, float),
            ("min_silence_ms", 80, 3000, int),
            ("max_silence_kept_ms", 0, 2000, int),
        )
        for (name, low, high, kind), default in zip(names, PRESETS[self.profile]):
            value = getattr(self, name)
            value = default if value is None else value
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
                or (kind is int and int(value) != value)
            ):
                raise DatasetError("dataset_option_invalid")
            object.__setattr__(self, name, kind(value))
        if self.max_clip_seconds < self.min_clip_seconds:
            raise DatasetError("dataset_clip_range_invalid")
