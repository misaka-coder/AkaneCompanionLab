"""Cold audio storage use must not initialize unrelated voice clients."""
import subprocess
import sys
import unittest
from pathlib import Path


class VoiceRuntimeImportTests(unittest.TestCase):
    def test_generation_codec_does_not_initialize_unrelated_channel_authority(self):
        code = """
import sys
import companion_v01.plugin_generation_codec as codec
assert 'channelcore_onebot' not in sys.modules
from companion_v01.plugin_generation_codec import event_payload_to_wire
from companion_v01.plugin_generation_event_payload import event_payload_to_wire as Original
assert event_payload_to_wire is Original
assert codec.event_payload_from_wire(event_payload_to_wire({'text': 'same payload'})) == {'text': 'same payload'}
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_service_contracts_do_not_load_model_tables_and_broker_alias_is_shared(self):
        code = """
import sys
from companion_v01.tool_handlers.core import ToolExecutionContext, BaseToolHandler
from companion_v01.executor_broker import ExecutorBroker
assert 'companion_v01.tool_handlers.builtin_tables' not in sys.modules
assert 'companion_v01.capability_registry' not in sys.modules
assert 'memcore' not in sys.modules
from companion_v01.capability_registry import ExecutorBroker as OriginalImport
assert OriginalImport is ExecutorBroker
from companion_v01.tool_handlers.core import TOOL_SPEC_BY_TYPE, TOOL_METADATA_BY_TYPE
handler = BaseToolHandler()
handler.tool_type = 'web_search'
assert handler.tool_spec() is TOOL_SPEC_BY_TYPE['web_search']
assert handler.tool_metadata() is TOOL_METADATA_BY_TYPE['web_search']
from companion_v01.tool_handlers.builtin_tables import TOOL_SPEC_BY_TYPE as Tables
assert TOOL_SPEC_BY_TYPE is Tables
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_audio_port_import_keeps_clients_cold_and_public_aliases_identical(self):
        code = """
import sys
from companion_v01.voice_runtime import FileVoiceAudioArtifactPort
from companion_v01.voice_runtime.durable_ports import FileVoiceAudioArtifactPort as Direct
assert FileVoiceAudioArtifactPort is Direct
for name in ('asr_provider', 'realtime_transport', 'thinking_agent', 'production'):
    assert 'companion_v01.voice_runtime.' + name not in sys.modules, name
import companion_v01.voice_runtime as voice
assert 'AkaneVoiceRuntimeService' in dir(voice)
from companion_v01.voice_runtime import AkaneVoiceRuntimeService
from companion_v01.voice_runtime.production import AkaneVoiceRuntimeService as Production
assert AkaneVoiceRuntimeService is Production
assert voice.AkaneVoiceRuntimeService is Production
try:
    voice.not_a_voice_port
except AttributeError:
    pass
else:
    raise AssertionError('unknown export accepted')
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
