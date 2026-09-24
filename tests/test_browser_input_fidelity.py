"""Input data must survive the public browser facade without heuristic filtering."""
import unittest
from urllib.parse import quote
from companion_v01.browser_page_runtime import ManagedBrowserPageRunner
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler
from tests.test_browser_page_hybrid_observation import _make_context


class BrowserInputFidelityTests(unittest.TestCase):
    def test_normalization_preserves_data_and_distinguishes_missing(self):
        handler = BrowserPageToolHandler(browser_runner=object())
        for text in ("", "  alpha\n  beta\tend  ", "\r\n", "Documentation example: token=example"):
            with self.subTest(text=text):
                raw = {"type": "browser_page", "action": "fill", "selector": "#token-count", "text": text}
                self.assertEqual(handler.normalize_call(raw)["text"], text)
        for text in (None, 0, False, "x" * 501, "a\x00b"):
            raw = {"type": "browser_page", "action": "fill", "selector": "#token-count", "text": text}
            self.assertIsNone(handler.normalize_call(raw))
            self.assertTrue(handler.argument_error(raw))
        raw = {"type": "browser_page", "action": "fill", "selector": "#token-count"}
        self.assertIsNone(handler.normalize_call(raw))
        # An explicitly empty canonical field must win over a legacy alias.
        self.assertEqual(handler.normalize_call({**raw, "text": "", "value": "wrong"})["text"], "")

    def test_real_field_value_through_handler_and_runner(self):
        runner = ManagedBrowserPageRunner(headless=True)
        if not runner.is_available():
            self.skipTest("Playwright not installed")
        handler = BrowserPageToolHandler(browser_runner=runner, approval_checker=lambda **kw: True)
        try:
            result = runner.run(action="navigate", url="data:text/html," + quote('<textarea id="token-count"></textarea>'), observation_mode="text")
            self.assertTrue(result.ok, result.reason)
            for text in ("  alpha\n  beta\tend  ", "Documentation example: token=example", ""):
                observed = runner.run(action="snapshot", observation_mode="text")
                call = handler.normalize_call({"type": "browser_page", "action": "fill", "selector": "#token-count",
                    "text": text, "observation_id": observed.observation_id, "observation_mode": "text"})
                result = handler.execute(call=call, context=_make_context())
                value = runner._submit(lambda: runner._page.locator("#token-count").input_value())
                self.assertEqual(value, text, result.followup_context)
        finally:
            runner.shutdown()
