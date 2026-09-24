"""Install the real first-party TTS service for consumer and performance tests."""
import asyncio
import json
import shutil
import threading
import tomllib
import tempfile
import subprocess
import sys
import site
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from tests.image_plugin_harness import ImageHarness, ROOT
from tests.dependency_supply_fixture import wheelhouse_for_plugins
from companion_v01.plugin_connections import ModelServicePluginConnectionProvider


def verify_without_host(harness, root):
    """Import and run installed plugin tests in -I without an Akane host path."""
    import akane_plugin
    import capcore
    sdk = root / "public-sdk-only"
    sdk.mkdir()
    for module in (akane_plugin, capcore):
        shutil.copytree(Path(module.__file__).parent, sdk / module.__name__,
            ignore=shutil.ignore_patterns("__pycache__"))
    for plugin_id, source in (("akane.tts", ROOT / "plugins/akane_tts"),
                              ("example.tts-tone", ROOT / "examples/plugins/akane_sdk_tts_tone")):
        installed = harness.artifacts.resolve_generation_source(plugin_id)
        tests = sdk / plugin_id / "tests"
        shutil.copytree(source / "tests", tests)
        code = """
import sys, importlib.util, unittest
sys.path[:0] = sys.argv[1:4]
assert importlib.util.find_spec('companion_v01') is None
suite = unittest.TestLoader().discover(sys.argv[4])
result = unittest.TextTestRunner().run(suite)
assert importlib.util.find_spec('companion_v01') is None
assert not any(name.startswith('companion_v01') for name in sys.modules)
raise SystemExit(not result.wasSuccessful())
"""
        checked = subprocess.run([sys.executable, "-I", "-c", code, str(sdk), str(installed.site_dir),
            site.getusersitepackages(), str(tests)], cwd=sdk, capture_output=True, text=True, timeout=30)
        if checked.returncode:
            raise AssertionError(checked.stderr)


def tts_harness(root, engine, config):
    harness = ImageHarness(root, ModelServicePluginConnectionProvider(engine, config),
                           wheelhouse_for_plugins("akane_tts"))
    harness.plugin_id = "akane.tts"
    harness.engine_factory = lambda source: SimpleNamespace(plugin_capability_source=source, tool_handlers={})
    entries = tomllib.loads((ROOT / "plugins/market.toml").read_text(encoding="utf-8"))["plugins"]
    entry = dict(next(item for item in entries if item["plugin_id"] == harness.plugin_id))
    entry["source"] = "plugin-source"
    shutil.copytree(ROOT / "plugins/akane_tts", root / "plugin-source",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build"))
    harness.market_manifest = root / "tts-market.toml"
    harness.market_manifest.write_text("schema_version = 1\n[[plugins]]\n" + "\n".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in entry.items()), encoding="utf-8")
    return harness


class ThreadedTTSHarness:
    def __init__(self, root, engine, config):
        self.harness = tts_harness(root, engine, config)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        async def start():
            await self.harness.start()
            await self.harness.install()
        self.run(start())

    def run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(90)

    def close(self):
        try:
            self.run(self.harness.close())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.loop.close()


@contextmanager
def configured_tts_fixture():
    """Real worker, scoped reference files, deterministic loopback speech server."""
    from tests.tts_service_benchmark import wav_bytes
    from companion_v01.local_capability_config import (
        save_provider_config, save_voice_profile_config, save_capability_approval_mode,
    )
    calls = []
    audio = wav_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            request["reference_bytes"] = Path(request["ref_audio_path"]).read_bytes()
            calls.append(request)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = SimpleNamespace(DATA_DIR=root / "bot", WEB_OWNER_PROFILE_USER_ID="master")
        source = SimpleNamespace(capability_config_base_dir=config.DATA_DIR / "users_data", settings=None)
        source.desktop_pet_character_resources = SimpleNamespace(
            build_character_voice_preference=lambda _pack: {"provider": "gpt_sovits", "profileId": "dania"})
        for name in ("neutral", "happy"):
            (root / (name + ".wav")).write_bytes(audio)
        save_provider_config(base_dir=source.capability_config_base_dir, profile_user_id="master",
            provider_id="provider.tts.gpt_sovits.local",
            payload={"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}"})
        save_voice_profile_config(base_dir=source.capability_config_base_dir, profile_user_id="master",
            voice_profile_id="dania", payload={"enabled": True, "refAudioPath": str(root / "neutral.wav"),
                "promptText": "中性参考文本", "emotionVoiceMap": {"happy": {"aliases": ["开心"],
                    "refAudioPath": str(root / "happy.wav"), "promptText": "开心参考文本"}}})
        installed = None
        try:
            installed = ThreadedTTSHarness(root, source, config)
            engine = installed.harness.engine
            engine.desktop_pet_character_resources = source.desktop_pet_character_resources
            # Admission lives in the harness config root; voice configuration
            # lives in the Bot-specific users_data directory projected by source.
            save_capability_approval_mode(base_dir=root, profile_user_id="qq_group_shared_123456",
                capability_id="akane.tts.service.tts.v1.synthesize", mode="trusted_auto_allow")
            yield SimpleNamespace(root=root, engine=engine, config=config, calls=calls, audio=audio, installed=installed)
        finally:
            if installed:
                installed.close()
            server.shutdown()
            server.server_close()
            thread.join(3)
