"""Managed page candidates and shutdown must preserve their execution boundaries."""
import threading
import time
import unittest
from urllib.parse import quote

from companion_v01.browser_page_runtime import ManagedBrowserPageRunner, ManagedBrowserSessionManager


class _Element:
    def __init__(self, href, label="Account"):
        self.href = href
        self.label = label

    def is_visible(self, **kwargs):
        return True

    def evaluate(self, script):
        return "a"

    def get_attribute(self, name):
        return self.href if name == "href" else ""

    def inner_text(self, **kwargs):
        return self.label


class _Locator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class _Page:
    url = "https://public.example/account/"

    def __init__(self, *, elements=(), candidates=(), url=None):
        self.elements = elements
        self.candidates = candidates
        self.closed = False
        if url is not None:
            self.url = url

    def locator(self, selector):
        return _Locator(self.elements)

    def evaluate(self, script, options):
        return self.candidates

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def close(self):
        self.closed = True


class ManagedBrowserUrlBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.runner = ManagedBrowserPageRunner()
        self.addCleanup(self.runner.shutdown)

    def test_candidate_urls_reject_embedded_credentials(self):
        for url in (
            "https://user:SYNTHETIC_PASS@public.example/",
            "https://user@public.example/",
            "https://:SYNTHETIC_PASS@public.example/",
            "https://@public.example/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.runner._safe_public_candidate_url(url, base_url=""), "")

    def test_candidate_urls_reject_invalid_ports_without_raising(self):
        for url in (
            "https://public.example:invalid/path",
            "https://public.example:65536/path",
            "https://public.example:-1/path",
            "https://[broken/path",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.runner._safe_public_candidate_url(url, base_url=""), "")

    def test_public_relative_candidates_preserve_valid_query_and_port(self):
        self.assertEqual(
            self.runner._safe_public_candidate_url("../docs?page=2", base_url="https://public.example:8443/account/"),
            "https://public.example:8443/docs?page=2",
        )

    def test_private_network_override_does_not_allow_credentials_or_bad_ports(self):
        self.runner.allow_private_network_urls = True
        self.assertEqual(self.runner._safe_public_candidate_url("http://127.0.0.1:8080/page", base_url=""),
                         "http://127.0.0.1:8080/page")
        for url in ("http://user:SYNTHETIC_PASS@127.0.0.1/page", "http://127.0.0.1:invalid/page"):
            with self.subTest(url=url):
                self.assertEqual(self.runner._safe_public_candidate_url(url, base_url=""), "")

    def test_invalid_candidates_do_not_shift_source_element_indices(self):
        page = _Page(candidates=[
            {"href": "https://user:SYNTHETIC_PASS@public.example/", "title": "Credential", "sourceIndex": 0},
            {"href": "https://public.example:invalid/", "title": "Invalid", "sourceIndex": 1},
            {"href": "../docs", "title": "Docs", "sourceIndex": 2},
        ])
        self.assertEqual(self.runner._safe_link_candidate_rows(page, max_items=10), [
            {"title": "Docs", "url": "https://public.example/docs", "kind": "link", "source_index": 2},
        ])

    def test_element_summary_keeps_label_without_exposing_unsafe_href(self):
        for url in ("https://user:SYNTHETIC_PASS@public.example/", "https://public.example/?token=SYNTHETIC_PASS"):
            with self.subTest(url=url):
                page = _Page(elements=[_Element(url)])
                self.assertEqual(self.runner._safe_element_summary(page, element_limit=10), "1. a: Account")

    def test_element_summary_does_not_use_unsafe_href_as_label(self):
        page = _Page(elements=[_Element("https://user:SYNTHETIC_PASS@public.example/", label="")])
        self.assertEqual(self.runner._safe_element_summary(page, element_limit=10), "1. a")

    def test_element_summary_preserves_safe_relative_href(self):
        page = _Page(elements=[_Element("../docs", label="Docs")])
        self.assertEqual(self.runner._safe_element_summary(page, element_limit=10), "1. a: Docs (../docs)")

    def test_rejected_popup_preserves_executed_receipt_and_source_page(self):
        source = _Page()
        popup = _Page(url="https://user:SYNTHETIC_PASS@public.example/")
        self.runner._page = source
        self.runner._action_state = "executed"
        with self.assertRaisesRegex(RuntimeError, "popup_url_rejected"):
            self.runner._adopt_popup_page(source, popup)
        self.assertTrue(popup.closed)
        self.assertIs(self.runner._page, source)
        self.assertEqual(self.runner._action_state, "executed")


class ManagedBrowserShutdownBoundaryTests(unittest.TestCase):
    def test_bounded_shutdown_keeps_all_queued_session_cleanup(self):
        release = threading.Event()
        completed = threading.Event()
        cleaned = []
        lock = threading.Lock()

        class Runner:
            def shutdown(self):
                release.wait(3)
                with lock:
                    cleaned.append(threading.get_ident())
                    if len(cleaned) == 8:
                        completed.set()

        manager = ManagedBrowserSessionManager(runner_factory=Runner, max_sessions=8)
        for index in range(8):
            manager.runner_for_session("profile", f"session-{index}")
        started = time.monotonic()
        try:
            manager.shutdown(timeout=.1)
            self.assertLess(time.monotonic() - started, 2)
            self.assertFalse(completed.is_set())
            manager.shutdown(timeout=.1)
        finally:
            release.set()
        self.assertTrue(completed.wait(2), "Queued sessions lost their cleanup after the bounded wait")
        self.assertEqual(len(cleaned), 8)
        self.assertNotIn(threading.get_ident(), cleaned)


class ManagedBrowserLiveBoundaryTests(unittest.TestCase):
    def runner(self, runner_type=ManagedBrowserPageRunner):
        runner = runner_type(headless=True, timeout_ms=3000)
        if not runner.is_available():
            self.skipTest("Playwright unavailable")
        self.addCleanup(runner.shutdown)
        return runner

    def test_malformed_page_links_do_not_abort_observation(self):
        runner = self.runner()
        html = ('<a href="https://[broken/path">Malformed</a>'
                '<a href="https://user:SYNTHETIC_PASS@public.example/">Account</a>'
                '<a href="https://public.example/docs">Docs</a>')
        navigation = runner.run(action="navigate", url="data:text/html," + quote(html), observation_mode="text")
        self.assertTrue(navigation.ok, navigation.reason)
        self.assertEqual(navigation.observation_state, "complete")
        elements = runner.run(action="elements", observation_mode="text")
        self.assertTrue(elements.ok, elements.reason)
        self.assertEqual(elements.observation_state, "complete")
        self.assertIn("Account", elements.text)
        self.assertNotIn("SYNTHETIC_PASS", elements.text)
        observation = runner.get_observation(elements.observation_id)
        self.assertEqual(observation.element_candidates, [
            {"title": "Docs", "url": "https://public.example/docs", "kind": "link", "source_index": 2},
        ])

    def test_observation_failure_after_click_does_not_replay_input(self):
        class Runner(ManagedBrowserPageRunner):
            fail_observation = False

            def _safe_page_snapshot(self, page, *, max_chars):
                if self.fail_observation:
                    raise RuntimeError("Target page, context or browser has been closed")
                return super()._safe_page_snapshot(page, max_chars=max_chars)

        runner = self.runner(Runner)
        html = '<script>window.hits=0</script><button id="target" onclick="window.hits++">Click</button>'
        navigation = runner.run(action="navigate", url="data:text/html," + quote(html), observation_mode="text")
        self.assertTrue(navigation.ok, navigation.reason)
        runner.fail_observation = True
        clicked = runner.run(action="click", selector="#target", observation_id=navigation.observation_id,
                             observation_mode="text", options={"dialog": {"action": "dismiss"}})
        self.assertTrue(clicked.ok, clicked.reason)
        self.assertEqual(clicked.action_state, "executed")
        self.assertEqual(clicked.observation_state, "failed")
        self.assertEqual(clicked.next_action, "snapshot")
        self.assertEqual(runner._submit(lambda: runner._page.evaluate("window.hits")), 1)
        self.assertIsNone(runner._dialog_policy)


if __name__ == "__main__":
    unittest.main()
