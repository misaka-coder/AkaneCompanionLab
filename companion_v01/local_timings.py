"""Small request-local stage measurements; no payloads or persistent state."""

from contextlib import contextmanager
import hashlib
from time import perf_counter


def timing_scope(profile_user_id: str, session_id: str, character_pack_id: str = "") -> str:
    """Match preparation stages without logging account or storage details."""
    return hashlib.sha256("\0".join((profile_user_id, session_id, character_pack_id)).encode()).hexdigest()[:12]


class LocalTimings:
    def __init__(self):
        self.started = self.last = perf_counter()
        self.values: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        now = perf_counter()
        self.values[stage] = self.values.get(stage, 0.0) + (now - self.last) * 1000
        self.last = now

    @contextmanager
    def measure(self, stage: str):
        start = perf_counter()
        try:
            yield
        finally:
            self.values[stage] = self.values.get(stage, 0.0) + (perf_counter() - start) * 1000

    def snapshot(self) -> dict[str, float]:
        return {**{key: round(value, 1) for key, value in self.values.items()},
                "total_ms": round((perf_counter() - self.started) * 1000, 1)}
