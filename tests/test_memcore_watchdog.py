from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "watchdog"
    / "akane-host-memcore-watchdog.py"
)
SPEC = importlib.util.spec_from_file_location("akane_host_memcore_watchdog", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
WATCHDOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCHDOG)


def _line(epoch: int, status: str, namespace: str, raw_tokens: int) -> str:
    return (
        f"{epoch}.0 host akane-host[1]: memcore_compaction "
        f"status={status} namespace={namespace} profile=openai_chat reason=test "
        f"before_tokens={raw_tokens} after_tokens={raw_tokens} "
        f"raw_before_tokens={raw_tokens} raw_after_tokens={raw_tokens}"
    )


class MemCoreWatchdogTests(unittest.TestCase):
    def test_repeated_high_raw_failures_are_detected(self) -> None:
        incidents = WATCHDOG.find_stuck_namespaces(
            [
                _line(10, "failed", "finance", 100_000),
                _line(11, "blocked_by_open_turn", "finance", 120_000),
                _line(12, "failed", "finance", 140_000),
            ],
            minimum_raw_tokens=96_000,
        )

        self.assertEqual(
            incidents["finance"],
            {"count": 3, "max_raw_tokens": 140_000},
        )

    def test_success_after_failures_clears_the_incident(self) -> None:
        incidents = WATCHDOG.find_stuck_namespaces(
            [
                _line(10, "failed", "finance", 100_000),
                _line(11, "blocked_by_open_turn", "finance", 120_000),
                _line(12, "compacted", "finance", 120_000),
            ],
            minimum_raw_tokens=96_000,
        )

        self.assertEqual(incidents, {})

    def test_low_raw_failures_and_pre_restart_events_are_ignored(self) -> None:
        incidents = WATCHDOG.find_stuck_namespaces(
            [
                _line(10, "failed", "finance", 95_999),
                _line(11, "failed", "finance", 120_000),
            ],
            minimum_raw_tokens=96_000,
            after_epoch=11.0,
        )

        self.assertEqual(incidents, {})


if __name__ == "__main__":
    unittest.main()
