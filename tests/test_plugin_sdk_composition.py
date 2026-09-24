"""Install independent SDK projects and compose a real HTTP query into a CSV."""

from __future__ import annotations

import asyncio
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.tool_runtime import SendFileToolHandler, ToolExecutionContext
from tests.test_document_writer_plugin import DocumentHarness


ROOT = Path(__file__).resolve().parents[1]


class SdkCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_public_query_to_document_without_model_or_preview_dependencies(self):
        requests = []
        catalog = [{"name": "铅笔", "quantity": 3, "price": 1.5},
                   {"name": "Notebook, A5", "quantity": 2, "price": 9.25}]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = json.dumps(catalog if self.path == "/catalog" else {"error": "wrong schema"},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = await DocumentHarness(root).start()
            try:
                provider = EnginePluginCapabilityProvider(harness.engine)
                harness.runtime.bind_capability_provider(provider)
                await harness.install()
                for project in ("akane_sdk_catalog", "akane_sdk_report"):
                    staged = await harness.service.stage_source(source_path=str(ROOT / "examples/plugins" / project))
                    self.assertTrue(staged["ok"], staged)
                    installed = await harness.service.install_stage(
                        stage_id=staged["stage_id"], approved_permissions=staged["permissions"],
                    )
                    self.assertTrue(installed["ok"], installed)
                handler = harness.handlers()["example.report.create"]
                self.assertNotIn("ctx", handler.tool_spec().input_schema["properties"])
                self.assertNotIn("send_to_user", harness.handlers()["example.catalog.fetch"].tool_spec().input_schema["properties"])
                from companion_v01.plugin_tool_bridge import project_model_result
                with patch("companion_v01.plugin_tool_bridge.project_model_result", wraps=project_model_result) as projection:
                    result = await asyncio.to_thread(handler.execute,
                        call=handler.normalize_call({"type": "example.report.create", "arguments": {"url": url + "/catalog"}}),
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"))
                    # Only the top-level report is consumed by the model/UI.
                    self.assertEqual(projection.call_count, 1)
                self.assertFalse(result.capability_result.is_error, result.capability_result)
                self.assertEqual(result.capability_result.value, {"row_count": 2})
                self.assertEqual(requests, ["/catalog"])
                ready = [event for event in result.stream_events if event["type"] == "generated_file_ready"]
                self.assertEqual(len(ready), 1)
                self.assertTrue(ready[0]["send_to_user"])
                handle = ready[0]["generated_file"]["generated_handle"]
                resource = harness.resolve(handle)
                with Path(resource["absolute_path"]).open(encoding="utf-8-sig", newline="") as stream:
                    self.assertEqual(list(csv.reader(stream)), [["Name", "Quantity", "Price"],
                                                              ["铅笔", "3", "1.5"], ["Notebook, A5", "2", "9.25"]])
                records = harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
                self.assertEqual(len(records), 2)  # source document and explicit final report, no preview artifact
                self.assertEqual(list((root / "copies").iterdir()), [])
                send = SendFileToolHandler(generated_file_service=harness.files)
                opened = send.execute(call=send.normalize_call({"type": "send_file", "target": handle, "delivery_action": "open"}),
                                      context=ToolExecutionContext("owner", "session", 2, {}, client_mode="desktop_pet"))
                event = opened.stream_events[0]
                self.assertEqual(event["desktop_delivery"]["action"], "open")
                if shutil.which("node"):
                    smoke = await asyncio.to_thread(subprocess.run,
                        ["node", str(ROOT / "desktop_pet_next/scripts/media-plugin-delivery-smoke.mjs"), "csv"],
                        input=json.dumps(event), text=True, capture_output=True, timeout=20)
                    self.assertEqual(smoke.returncode, 0, smoke.stderr)
                # A wrong output contract fails before document creation; the
                # failed dependency remains inspectable at the parent boundary.
                failed = await asyncio.to_thread(handler.execute,
                    call=handler.normalize_call({"type": "example.report.create", "arguments": {"url": url + "/invalid"}}),
                    context=ToolExecutionContext("owner", "session", 3, {}, client_mode="desktop_pet"))
                self.assertTrue(failed.capability_result.is_error)
                self.assertEqual(failed.capability_result.reason, "plugin_result_schema_mismatch")
                self.assertFalse(failed.capability_result.content["retryable"])
                self.assertEqual(requests, ["/catalog", "/invalid"])
                self.assertEqual(len(harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)), 2)
                self.assertTrue((await harness.service.set_enabled(plugin_id="example.catalog", enabled=False))["ok"])
                handler = harness.handlers()["example.report.create"]
                disabled = await asyncio.to_thread(handler.execute,
                    call=handler.normalize_call({"type": "example.report.create", "arguments": {"url": url + "/catalog"}}),
                    context=ToolExecutionContext("owner", "session", 4, {}, client_mode="desktop_pet"))
                self.assertEqual(disabled.capability_result.reason, "capability_dependency_unavailable")
                self.assertEqual(requests, ["/catalog", "/invalid"])
            finally:
                await harness.close()


if __name__ == "__main__":
    unittest.main()
