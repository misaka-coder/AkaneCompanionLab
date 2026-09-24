import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave

from tts_tone import plugin, synthesize


class ToneTests(unittest.TestCase):
    def test_real_wav_and_no_inherited_connection_permission(self):
        self.assertNotIn("capability.prompt.invoke", plugin.manifest.permissions)
        self.assertFalse(any(permission.startswith("connection.") for permission in plugin.manifest.permissions))
        with tempfile.TemporaryDirectory() as directory:
            async def work_directory():
                return Path(directory)
            result = asyncio.run(synthesize("test", {"provider": "example.voice", "profile_id": ""}, "happy",
                SimpleNamespace(resources=SimpleNamespace(work_directory=work_directory))))
            self.assertFalse(result.is_error)
            with wave.open(str(result.content.artifacts[0].path), "rb") as audio:
                self.assertEqual(audio.getnframes(), 4800)
                self.assertEqual(audio.getframerate(), 24000)
