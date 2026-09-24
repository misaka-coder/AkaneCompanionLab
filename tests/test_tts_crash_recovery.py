import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.tts_service_benchmark import ROOT, wav_bytes
from tests.tts_plugin_benchmark import prepare
from tests.tts_crash_probe import plugins
from companion_v01.local_capability_config import save_capability_approval_mode


class TTSCrashRecoveryTests(unittest.TestCase):
    def test_worker_loss_and_fresh_host_recovery_never_resynthesize_or_replay(self):
        calls = []
        audio = wav_bytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.assert_reference = Path(body["ref_audio_path"]).read_bytes() == audio
                calls.append(body["text"])
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
        with tempfile.TemporaryDirectory(prefix="tts-crash-") as directory:
            root = Path(directory)
            reference = root / "reference.wav"
            reference.write_bytes(audio)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                asyncio.run(prepare(root, f"http://127.0.0.1:{server.server_port}", str(reference)))
                async def install_alternate():
                    harness = await plugins(root)
                    try:
                        stage = await harness.service.stage_source(source_path=str(ROOT / "examples/plugins/akane_sdk_tts_tone"))
                        self.assertTrue(stage["ok"], stage)
                        result = await harness.service.install_stage(stage_id=stage["stage_id"], approved_permissions=stage["permissions"])
                        self.assertTrue(result["ok"], result)
                    finally:
                        await harness.close()
                asyncio.run(install_alternate())
                save_capability_approval_mode(base_dir=root, profile_user_id="benchmark",
                    capability_id="example.tts-tone.service.tts.v1.synthesize", mode="trusted_auto_allow")
                from companion_v01.extension_management import PluginSelectionStore
                selection = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="image-test")
                def bind(plugin):
                    selection.save_service_binding(service_id="tts", version=1, plugin_id=plugin,
                        expected_sha256=hashlib.sha256(selection.path.read_bytes()).hexdigest())
                expected_artifacts = 0
                for stage in ("worker_before_artifact", "provider_before_artifact", "artifact_before_association",
                              "association_before_receipt", "ready_before_handoff", "played_without_receipt"):
                    with self.subTest(stage=stage):
                        bind("akane.tts")
                        before = len(calls)
                        base = [sys.executable, "-m", "tests.tts_crash_probe", "--root", str(root), "--stage", stage]
                        crashed = subprocess.run(base, cwd=ROOT, capture_output=True, text=True, timeout=60)
                        self.assertEqual(crashed.returncode, 0 if stage == "worker_before_artifact" else 73, crashed.stderr)
                        self.assertEqual(len(calls), before + 1)
                        if stage not in {"worker_before_artifact", "provider_before_artifact"}:
                            expected_artifacts += 1
                        fault = json.loads((root / stage / "fault.json").read_text())
                        self.assertEqual(fault["stage"], stage)
                        bind("example.tts-tone")
                        observations = []
                        for attempt in range(2):
                            output = root / stage / f"recovery-{attempt}.json"
                            recovered = subprocess.run([*base, "--recover", "--output", str(output)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
                            self.assertEqual(recovered.returncode, 0, recovered.stderr)
                            observations.append(json.loads(output.read_text()))
                            self.assertEqual(len(calls), before + 1)
                        self.assertEqual(observations[0], observations[1])
                        report = observations[0]
                        self.assertEqual(report["artifacts"], expected_artifacts)
                        self.assertFalse(report["outbound"])
                        self.assertEqual(report["played"], 0)
                        if stage in {"worker_before_artifact", "provider_before_artifact", "artifact_before_association"}:
                            self.assertEqual(report["status"], "unavailable", report)
                            self.assertEqual(report["reason"], "voice_tts_outcome_unconfirmed")
                            self.assertEqual(report["ready"], [])
                        else:
                            self.assertEqual(report["status"], "ready", report)
                            self.assertEqual(len(report["ready"]), 1)
                            self.assertEqual(report["ready"][0]["provider_id"], "akane.tts")
                            self.assertEqual(report["ready"][0]["audio_artifact_ref"], fault["audio_artifact_ref"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
