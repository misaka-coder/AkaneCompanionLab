"""Identical VoiceCore timing boundaries, with the installed public TTS service."""
import argparse
import asyncio
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from tests.tts_scheduler_benchmark import measure
from tests.tts_service_benchmark import stats, wav_bytes
from tests.tts_plugin_harness import ThreadedTTSHarness
from companion_v01.local_capability_config import save_provider_config, save_voice_profile_config, save_capability_approval_mode
from companion_v01.tts_provider_runtime import ResolvedTTSClient


class ObservedProvider:
    def __init__(self, client, *, blocked=False):
        self.client = client
        self.started, self.release = threading.Event(), threading.Event()
        if not blocked:
            self.release.set()
        self.calls, self.terminal = [], []

    async def synthesize(self, text):
        return await self.synthesize_command(text)

    async def synthesize_command(self, text, **context):
        try:
            return await self.client.synthesize_command(text, **context)
        finally:
            self.terminal.append(time.perf_counter())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    active = {}
    audio = wav_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert Path(request["ref_audio_path"]).read_bytes() == audio
            provider = active["provider"]
            provider.calls.append(time.perf_counter())
            provider.started.set()
            assert provider.release.wait(10)
            time.sleep(.02)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
    with tempfile.TemporaryDirectory(prefix="tts-plugin-scheduler-") as directory:
        root = Path(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        reference = root / "reference.wav"
        reference.write_bytes(audio)
        source = SimpleNamespace(capability_config_base_dir=root, settings=None)
        save_provider_config(base_dir=root, profile_user_id="profile-user", provider_id="provider.tts.gpt_sovits.local",
            payload={"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}"})
        save_voice_profile_config(base_dir=root, profile_user_id="profile-user", voice_profile_id="benchmark",
            payload={"enabled": True, "refAudioPath": str(reference), "promptText": "测试参考文本", "promptLang": "zh"})
        save_capability_approval_mode(base_dir=root, profile_user_id="profile-user",
            capability_id="akane.tts.service.tts.v1.synthesize", mode="trusted_auto_allow")
        installed = None
        try:
            installed = ThreadedTTSHarness(root, source, SimpleNamespace())
            installed.harness.engine.desktop_pet_character_resources = SimpleNamespace(
                build_character_voice_preference=lambda _: {"provider": "gpt_sovits", "profileId": "benchmark"})
            client = ResolvedTTSClient(installed.harness.engine, "profile-user", "session-visible-id", "character-pack")
            active["provider"] = ObservedProvider(client)
            installed.run(active["provider"].synthesize("预热"))
            def run(**options):
                active["provider"] = ObservedProvider(client, blocked=options.get("cancel", False))
                return measure(provider=active["provider"], **options)
            continuous = run()
            first = [run(segments=1) for _ in range(30)]
            cancelled = [run(cancel=True) for _ in range(30)]
            report = {"variant": "installed-plugin", "dataset": "controlled-loopback-20ms",
                "continuous": continuous, "first_segments": first, "cancellation": cancelled,
                "summary": {"ready_gap_ms": stats(continuous["ready_gap_ms"]),
                    "first_ready_ms": stats([row["first_ready_ms"] for row in first]),
                    **{key: stats([row[key] for row in cancelled]) for key in cancelled[0]}},
                "failures": 0, "unmeasured": ["speaker playback", "remote provider interruption"],
                "comparison": "Same VoiceCore, queue, text and 20ms provider delay; public service now crosses HTTP/worker"}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(report["summary"], indent=2))
        finally:
            if active:
                active["provider"].release.set()
            if installed:
                installed.close()
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    main()
