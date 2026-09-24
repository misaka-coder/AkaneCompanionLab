"""Tests for browser_page hybrid observation (AX + temporary screenshot, decoupled action/observation)."""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote
from unittest.mock import MagicMock

from companion_v01.browser_page_download import (
    BrowserDownloadRecord,
    BrowserDownloadTracker,
    sanitize_download_filename,
)
from companion_v01.browser_page_observation import (
    ObservationRecord,
    ObservationStore,
    generate_observation_id,
    generate_screenshot_id,
)
from companion_v01.browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler


def _make_context(profile_user_id: str = "p1", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=1_700_000_000,
        visual_payload={},
        client_mode="desktop_pet",
    )


class ObservationStoreTests(unittest.TestCase):
    def test_store_put_get_latest(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        obs_id = generate_observation_id()
        record = ObservationRecord(
            observation_id=obs_id,
            url="https://example.com",
            title="Example",
            aria_snapshot="- button 'OK'",
        )
        store.put(record)
        fetched = store.get(obs_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.url, "https://example.com")
        self.assertEqual(store.latest(), fetched)

    def test_store_expiration_after_ttl(self) -> None:
        now = [1000.0]
        store = ObservationStore(max_records=5, ttl_seconds=60, now=lambda: now[0])
        obs_id = generate_observation_id()
        record = ObservationRecord(observation_id=obs_id, url="https://example.com")
        store.put(record)
        self.assertIsNotNone(store.get(obs_id))

        now[0] = 1000.0 + 61.0
        self.assertIsNone(store.get(obs_id))
        self.assertIsNone(store.latest())

    def test_target_freshness_validation(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        obs_id = generate_observation_id()
        record = ObservationRecord(
            observation_id=obs_id,
            browser_generation=1,
            document_generation=2,
            url="https://example.com",
        )
        store.put(record)

        # Fresh target
        valid, _ = store.validate_action_target(
            observation_id=obs_id,
            current_browser_gen=1,
            current_doc_gen=2,
        )
        self.assertTrue(valid)

        # Browser was restarted
        valid, reason = store.validate_action_target(
            observation_id=obs_id,
            current_browser_gen=2,
            current_doc_gen=2,
        )
        self.assertFalse(valid)
        self.assertIn("browser_restarted", reason)

        # Document navigated
        valid, reason = store.validate_action_target(
            observation_id=obs_id,
            current_browser_gen=1,
            current_doc_gen=3,
        )
        self.assertFalse(valid)
        self.assertIn("document_navigated", reason)

    def test_target_freshness_includes_page_revision(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        obs_id = generate_observation_id()
        store.put(
            ObservationRecord(
                observation_id=obs_id,
                page_id="page_a",
                page_revision=4,
                browser_generation=1,
                document_generation=2,
            )
        )
        valid, reason = store.validate_action_target(
            observation_id=obs_id,
            current_browser_gen=1,
            current_doc_gen=2,
            current_page_id="page_a",
            current_page_revision=4,
        )
        self.assertTrue(valid)
        self.assertEqual(reason, "")

        valid, reason = store.validate_action_target(
            observation_id=obs_id,
            current_browser_gen=1,
            current_doc_gen=2,
            current_page_id="page_a",
            current_page_revision=5,
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "page_revision_stale_observation")

    def test_screenshot_budget_drops_visual_payload_without_dropping_text(self) -> None:
        store = ObservationStore(
            max_records=5,
            ttl_seconds=60,
            max_screenshot_bytes=4,
            max_total_screenshot_bytes=8,
        )
        stored = store.put(
            ObservationRecord(
                observation_id="obs_budget",
                screenshot_id="shot_budget",
                screenshot_bytes=b"12345",
                visible_text="still usable text",
                visual_status="available",
            )
        )
        self.assertEqual(stored.visible_text, "still usable text")
        self.assertEqual(stored.screenshot_id, "")
        self.assertEqual(stored.visual_status, "visual_unavailable")
        self.assertIsNone(store.get_by_screenshot_id("shot_budget"))


class HybridObservationHandlerTests(unittest.TestCase):
    def test_handler_injects_temporary_screenshot_into_model_image_inputs(self) -> None:
        obs_id = generate_observation_id()
        shot_id = generate_screenshot_id()
        fake_model_image = {
            "attachment_id": obs_id,
            "attachment_handle": shot_id,
            "title": f"browser-view-{obs_id[:8]}",
            "media_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        }

        class HybridRunner:
            def run(self, **kwargs) -> BrowserPageResult:
                return BrowserPageResult(
                    ok=True,
                    status="available",
                    action=kwargs.get("action", "navigate"),
                    url="https://example.com",
                    title="Example",
                    text="Page text content",
                    observation_id=obs_id,
                    screenshot_id=shot_id,
                    action_state="executed",
                    observation_state="complete",
                    page_changed="yes",
                    visual_status="available",
                    model_image=fake_model_image,
                    viewport=(1280, 720),
                    screenshot_dimensions=(1280, 720),
                )

        generated_file_service = MagicMock()
        handler = BrowserPageToolHandler(
            browser_runner=HybridRunner(),
            generated_file_service=generated_file_service,
        )
        call = handler.normalize_call({"type": "browser_page", "action": "navigate", "url": "https://example.com"})
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=_make_context())

        # Temporary screenshot must be injected into model_image_inputs
        self.assertEqual(len(result.model_image_inputs), 1)
        self.assertEqual(result.model_image_inputs[0]["attachment_id"], obs_id)
        self.assertEqual(result.model_image_inputs[0]["attachment_handle"], shot_id)

        # Must NOT register as a permanent GeneratedFile
        generated_file_service.register_generated_artifact.assert_not_called()

        # Context contains observation metadata and multi-modal note
        self.assertIn(f"观察编号：{obs_id}", result.followup_context)
        self.assertIn("多模态视口截图", result.followup_context)
        self.assertIn(shot_id, result.followup_context)
        self.assertIn("1280 × 720 CSS", result.followup_context)

    def test_stale_observation_rejected_for_control_action(self) -> None:
        class StaleTargetRunner:
            def run(self, **kwargs) -> BrowserPageResult:
                if kwargs.get("observation_id") == "obs_expired":
                    return BrowserPageResult(
                        ok=False,
                        status="stale_target",
                        action="click",
                        reason="document_navigated_stale_observation",
                        retryable=True,
                        next_action="snapshot",
                        observation_id="obs_expired",
                    )
                return BrowserPageResult(ok=True, status="available", action="click")

        handler = BrowserPageToolHandler(
            browser_runner=StaleTargetRunner(),
            approval_checker=lambda **kwargs: True,
        )
        call = handler.normalize_call({
            "type": "browser_page",
            "action": "click",
            "ref": "e2",
            "observation_id": "obs_expired",
        })
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=_make_context())
        self.assertEqual(result.state_updates["browser_page_status"], "stale_target")
        self.assertIn("重新调用 snapshot", result.followup_context)


class VisualCoordinateClickTests(unittest.TestCase):
    def test_validate_coordinate_target_valid(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        shot_id = generate_screenshot_id()
        obs_id = generate_observation_id()
        store.put(
            ObservationRecord(
                observation_id=obs_id,
                screenshot_id=shot_id,
                browser_generation=1,
                document_generation=1,
                viewport=(1280, 800),
            )
        )

        valid, reason = store.validate_coordinate_target(
            screenshot_id=shot_id,
            coordinate=(400, 300),
            current_browser_gen=1,
            current_doc_gen=1,
        )
        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_validate_coordinate_target_out_of_bounds(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        shot_id = generate_screenshot_id()
        obs_id = generate_observation_id()
        store.put(
            ObservationRecord(
                observation_id=obs_id,
                screenshot_id=shot_id,
                browser_generation=1,
                document_generation=1,
                viewport=(1280, 800),
            )
        )

        valid, reason = store.validate_coordinate_target(
            screenshot_id=shot_id,
            coordinate=(1500, 900),
            current_browser_gen=1,
            current_doc_gen=1,
        )
        self.assertFalse(valid)
        self.assertIn("coordinate_out_of_bounds", reason)

    def test_validate_coordinate_target_expired(self) -> None:
        now = [1000.0]
        store = ObservationStore(max_records=5, ttl_seconds=60, now=lambda: now[0])
        shot_id = generate_screenshot_id()
        obs_id = generate_observation_id()
        store.put(
            ObservationRecord(
                observation_id=obs_id,
                screenshot_id=shot_id,
                browser_generation=1,
                document_generation=1,
                viewport=(1280, 800),
            )
        )

        now[0] = 1000.0 + 61.0
        valid, reason = store.validate_coordinate_target(
            screenshot_id=shot_id,
            coordinate=(100, 100),
            current_browser_gen=1,
            current_doc_gen=1,
        )
        self.assertFalse(valid)
        self.assertIn("screenshot_expired", reason)

    def test_validate_coordinate_target_missing_screenshot_id(self) -> None:
        store = ObservationStore(max_records=5, ttl_seconds=60)
        valid, reason = store.validate_coordinate_target(
            screenshot_id="",
            coordinate=(100, 100),
            current_browser_gen=1,
            current_doc_gen=1,
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "screenshot_id_required")

    def test_handler_normalize_coordinate_click(self) -> None:
        handler = BrowserPageToolHandler(browser_runner=object())
        call = handler.normalize_call({
            "type": "browser_page",
            "action": "click",
            "coordinate": [250, 350],
            "screenshot_id": "shot_xyz",
        })
        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["coordinate"], (250, 350))
        self.assertEqual(call["screenshot_id"], "shot_xyz")

    def test_approval_preview_for_coordinate_click(self) -> None:
        handler = BrowserPageToolHandler(
            browser_runner=object(),
            approval_checker=lambda **kwargs: False,
        )
        call = handler.normalize_call({
            "type": "browser_page",
            "action": "click",
            "coordinate": [120, 240],
            "screenshot_id": "shot_123",
        })
        self.assertIsNotNone(call)
        assert call is not None
        result = handler.execute(call=call, context=_make_context())
        self.assertEqual(len(result.stream_events), 1)
        event = result.stream_events[0]
        self.assertEqual(event.get("type"), "capability_approval_required")
        preview = event.get("payloadPreview") or event.get("payload_preview") or {}
        self.assertEqual(preview.get("coordinate"), {"type": "array", "length": 2})
        self.assertEqual(preview.get("screenshot_id"), "shot_123")


class BrowserDownloadTests(unittest.TestCase):
    def test_sanitize_download_filename(self) -> None:
        self.assertEqual(sanitize_download_filename("../../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_download_filename("c:\\windows\\system32\\calc.exe"), "calc.exe")
        self.assertEqual(sanitize_download_filename("report:data*?.pdf"), "report_data__.pdf")
        cleaned_empty = sanitize_download_filename("...///\\\\")
        self.assertTrue(cleaned_empty.endswith(".bin"))

    def test_download_tracker_save_and_attachment_registration(self) -> None:
        import shutil
        import tempfile
        temp_dir = Path(tempfile.mkdtemp(prefix="akane_dl_test_"))
        try:
            mock_inbox = MagicMock()
            mock_inbox.base_dir = temp_dir
            mock_inbox.create_pending.return_value = {
                "attachment_id": "att_uuid_1",
                "attachment_handle": "att_001",
            }
            mock_inbox.mark_ready.return_value = {
                "attachment_id": "att_uuid_1",
                "attachment_handle": "att_001",
                "status": "ready",
            }

            tracker = BrowserDownloadTracker(attachment_service=mock_inbox)

            class FakeDownload:
                url = "https://example.com/files/document.txt"
                suggested_filename = "document.txt"

                def save_as(self, path: str) -> None:
                    Path(path).write_bytes(b"hello attachment inbox download")

            record = tracker.handle_playwright_download(
                FakeDownload(),
                profile_user_id="user_test",
                session_id="session_test",
            )

            self.assertEqual(record.status, "completed")
            self.assertEqual(record.file_size, len(b"hello attachment inbox download"))
            self.assertEqual(record.attachment_handle, "att_001")
            self.assertEqual(record.attachment_id, "att_uuid_1")
            self.assertEqual(record.profile_user_id, "user_test")
            self.assertEqual(record.session_id, "session_test")
            self.assertTrue(Path(record.saved_path).exists())
            self.assertEqual(Path(record.saved_path).read_bytes(), b"hello attachment inbox download")
            self.assertEqual(tracker.get(record.download_id), record)
            self.assertEqual(tracker.latest(), record)

            mock_inbox.create_pending.assert_called_once()
            mock_inbox.mark_ready.assert_called_once()
            self.assertEqual(mock_inbox.create_pending.call_args.kwargs["profile_user_id"], "user_test")
            self.assertEqual(mock_inbox.create_pending.call_args.kwargs["session_id"], "session_test")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_download_tracker_file_too_large(self) -> None:
        tracker = BrowserDownloadTracker(max_bytes=100)

        class LargeDownload:
            url = "https://example.com/big.iso"
            suggested_filename = "big.iso"

            def save_as(self, path: str) -> None:
                Path(path).write_bytes(b"A" * 500)

        record = tracker.handle_playwright_download(LargeDownload())
        self.assertEqual(record.status, "failed")
        self.assertIn("file_too_large", record.error_reason)

    def test_download_registration_failure_does_not_publish_success(self) -> None:
        class Download:
            url = "https://example.com/probe.txt"
            suggested_filename = "probe.txt"

            def save_as(self, path):
                Path(path).write_bytes(b"saved but not registered")

        for failure in ("create", "ready"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                inbox = MagicMock()
                inbox.base_dir = Path(directory)
                if failure == "create":
                    inbox.create_pending.side_effect = RuntimeError("registration unavailable")
                else:
                    inbox.create_pending.return_value = {"attachment_id": "id", "attachment_handle": "att_1"}
                    inbox.mark_ready.return_value = None
                tracker = BrowserDownloadTracker(attachment_service=inbox)
                record = tracker.handle_playwright_download(Download())
                self.assertEqual(record.status, "failed")
                self.assertIn("attachment_registration_failed", record.error_reason)
                self.assertFalse(record.attachment_handle)
                self.assertEqual(Path(record.saved_path).read_bytes(), b"saved but not registered")

    def test_download_status_query_action(self) -> None:
        class FakeRunner:
            def run(self, **kwargs) -> BrowserPageResult:
                if kwargs.get("action") == "download_status":
                    return BrowserPageResult(
                        ok=True,
                        status="completed",
                        action="download_status",
                        text="下载任务标识: dl_123\n文件名: report.pdf\n状态: completed\n大小: 1024 字节\n工作台材料句柄: att_002\n",
                        download_id="dl_123",
                        download_status="completed",
                        download_filename="report.pdf",
                        attachment_handle="att_002",
                    )
                return BrowserPageResult(ok=True, status="available", action="current")

        handler = BrowserPageToolHandler(browser_runner=FakeRunner())
        call = handler.normalize_call({"type": "browser_page", "action": "download_status", "download_id": "dl_123"})
        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["action"], "download_status")
        self.assertEqual(call["download_id"], "dl_123")

        result = handler.execute(call=call, context=_make_context())
        self.assertIn("dl_123", result.followup_context)
        self.assertIn("att_002", result.followup_context)
        self.assertIn("inspect_attachment", result.followup_context)


class PlaywrightLiveHybridSmokeTests(unittest.TestCase):
    def test_live_stale_visual_evidence_and_executed_popup_failure(self) -> None:
        runner = ManagedBrowserPageRunner(headless=True)
        if not runner.is_available():
            self.skipTest("Playwright not installed")
        try:
            html = """<button id='target' style='position:absolute;left:40px;top:40px;width:120px;height:80px'
                onclick='window.hits=(window.hits||0)+1;window.open("about:blank")'>Original</button>
                <input type='password' value='hidden'>"""
            observed = runner.run(action="navigate", url="data:text/html," + quote(html))
            self.assertEqual(observed.observation_state, "complete")
            runner._executor.submit(lambda: runner._page.evaluate(
                "document.getElementById('target').textContent='Replacement'"
            )).result()
            stale = runner.run(action="click", coordinate=(70, 70),
                screenshot_id=observed.screenshot_id, observation_id=observed.observation_id)
            self.assertEqual(stale.status, "stale_target")
            self.assertEqual(stale.action_state, "not_started")
            self.assertFalse(runner._executor.submit(lambda: runner._page.evaluate("window.hits || 0")).result())
            self.assertIsNotNone(runner.read_snapshot(observed.snapshot_id))

            fresh = runner.run(action="snapshot")
            failed = runner.run(action="click", selector="#target", observation_id=fresh.observation_id)
            self.assertFalse(failed.ok)
            self.assertIn("popup_url_rejected", failed.reason)
            self.assertEqual(failed.action_state, "executed")
            self.assertEqual(runner._executor.submit(lambda: runner._page.evaluate("window.hits")).result(), 1)
            handler = BrowserPageToolHandler(browser_runner=runner)
            feedback = handler._failure(failed)
            self.assertIn("不要自动重复", feedback.followup_context)
            self.assertEqual(feedback.stream_events[-1]["action_state"], "executed")

            canvas_page = runner.run(action="navigate", url="data:text/html," + quote(
                "<canvas width='200' height='200'></canvas>"
            ))
            runner._executor.submit(lambda: runner._page.evaluate(
                "document.querySelector('canvas').getContext('2d').fillRect(0,0,200,200)"
            )).result()
            stale_canvas = runner.run(action="click", coordinate=(70, 70), screenshot_id=canvas_page.screenshot_id)
            self.assertEqual(stale_canvas.status, "stale_target")
            self.assertEqual(stale_canvas.action_state, "not_started")
            self.assertEqual(stale_canvas.reason, "coordinate_target_changed_or_masked")
        finally:
            runner.shutdown()

    def test_live_playwright_hybrid_observation(self) -> None:
        runner = ManagedBrowserPageRunner(headless=True)
        if not runner.is_available():
            self.skipTest("Playwright not installed in current environment")

        try:
            # Step 1: Navigate to a test page with hybrid observation
            test_html = "data:text/html,<html><head><title>Hybrid Test</title></head><body><h1>Playwright Hybrid</h1><button id='btn' onclick='window.clicked=true'>Click Me</button></body></html>"
            result = runner.run(action="navigate", url=test_html)

            self.assertTrue(result.ok)
            self.assertEqual(result.action_state, "executed")
            self.assertEqual(result.observation_state, "complete")
            self.assertEqual(result.visual_status, "available")
            self.assertTrue(result.observation_id.startswith("obs_"))
            self.assertTrue(result.screenshot_id.startswith("shot_"))
            self.assertIsNotNone(result.model_image)
            assert result.model_image is not None
            self.assertTrue(result.model_image["data_url"].startswith("data:image/png;base64,"))
            self.assertIn("Hybrid Test", result.title)

            # Step 2: Read text with text mode (must NOT generate screenshot)
            read_result = runner.run(action="read_text", observation_mode="text")
            self.assertTrue(read_result.ok)
            self.assertEqual(read_result.visual_status, "not_requested")
            self.assertIsNone(read_result.model_image)
            self.assertEqual(read_result.screenshot_id, "")

            # Step 3: Perform a click with observation_id binding
            click_result = runner.run(
                action="click",
                selector="#btn",
                observation_id=result.observation_id,
            )
            self.assertTrue(click_result.ok)
            self.assertEqual(click_result.action_state, "executed")
            self.assertEqual(click_result.page_changed, "unknown")
            self.assertEqual(click_result.visual_status, "available")
            self.assertIsNotNone(click_result.model_image)

            # Step 4: Validate visual coordinate click on custom non-semantic element
            coord_html = """data:text/html,<html><body><div id='box' style='width:100px;height:100px;background:red;position:absolute;left:50px;top:50px;' onclick='window.coord_clicked=true'></div></body></html>"""
            nav_box = runner.run(action="navigate", url=coord_html)
            self.assertTrue(nav_box.ok)
            self.assertTrue(nav_box.screenshot_id)

            coord_click = runner.run(
                action="click",
                coordinate=(75, 75),
                screenshot_id=nav_box.screenshot_id,
            )
            self.assertTrue(coord_click.ok)
            self.assertEqual(coord_click.action_state, "executed")
            self.assertEqual(coord_click.visual_status, "available")

            # Step 5: Validate dialog auto-dismiss
            dialog_html = "data:text/html,<html><body><button id='alert-btn' onclick='alert(\"test dialog\")'>Alert</button></body></html>"
            dialog_nav = runner.run(action="navigate", url=dialog_html)
            dialog_result = runner.run(action="click", selector="#alert-btn", observation_id=dialog_nav.observation_id)
            self.assertTrue(dialog_result.dialog_events)
            self.assertEqual(dialog_result.dialog_events[-1]["type"], "alert")
            self.assertTrue(len(runner._dialog_events) >= 1)
            self.assertEqual(runner._dialog_events[-1]["type"], "alert")
            self.assertEqual(runner._dialog_events[-1]["message"], "test dialog")

        finally:
            runner.shutdown()

    def test_live_playwright_download_interception(self) -> None:
        class DownloadServer(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/file.txt":
                    content = b"Managed browser download smoke test payload 98765"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", "attachment; filename=smoke_dl.txt")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b'<a id="download_link" href="/file.txt">Download File</a>')

            def log_message(self, *args):
                pass

        server = socketserver.TCPServer(("127.0.0.1", 0), DownloadServer)
        port = server.server_address[1]
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()

        download_workspace = tempfile.TemporaryDirectory(prefix="akane_browser_download_review_")
        inbox = AttachmentInboxService(
            store=MemoryStore(Path(download_workspace.name) / "store"),
            base_dir=Path(download_workspace.name) / "inbox",
        )
        runner = ManagedBrowserPageRunner(headless=True, attachment_service=inbox)
        if not runner.is_available():
            server.shutdown()
            server.server_close()
            download_workspace.cleanup()
            self.skipTest("Playwright not installed in current environment")

        try:
            # Navigate to local test page
            nav = runner.run(action="navigate", url=f"http://127.0.0.1:{port}/")
            self.assertTrue(nav.ok)

            # Click download link
            click_res = runner.run(action="click", selector="#download_link", observation_id=nav.observation_id)
            self.assertTrue(click_res.ok)

            # Query download status
            status_res = runner.run(action="download_status")
            if status_res.download_status == "pending":
                time.sleep(0.1)
                status_res = runner.run(action="download_status")
            self.assertTrue(status_res.ok)
            self.assertEqual(status_res.download_status, "completed")
            self.assertEqual(status_res.download_filename, "smoke_dl.txt")
            self.assertTrue(status_res.download_id.startswith("dl_"))
            self.assertTrue(status_res.attachment_handle.startswith("file_"))
            inspected = inbox.inspect_attachment(
                profile_user_id="default_user", session_id="default_session", target=status_res.attachment_handle,
            )
            self.assertIn("smoke_dl.txt", inspected["followup_context"])
            saved = Path(runner.get_download(status_res.download_id).saved_path)
            runner.shutdown()
            self.assertEqual(saved.read_bytes(), b"Managed browser download smoke test payload 98765")

        finally:
            runner.shutdown()
            server.shutdown()
            server.server_close()
            srv_thread.join(timeout=2)
            download_workspace.cleanup()

    def test_candidate_index_click_requires_observation(self) -> None:
        runner = ManagedBrowserPageRunner(headless=True)
        if not runner.is_available():
            self.skipTest("Playwright not installed in current environment")
        try:
            test_html = "data:text/html,<html><body><a href='https://example.com/one'>Link 1</a></body></html>"
            runner.run(action="navigate", url=test_html)

            # Clicking candidate_index without observation returns stale_target (no guessing)
            click_res = runner.run(action="click", candidate_index=1)
            self.assertFalse(click_res.ok)
            self.assertEqual(click_res.status, "stale_target")
            self.assertIn("observation_id_required", click_res.reason)

            # Perform elements observation to freeze candidates
            elem_res = runner.run(action="elements")
            self.assertTrue(elem_res.ok)
            obs_id = elem_res.observation_id
            self.assertTrue(obs_id)

            # Clicking candidate_index=1 bound to observation succeeds
            click_success = runner.run(action="click", candidate_index=1, observation_id=obs_id)
            self.assertTrue(click_success.ok)

            # Clicking candidate_index=99 (out of range) fails with stale_target
            click_oor = runner.run(action="click", candidate_index=99, observation_id=obs_id)
            self.assertFalse(click_oor.ok)
            self.assertEqual(click_oor.status, "stale_target")
            self.assertIn("document_navigated", click_oor.reason)
        finally:
            runner.shutdown()


if __name__ == "__main__":
    unittest.main()
