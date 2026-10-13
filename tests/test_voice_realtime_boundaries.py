"""Keep realtime wire types independent from transport and production clients."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VoiceRealtimeBoundaryTests(unittest.TestCase):
    def test_wire_types_are_lazy_and_keep_public_aliases_identical(self):
        code = """
import sys
from companion_v01.voice_runtime import VoiceRealtimeOpenRequest, VoiceRealtimeCoordinatorResolution
from companion_v01.voice_runtime.realtime_protocol import VoiceRealtimeOpenRequest as Direct
assert VoiceRealtimeOpenRequest is Direct
for name in ('realtime_transport', 'production', 'asr_provider'):
    assert 'companion_v01.voice_runtime.' + name not in sys.modules, name
from companion_v01.voice_runtime.realtime_transport import VoiceRealtimeOpenRequest as Legacy
assert Legacy is Direct
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)

    def test_session_and_dispatch_exports_use_their_implementations_directly(self):
        code = """
import sys
from companion_v01.voice_runtime import (
    VoiceRealtimeWebSocketSession, VoiceRealtimeCallWebSocketSession, handle_voice_realtime_websocket,
)
from companion_v01.voice_runtime.realtime_turn import VoiceRealtimeWebSocketSession as Turn
from companion_v01.voice_runtime.realtime_call import VoiceRealtimeCallWebSocketSession as Call
from companion_v01.voice_runtime.realtime_dispatch import handle_voice_realtime_websocket as Dispatch
assert VoiceRealtimeWebSocketSession is Turn
assert VoiceRealtimeCallWebSocketSession is Call
assert handle_voice_realtime_websocket is Dispatch
for name in ('realtime_transport', 'production', 'asr_provider'):
    assert 'companion_v01.voice_runtime.' + name not in sys.modules, name
from companion_v01.voice_runtime import realtime_transport as legacy
assert legacy.VoiceRealtimeWebSocketSession is Turn
assert legacy.VoiceRealtimeCallWebSocketSession is Call
assert legacy.handle_voice_realtime_websocket is Dispatch
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)

    def test_compatibility_entrypoint_has_no_implementation_or_reverse_dependencies(self):
        runtime = ROOT / "companion_v01" / "voice_runtime"
        facade = ast.parse((runtime / "realtime_transport.py").read_text(encoding="utf-8"))
        self.assertTrue(all(isinstance(node, (ast.Expr, ast.ImportFrom)) for node in facade.body))
        for path in runtime.glob("realtime_*.py"):
            if path.name == "realtime_transport.py":
                continue
            with self.subTest(module=path.stem):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotEqual((node.module or "").split(".")[-1], "realtime_transport")
                    elif isinstance(node, ast.Import):
                        self.assertFalse(any(alias.name.endswith(".realtime_transport") for alias in node.names))


if __name__ == "__main__":
    unittest.main()
