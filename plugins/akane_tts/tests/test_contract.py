import asyncio
import builtins
import io
import unittest
import wave
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from akane_plugin import CapabilityResult, InvocationContext
from akane_tts.plugin import TTSAdapter, CAPABILITY_ID, descriptor, inspect_audio


class ContractTests(unittest.TestCase):
    def test_health_does_not_import_the_edge_backend(self):
        original_import = builtins.__import__
        def check_import(name, *args, **kwargs):
            if name == "edge_tts" or name.startswith("edge_tts."):
                raise AssertionError("health initialized an unused Edge backend")
            return original_import(name, *args, **kwargs)
        adapter = TTSAdapter(None, None)
        with patch("builtins.__import__", check_import):
            health = asyncio.run(adapter.health())
        self.assertTrue(health.ok, health)
        self.assertEqual(adapter.clients, {})

    def test_health_reports_missing_edge_without_initializing_clients(self):
        adapter = TTSAdapter(None, None)
        with patch("akane_tts.plugin.importlib.util.find_spec", return_value=None):
            missing = asyncio.run(adapter.health())
        self.assertFalse(missing.ok)
        self.assertEqual(missing.reason, "edge_tts_not_installed")
        self.assertEqual(adapter.clients, {})

    def test_client_reuse_and_idle_rotation_do_not_exhaust_worker(self):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as output:
            output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 240)
        audio = stream.getvalue()
        created, closed = [], []
        class Client:
            def __init__(self, endpoint, **options):
                created.append(self)
            async def synthesize(self, text, **options):
                return SimpleNamespace(audio=audio, media_type="audio/wav")
            async def aclose(self):
                closed.append(self)
        async def run(root):
            class Ports:
                revision = 0
                async def resolve(self, name):
                    return SimpleNamespace(ok=True, model="provider.tts.gpt_sovits.local", base_url="http://fixture",
                        options={"client": {"speed_factor": self.revision}, "profile": {"refAudioHandle": "reference",
                            "promptText": "test", "promptLang": "zh"}})
                async def open(self, handle):
                    return SimpleNamespace(ok=True, path=root / "reference.wav", file_size=len(audio))
                async def work_directory(self):
                    return root
            ports = Ports()
            adapter = TTSAdapter(ports, ports)
            args = {"text": "test", "voice": {"provider": "provider.tts.gpt_sovits.local", "profile_id": "test"}}
            for _ in range(30):
                result = await adapter.invoke(CAPABILITY_ID, args, InvocationContext("owner", "session", "web"))
                self.assertFalse(result.is_error, result)
            self.assertEqual(len(created), 1)
            for revision in range(1, 13):
                ports.revision = revision
                result = await adapter.invoke(CAPABILITY_ID, args, InvocationContext("owner", "session", "web"))
                self.assertFalse(result.is_error, result)
            self.assertEqual(len(created), 13)
            self.assertEqual(len(adapter.clients), 8)
            self.assertEqual(len(closed), 5)
            await adapter.aclose()
            self.assertEqual(len(closed), 13)
        with tempfile.TemporaryDirectory() as directory, patch("akane_tts.plugin.GptSovitsTTSClient", Client):
            root = Path(directory)
            (root / "reference.wav").write_bytes(audio)
            asyncio.run(run(root))

    def test_service_is_not_a_model_tool(self):
        declared = descriptor()
        self.assertFalse(declared.prompt_exposed)
        self.assertEqual(declared.raw["service"], {"service_id": "tts", "version": 1, "method": "synthesize"})
        self.assertEqual(declared.outputs[0].delivery, "generated_file")

    def test_audio_is_decodable_and_truncated_audio_is_rejected(self):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as output:
            output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x10\x00" * 240)
        data = stream.getvalue()
        self.assertEqual(inspect_audio(data, "audio/wav"), ("wav", "audio/wav"))
        with self.assertRaises(ValueError):
            inspect_audio(data[:-20], "audio/wav")

    def test_unsupported_voice_does_not_resolve_a_connection(self):
        class Connections:
            async def resolve(self, name):
                raise AssertionError("unsupported voice must not access configuration")
        result = asyncio.run(TTSAdapter(None, Connections()).invoke(CAPABILITY_ID,
            {"text": "test", "voice": {"provider": "example.unsupported", "profile_id": ""}},
            InvocationContext("owner", "session", "web")))
        self.assertIsInstance(result, CapabilityResult)
        self.assertEqual(result.reason, "requested_provider_unknown")


if __name__ == "__main__":
    unittest.main()
