"""Behavior at the public browser/search input boundaries."""

import tempfile
import unittest
from pathlib import Path

from companion_v01.browser_page_runtime import BrowserPageResult
from companion_v01.tool_handlers.core import ToolExecutionContext

from companion_v01.tool_handlers.web_browser import (
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    WebSearchToolHandler,
)


class BrowserUrlBoundaryTests(unittest.TestCase):
    def handlers(self):
        return (
            OpenBrowserToolHandler(),
            BrowserPageToolHandler(browser_runner=object()),
            WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object()),
        )

    def test_malformed_urls_are_rejected_without_raising(self):
        for handler in self.handlers():
            for url in ("https://[broken/", "https://[example.com]/", "https://example.com:bad/",
                        "https://example.com:70000/", "https://example.com/a\nb"):
                with self.subTest(tool=handler.tool_type, url=url):
                    self.assertIsNone(handler.normalize_call({"type": handler.tool_type, "url": url}))

    def test_overlong_url_is_rejected_instead_of_opening_a_different_target(self):
        url = "https://example.com/" + "a" * 1700
        for handler in self.handlers():
            with self.subTest(tool=handler.tool_type):
                self.assertIsNone(handler.normalize_call({"type": handler.tool_type, "url": url}))

    def test_embedded_credentials_are_never_accepted(self):
        for handler in self.handlers():
            with self.subTest(tool=handler.tool_type):
                self.assertIsNone(handler.normalize_call({"type": handler.tool_type,
                                                         "url": "https://user:fixture-password@example.com/"}))

    def test_explicit_private_network_policy_still_applies_only_to_managed_browser(self):
        managed = BrowserPageToolHandler(browser_runner=object(), allow_private_network_urls=True)
        self.assertIsNotNone(managed.normalize_call({"type": "browser_page", "url": "http://127.0.0.1:8188/"}))
        for handler in self.handlers():
            self.assertIsNone(handler.normalize_call({"type": handler.tool_type, "url": "http://127.0.0.1:8188/"}))


def context():
    return ToolExecutionContext(profile_user_id="p1", session_id="s1", now_ts=1700000000,
                                visual_payload={}, client_mode="desktop_pet")


class BrowserExecutionBoundaryTests(unittest.TestCase):
    def test_mapping_batch_failure_preserves_receipts_and_does_not_repeat_steps(self):
        class Runner:
            def run(self, **kwargs):
                return {"ok": False, "action": "run_actions", "status": "site_unreachable",
                        "action_state": "executed", "step_results": [
                            {"action": "click", "status": "executed", "index": 0},
                            {"action": "fill", "status": "not_started", "index": 1},
                        ]}

        handler = BrowserPageToolHandler(browser_runner=Runner(), approval_checker=lambda **kwargs: True)
        call = handler.normalize_call({"type": "browser_page", "action": "run_actions", "actions": [
            {"action": "click", "selector": "#first"}, {"action": "fill", "selector": "#next", "text": "x"},
        ]})
        result = handler.execute(call=call, context=context())
        self.assertIn("批次逐步结果", result.followup_context)
        self.assertIn('"index": 1', result.followup_context)
        self.assertIn("只继续未执行", result.followup_context)
        self.assertEqual(result.stream_events[-1]["action_state"], "executed")

    def test_malformed_runner_result_is_failure_with_unknown_action_state(self):
        class Runner:
            def run(self, **kwargs):
                return {"ok": True, "shown_chars": "not-a-number"}

        handler = BrowserPageToolHandler(browser_runner=Runner(), approval_checker=lambda **kwargs: True)
        call = handler.normalize_call({"type": "browser_page", "action": "click", "selector": "#submit"})
        result = handler.execute(call=call, context=context())
        self.assertEqual(result.stream_events[-1]["status"], "unavailable")
        self.assertEqual(result.stream_events[-1]["action_state"], "unknown")
        self.assertIn("不要自动重复", result.followup_context)

    def test_screenshot_failures_remove_output_and_emit_no_ready_file(self):
        for registration_fails in (False, True):
            with self.subTest(registration_fails=registration_fails), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "screenshot.png"

                class Service:
                    def allocate_output_path(self, **kwargs):
                        return path

                    def register_generated_artifact(self, **kwargs):
                        raise RuntimeError("fixture failure")

                class Runner:
                    def run(self, **kwargs):
                        Path(kwargs["screenshot_path"]).write_bytes(b"fixture")
                        return BrowserPageResult(ok=registration_fails, action="screenshot",
                                                 status="available" if registration_fails else "unavailable")

                handler = BrowserPageToolHandler(browser_runner=Runner(), generated_file_service=Service())
                result = handler.execute(call={"type": "browser_page", "action": "screenshot"}, context=context())
                self.assertFalse(path.exists())
                self.assertFalse(any(e["type"] == "generated_file_ready" for e in result.stream_events))
                self.assertEqual(result.state_updates["browser_page_status"], "unavailable")

    def test_legacy_runner_rejects_unsupported_extensions_without_running(self):
        class Runner:
            calls = 0

            def run(self, action, url, max_chars):
                self.calls += 1

        runner = Runner()
        handler = BrowserPageToolHandler(browser_runner=runner)
        call = handler.normalize_call({"type": "browser_page", "action": "snapshot", "scope_selector": "main"})
        result = handler.execute(call=call, context=context())
        self.assertEqual(runner.calls, 0)
        self.assertEqual(result.state_updates["browser_page_reason"], "browser_runner_extensions_unavailable")

    def test_upload_authorization_precedes_resource_resolution(self):
        class Service:
            calls = 0

            def resolve_input_resource(self, **kwargs):
                self.calls += 1

        service = Service()

        with tempfile.TemporaryDirectory() as directory:
            handler = BrowserPageToolHandler(browser_runner=object(), generated_file_service=service,
                                             config_base_dir=directory)
            call = handler.normalize_call({"type": "browser_page", "action": "upload", "selector": "#file",
                                           "files": ["gen_001"]})
            self.assertIsNotNone(call)
            result = handler.execute(call=call, context=context())
        self.assertEqual(result.state_updates["browser_control_status"], "approval_required")
        self.assertEqual(service.calls, 0)


if __name__ == "__main__":
    unittest.main()
