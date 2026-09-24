from __future__ import annotations

import contextlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "akane_voice_runtime.ps1"


@contextlib.contextmanager
def api_server(paths):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"paths": paths}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell 7 required")
class VoiceRuntimeLauncherTests(unittest.TestCase):
    def run_helper(self, body, *, port=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("runtime/python.exe", "api_v2.py", "GPT_SoVITS/configs/tts_infer.yaml"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            if port is None:
                with socket.socket() as bound:
                    bound.bind(("127.0.0.1", 0))
                    port = bound.getsockname()[1]
            script = root / "check.ps1"
            script.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                f". '{str(HELPER).replace(chr(39), chr(39) * 2)}'\n"
                f"$root = '{str(root).replace(chr(39), chr(39) * 2)}'\n"
                f"$port = {port}\n"
                "$receipt = Join-Path $root 'run/voice.pid'\n"
                "$logs = Join-Path $root 'logs'\n"
                + body,
                encoding="utf-8",
            )
            result = subprocess.run(
                [shutil.which("pwsh"), "-NoProfile", "-File", str(script)],
                capture_output=True, text=True, timeout=25, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_verified_api_is_reused_without_process_or_receipt(self):
        with api_server({"/tts": {}}) as port:
            result = self.run_helper(
                "function Start-Process { throw 'must_not_launch' }\n"
                "$first = Ensure-AkaneGptSoVitsApi -Root $root -Port $port -LogDirectory $logs -PidPath $receipt\n"
                "$second = Ensure-AkaneGptSoVitsApi -Root $root -Port $port -LogDirectory $logs -PidPath $receipt\n"
                "@{first=$first; second=$second; receipt=(Test-Path $receipt)} | ConvertTo-Json -Compress\n",
                port=port,
            )
        self.assertEqual(result["first"], {"Status": "ready", "Started": False})
        self.assertEqual(result["second"], result["first"])
        self.assertFalse(result["receipt"])

    def test_occupied_non_voice_port_is_rejected_without_stopping_it(self):
        with api_server({"/health": {}}) as port:
            result = self.run_helper(
                "function Start-Process { throw 'must_not_launch' }\n"
                "function Stop-Process { throw 'must_not_stop' }\n"
                "try { Ensure-AkaneGptSoVitsApi -Root $root -Port $port -LogDirectory $logs -PidPath $receipt; throw 'unexpected_ready' }\n"
                "catch { @{reason=$_.Exception.Message; receipt=(Test-Path $receipt)} | ConvertTo-Json -Compress }\n",
                port=port,
            )
        self.assertEqual(result["reason"], "gpt_sovits_port_in_use_by_another_service")
        self.assertFalse(result["receipt"])

    def launch_fixture(self, *, ready):
        return self.run_helper(
            "function Get-AkaneGptSoVitsApi { return $null }\n"
            "function Test-AkaneTcpPort { return $false }\n"
            "$script:launch = $null; $script:stopped = @()\n"
            "function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)\n"
            "  $script:launch = @{args=$ArgumentList; hidden=($WindowStyle -eq 'Hidden'); cwdMatches=($WorkingDirectory -eq $root)}\n"
            "  return [pscustomobject]@{Id=7654321; HasExited=$false}\n"
            "}\n"
            "function Stop-Process { param($Id,[switch]$Force,$ErrorAction); $script:stopped += $Id }\n"
            "function Wait-AkaneGptSoVitsApi { "
            + ("return @{paths=@{'/tts'=@{}}}" if ready else "return $null")
            + " }\n"
            "try { $result=Ensure-AkaneGptSoVitsApi -Root $root -Port $port -LogDirectory $logs -PidPath $receipt; $reason='' }\n"
            "catch { $result=$null; $reason=$_.Exception.Message }\n"
            "@{result=$result; reason=$reason; launch=$script:launch; stopped=$script:stopped; pid=(Get-Content $receipt); port=$port} | ConvertTo-Json -Compress -Depth 4\n"
        )

    def test_start_uses_existing_runtime_loopback_hidden_window_and_readiness(self):
        result = self.launch_fixture(ready=True)
        self.assertEqual(result["result"], {"Status": "ready", "Started": True})
        self.assertTrue(result["launch"]["hidden"])
        self.assertTrue(result["launch"]["cwdMatches"])
        self.assertEqual(result["launch"]["args"], [
            "-I", f'"{ROOT / "scripts" / "launch_gpt_sovits_api.py"}"',
            "--upstream-api", "api_v2.py", "-a", "127.0.0.1", "-p", str(result["port"]),
            "-c", "GPT_SoVITS/configs/tts_infer.yaml",
        ])
        self.assertEqual(result["pid"], "7654321")
        self.assertEqual(result["stopped"], [])

    def test_failed_readiness_stops_only_the_new_process_and_never_reports_ready(self):
        result = self.launch_fixture(ready=False)
        self.assertIsNone(result["result"])
        self.assertEqual(result["reason"], "gpt_sovits_start_timeout_or_exit")
        self.assertEqual(result["stopped"], [7654321])


if __name__ == "__main__":
    unittest.main()
