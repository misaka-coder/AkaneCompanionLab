"""Controlled TTS transport baseline; never contacts the user's TTS instance.

Run: python -m tests.tts_service_benchmark --output work/tts-baseline.json
Cold samples use independent interpreters. This measures synthesis-to-durable
audio readiness, not speaker playback, VoiceCore scheduling or model warm-up.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import platform
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = "今天想聊点什么，我在这里陪着你。"


def wav_bytes() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x10\x00\xf0\xff" * 2400)
    return stream.getvalue()


async def sample(endpoint: str, reference: str, count: int) -> dict:
    from services.tts_client import GptSovitsTTSClient
    from companion_v01.tts_provider_runtime import synthesize_tts_resolution
    from companion_v01.tts_provider_selection import GPT_SOVITS_PROVIDER_ID
    from companion_v01.voice_runtime.durable_ports import FileVoiceAudioArtifactPort

    started = time.perf_counter()
    client = GptSovitsTTSClient(endpoint)
    init_ms = (time.perf_counter() - started) * 1000
    resolution = {
        "activeProviderId": GPT_SOVITS_PROVIDER_ID, "client": client,
        "voiceProfileId": "benchmark", "profileUserId": "benchmark",
        "voiceProfile": {"refAudioPath": reference, "promptText": TEXT, "promptLang": "zh"},
    }
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix="akane-tts-benchmark-") as directory:
            port = FileVoiceAudioArtifactPort(state_dir=Path(directory), conversation_id="benchmark", conversation_generation=1)
            for index in range(count):
                begin = time.perf_counter()
                result = await synthesize_tts_resolution(resolution=resolution, text=TEXT,
                    payload={"session_id": "benchmark", "emotion": "neutral"}, default_media_type="audio/wav")
                synthesized = time.perf_counter()
                with wave.open(io.BytesIO(result.audio), "rb") as audio:
                    assert audio.getnframes() == 4800 and audio.getframerate() == 24000
                stored = port.put_audio(artifact_key=f"segment-{index}", audio=result.audio, media_type=result.media_type)
                assert stored.ok, stored.reason
                assert port.read_audio(stored.artifact_ref).audio == result.audio
                ready = time.perf_counter()
                rows.append({"synthesis_ms": (synthesized-begin)*1000,
                    "media_ms": (ready-synthesized)*1000, "ready_ms": (ready-begin)*1000})
    finally:
        await client.aclose()
    return {"client_initializations": 1, "init_ms": init_ms, "rows": rows,
        "ready_clock": ready}


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"count": len(values), "p50_ms": ordered[math.ceil(len(values)*.5)-1],
        "p95_ms": ordered[math.ceil(len(values)*.95)-1], "max_ms": ordered[-1]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--reference")
    args = parser.parse_args()
    if args.child:
        print(json.dumps(asyncio.run(sample(args.endpoint, args.reference, 1))))
        return
    assert args.output, "--output is required"
    audio = wav_bytes()
    requests_seen = []
    with tempfile.TemporaryDirectory(prefix="akane-tts-http-") as directory:
        reference = Path(directory) / "reference.wav"
        reference.write_bytes(audio)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if (self.path != "/tts" or request.get("text") != TEXT
                        or request.get("ref_audio_path") != str(reference)
                        or Path(request["ref_audio_path"]).read_bytes() != audio):
                    self.send_error(400)
                    return
                requests_seen.append(time.perf_counter())
                time.sleep(.02)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            cold = []
            for _ in range(10):
                start = time.perf_counter()
                child = subprocess.run([sys.executable, "-m", "tests.tts_service_benchmark", "--child",
                    "--endpoint", endpoint, "--reference", str(reference)], cwd=ROOT,
                    capture_output=True, text=True, timeout=60)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                measured = json.loads(child.stdout)
                cold.append({"process_to_exit_ms": (time.perf_counter()-start)*1000,
                    "process_to_ready_ms": (measured.pop("ready_clock")-start)*1000, **measured})
            hot = asyncio.run(sample(endpoint, str(reference), 30))
            hot.pop("ready_clock")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    source_files = ["companion_v01/tts_provider_runtime.py", "services/tts_client.py",
        "companion_v01/voice_runtime/durable_ports.py", "tests/tts_service_benchmark.py"]
    result = {"schema_version": 1, "variant": "legacy", "dataset": "controlled-loopback-20ms",
        "python": platform.python_version(), "platform": platform.platform(),
        "processor": platform.processor(), "concurrency": 1, "http_requests": len(requests_seen),
        "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_files},
        "cold": cold, "hot": hot, "failures": 0,
        "summary": {"cold_process": stats([row["process_to_ready_ms"] for row in cold]),
            **{name: stats([row[name] for row in hot["rows"]]) for name in ("synthesis_ms", "media_ms", "ready_ms")}},
        "unmeasured": ["VoiceCore queue", "cancellation", "speaker gap", "remote model cold start"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
