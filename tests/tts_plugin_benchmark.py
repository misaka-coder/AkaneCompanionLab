"""Installed-service counterpart of tts_service_benchmark, same loopback work."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from tests.tts_service_benchmark import TEXT, ROOT, stats, wav_bytes


async def sample(root, count):
    from tests.image_plugin_harness import ImageHarness
    from companion_v01.plugin_connections import ModelServicePluginConnectionProvider
    from companion_v01.tts_provider_runtime import ResolvedTTSClient
    from companion_v01.voice_runtime.durable_ports import FileVoiceAudioArtifactPort

    source = SimpleNamespace(capability_config_base_dir=root, settings=None)
    config = SimpleNamespace(WEB_OWNER_PROFILE_USER_ID="benchmark")
    harness = ImageHarness(root, ModelServicePluginConnectionProvider(source, config))
    harness.plugin_id = "akane.tts"
    harness.engine_factory = lambda bridge: SimpleNamespace(plugin_capability_source=bridge, tool_handlers={})
    harness.market_index = root / "market" / "index.json"
    begin = time.perf_counter()
    await harness.start()
    init_ms = (time.perf_counter()-begin)*1000
    harness.engine.desktop_pet_character_resources = SimpleNamespace(
        build_character_voice_preference=lambda _: {"provider": "gpt_sovits", "profileId": "benchmark"})
    client = ResolvedTTSClient(harness.engine, "benchmark", "benchmark", "benchmark")
    rows = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            port = FileVoiceAudioArtifactPort(state_dir=Path(directory), conversation_id="benchmark", conversation_generation=1)
            for index in range(count):
                started = time.perf_counter()
                result = await client.synthesize(TEXT)
                synthesized = time.perf_counter()
                assert result.audio == wav_bytes()
                stored = port.put_audio(artifact_key=f"segment-{index}", audio=result.audio,
                    media_type=result.media_type, origin=result.origin)
                assert stored.ok, stored.reason
                assert port.read_audio(stored.artifact_ref).audio == result.audio
                ready = time.perf_counter()
                rows.append({"synthesis_ms": (synthesized-started)*1000,
                    "media_ms": (ready-synthesized)*1000, "ready_ms": (ready-started)*1000})
    finally:
        await harness.close()
    return {"worker_initializations": 1, "init_ms": init_ms, "rows": rows, "ready_clock": ready}


async def prepare(root, endpoint, reference):
    from tests.tts_plugin_harness import tts_harness
    from companion_v01.local_capability_config import save_provider_config, save_voice_profile_config, save_capability_approval_mode
    source = SimpleNamespace(capability_config_base_dir=root, settings=None)
    save_provider_config(base_dir=root, profile_user_id="benchmark", provider_id="provider.tts.gpt_sovits.local",
        payload={"enabled": True, "endpoint": endpoint})
    save_voice_profile_config(base_dir=root, profile_user_id="benchmark", voice_profile_id="benchmark",
        payload={"enabled": True, "refAudioPath": reference, "promptText": TEXT, "promptLang": "zh"})
    save_capability_approval_mode(base_dir=root, profile_user_id="benchmark",
        capability_id="akane.tts.service.tts.v1.synthesize", mode="trusted_auto_allow")
    harness = tts_harness(root, source, SimpleNamespace())
    await harness.start()
    try:
        await harness.install()
    finally:
        await harness.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child-root", type=Path)
    args = parser.parse_args()
    if args.child_root:
        print(json.dumps(asyncio.run(sample(args.child_root, 1))))
        return
    audio = wav_bytes()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/tts" and request["text"] == TEXT
            assert Path(request["ref_audio_path"]).read_bytes() == audio
            calls.append(time.perf_counter())
            time.sleep(.02)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
    with tempfile.TemporaryDirectory(prefix="tts-plugin-benchmark-") as directory:
        root = Path(directory)
        reference = root / "reference.wav"
        reference.write_bytes(audio)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(prepare(root, f"http://127.0.0.1:{server.server_port}", str(reference)))
            cold = []
            for _ in range(10):
                started = time.perf_counter()
                child = subprocess.run([sys.executable, "-m", "tests.tts_plugin_benchmark", "--child-root", str(root)],
                    cwd=ROOT, capture_output=True, text=True, timeout=60)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                measured = json.loads(child.stdout)
                cold.append({"process_to_exit_ms": (time.perf_counter()-started)*1000,
                    "process_to_ready_ms": (measured.pop("ready_clock")-started)*1000, **measured})
            hot = asyncio.run(sample(root, 31))
            hot.pop("ready_clock")
            hot["warmup"] = hot["rows"].pop(0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
    result = {"schema_version": 1, "variant": "installed-plugin", "dataset": "controlled-loopback-20ms",
        "python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
        "concurrency": 1, "http_requests": len(calls), "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "cold": cold, "hot": hot, "failures": 0,
        "summary": {"cold_process": stats([row["process_to_ready_ms"] for row in cold]),
            **{name: stats([row[name] for row in hot["rows"]]) for name in ("synthesis_ms", "media_ms", "ready_ms")}},
        "unmeasured": ["VoiceCore queue", "cancellation", "speaker gap", "remote model cold start"],
        "preparation": "Formal install outside timer; cold includes imports, existing selection read, worker and first audio"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
