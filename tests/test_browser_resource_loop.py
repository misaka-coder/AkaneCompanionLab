"""A real browser download -> tracked CLI artifact -> browser upload."""
import http.server
import socketserver
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from companion_v01.browser_page_runtime import ManagedBrowserPageRunner
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.local_capability_config import save_capability_approval_modes
from companion_v01.tool_handlers.execution import ExecRunToolHandler, ExecStatusToolHandler
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler
from tests.test_execution_resources import _Harness, _context, _python_command


class BrowserResourceLoopTests(unittest.TestCase):
    def test_download_transform_and_upload(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path == "/source":
                    self.send_header("Content-Disposition", 'attachment; filename="source.txt"')
                    self.send_header("Content-Type", "text/plain")
                    body = b"synthetic resource loop"
                else:
                    self.send_header("Content-Type", "text/html")
                    body = b'<a id="download" href="/source">Download</a><input id="upload" type="file">'
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass

        with tempfile.TemporaryDirectory() as directory, socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            harness = _Harness(Path(directory))
            runner = ManagedBrowserPageRunner(headless=True, allow_private_network_urls=True, attachment_service=harness.attachment_service)
            if not runner.is_available(): self.skipTest("Playwright unavailable")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            runner.set_session_identity("alice", "s1")
            context = replace(_context(), now_ts=int(time.time()))
            provider = TrustedLocalExecutor(workspace_root=harness.workspace, run_log_dir=Path(directory)/"logs")
            try:
                nav = runner.run(action="navigate", url=f"http://127.0.0.1:{server.server_address[1]}/", observation_mode="text")
                downloaded = runner.run(action="click", selector="#download", observation_id=nav.observation_id, observation_mode="text")
                self.assertTrue(downloaded.ok, downloaded.reason)
                download = runner.run(action="download_status")
                deadline = time.monotonic() + 10
                while download.download_status == "pending" and time.monotonic() < deadline:
                    time.sleep(.05)
                    download = runner.run(action="download_status")
                self.assertEqual(download.download_status, "completed", download.reason)
                self.assertTrue(download.attachment_handle)
                save_capability_approval_modes(base_dir=directory, profile_user_id="alice", modes={"ops":"trusted_auto_allow"})
                execute = ExecRunToolHandler(execution_provider=provider, config_base_dir=directory, resource_bridge=harness.bridge)
                command = _python_command("from pathlib import Path; Path('out.txt').write_bytes(Path('in.txt').read_bytes().upper())")
                call = execute.normalize_call({"type":"exec_run", "command":command, "initial_wait_seconds":5,
                    "input_resources":[{"handle":download.attachment_handle,"as":"in.txt"}], "output_globs":["out.txt"]})
                result = execute.execute(call=call, context=context)
                state = result.state_updates["capability_execution"]
                if state["status"] == "running":
                    status = ExecStatusToolHandler(execution_provider=provider, resource_bridge=harness.bridge)
                    result = status.execute(call={"type":"exec_status", "run_id":state["run_id"], "wait_seconds":10}, context=context)
                    state = result.state_updates["capability_execution"]
                self.assertEqual(state["status"], "completed", result.followup_context)
                handle = state["generated_resources"][0]["handle"]
                browser = BrowserPageToolHandler(browser_runner=runner, generated_file_service=harness.generated_service,
                    approval_checker=lambda **kw:True, allow_private_network_urls=True)
                obs = runner.run(action="snapshot", observation_mode="text")
                call = browser.normalize_call({"type":"browser_page", "action":"upload", "selector":"#upload",
                    "files":[handle], "observation_id":obs.observation_id, "observation_mode":"text"})
                result = browser.execute(call=call, context=context)
                actual = runner._submit(lambda: runner._page.locator("#upload").evaluate("el=>el.files[0].text()"))
                self.assertEqual(actual, "SYNTHETIC RESOURCE LOOP", result.followup_context)
                self.assertNotIn(str(harness.managed), result.followup_context)
            finally:
                provider.request_shutdown()
                runner.shutdown()
                server.shutdown()
