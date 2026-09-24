"""Real VoiceCore queue/receipt baseline with a controlled 20 ms audio port.

The provider is synthetic; scheduling, journal, artifacts and cancellation are
production implementations. No audio is played and no user instance is opened.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.tts_service_benchmark import stats, wav_bytes
from tests.test_voice_runtime_production import (
    VoiceRuntimeProductionTests, _Adapter, _AheadOfTTSThinkingEngine,
    _open_request, _commit_realtime_turn, VOICE_PLAYBACK_OUTPUT_MODE,
)
from companion_v01.voice_runtime.tts_executor import AkaneVoiceTTSCommandExecutor


class ControlledProvider:
    provider_id = "benchmark.controlled"

    def __init__(self, *, blocked=False):
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.calls = []
        self.terminal = []

    async def synthesize(self, text):
        self.calls.append(time.perf_counter())
        self.started.set()
        if not self.release.wait(10):
            raise RuntimeError("benchmark_release_timeout")
        await asyncio.sleep(.02)
        self.terminal.append(time.perf_counter())
        return SimpleNamespace(audio=wav_bytes(), media_type="audio/wav")


def measure(*, cancel=False, segments=30, provider=None):
    harness = VoiceRuntimeProductionTests()
    provider = provider if provider is not None else ControlledProvider(blocked=cancel)
    ready = []
    scheduled = []
    dequeued = []
    original_synthesize = AkaneVoiceTTSCommandExecutor._synthesize
    original_schedule = AkaneVoiceTTSCommandExecutor._schedule

    def synthesize(executor, command, snapshot):
        dequeued.append(time.perf_counter())
        result = original_synthesize(executor, command, snapshot)
        ready.append(time.perf_counter())
        return result

    def schedule(executor, command, snapshot):
        before = time.perf_counter()
        result = original_schedule(executor, command, snapshot)
        if result.in_progress and command["command_id"] not in seen:
            seen.add(command["command_id"])
            scheduled.append(before)
        return result

    seen = set()
    with tempfile.TemporaryDirectory(prefix="akane-tts-scheduler-") as directory:
        root = Path(directory)
        manager = harness._manager(root)
        engine = _AheadOfTTSThinkingEngine(manager)
        engine.segments = tuple(f"这是第{i}句测试语音。" for i in range(4 if cancel else segments))
        service = harness._service(root=root, manager=manager, adapter=_Adapter(), engine=engine, tts_client=provider)
        try:
            with patch.object(AkaneVoiceTTSCommandExecutor, "_synthesize", synthesize), patch.object(AkaneVoiceTTSCommandExecutor, "_schedule", schedule):
                resolved = service.create_coordinator(_open_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE))
                assert resolved.status == "ready", resolved
                begin = time.perf_counter()
                asyncio.run(_commit_realtime_turn(resolved.coordinator))
                assert provider.started.wait(5)
                if cancel:
                    assert engine.stream_consumed.wait(5)
                    cancel_begin = time.perf_counter()
                    result = resolved.coordinator.cancel_response(reason="new_user_turn")
                    accepted = time.perf_counter()
                    assert result.status == "accepted", result
                    # The old implementation suppresses late observations but
                    # does not stop a blocked provider. Measure drain separately.
                    provider.release.set()
                assert service.wait_idle(timeout=20)
                terminal = time.perf_counter()
                if cancel:
                    assert len(provider.calls) == 1
                    assert resolved.delivery_channel.take_outbound() is None
                    events = resolved.coordinator.bridge.host._host.journal.load_events()
                    assert not any(event.event_kind == "voice.tts.ready" for event in events.events)
                    return {"suppression_ms": (accepted-cancel_begin)*1000,
                        "work_terminal_ms": (terminal-cancel_begin)*1000,
                        "provider_terminal_ms": (provider.terminal[0]-cancel_begin)*1000}
                assert len(ready) == len(provider.calls) == segments
                return {"first_ready_ms": (ready[0]-begin)*1000,
                    "ready_gap_ms": [(b-a)*1000 for a,b in zip(ready, ready[1:])],
                    "queue_ms": [(b-a)*1000 for a,b in zip(scheduled, dequeued)],
                    "service_dispatch_ms": [(b-a)*1000 for a,b in zip(dequeued, provider.calls)],
                    "synthesis_ms": [(b-a)*1000 for a,b in zip(provider.calls, provider.terminal)],
                    "media_ms": [(b-a)*1000 for a,b in zip(provider.terminal, ready)],
                    "provider_initializations": 1}
        finally:
            provider.release.set()
            service.close()
            manager.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    continuous = measure()
    first_segments = [measure(segments=1) for _ in range(30)]
    cancellation = [measure(cancel=True) for _ in range(30)]
    report = {"variant": "legacy", "dataset": "controlled-inprocess-20ms",
        "continuous": continuous, "first_segments": first_segments, "cancellation": cancellation,
        "summary": {"ready_gap_ms": stats(continuous["ready_gap_ms"]),
            "first_ready_ms": stats([row["first_ready_ms"] for row in first_segments]),
            **{key: stats([row[key] for row in cancellation]) for key in cancellation[0]}},
        "failures": 0, "unmeasured": ["speaker playback", "remote provider interruption"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
