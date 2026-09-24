import json
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from companion_v01.browser_page_runtime import ManagedBrowserPageRunner
from companion_v01.tool_handlers.web_browser import BrowserPageToolHandler
from tests.test_browser_page_hybrid_observation import _make_context
from tests.test_execution_resources import _Harness


class BrowserExtendedActionsTests(unittest.TestCase):
    def setUp(self):
        self.runner = ManagedBrowserPageRunner(headless=True, timeout_ms=3000)
        if not self.runner.is_available(): self.skipTest("Playwright unavailable")
        self.addCleanup(self.runner.shutdown)
        self.handler = BrowserPageToolHandler(browser_runner=self.runner, approval_checker=lambda **kw: True)
        self.context = _make_context()

    def page(self, html):
        result = self.runner.run(action="navigate", url="data:text/html," + quote(html), observation_mode="text")
        self.assertTrue(result.ok, result.reason)
        return result

    def execute(self, action, **args):
        observed = self.runner.run(action="snapshot", observation_mode="text",
                                   options={"frame_selector": args["frame_selector"]} if args.get("frame_selector") else {})
        self.assertTrue(observed.ok, observed.reason)
        call = self.handler.normalize_call({"type": "browser_page", "action": action,
            "observation_id": observed.observation_id, "observation_mode": "text", **args})
        self.assertIsNotNone(call, args)
        return self.handler.execute(call=call, context=self.context)

    def test_form_batch_exact_values_and_checkbox(self):
        self.page('<textarea id="text"></textarea><input id="check" type="checkbox"><select id="pick"><option value="a">A</option><option value="b">B</option></select>')
        result = self.execute("run_actions", actions=[
            {"action": "fill", "selector": "#text", "text": "  sample\nline  "},
            {"action": "set_checked", "selector": "#check", "checked": True},
            {"action": "select_option", "selector": "#pick", "values": ["b"]},
            {"action": "hover", "selector": "#pick"},
        ])
        state = self.runner._submit(lambda: self.runner._page.evaluate("({text:document.querySelector('#text').value,checked:document.querySelector('#check').checked,pick:document.querySelector('#pick').value})"))
        self.assertEqual(state, {"text": "  sample\nline  ", "checked": True, "pick": "b"}, result.followup_context)
        self.assertIn('"index": 3', result.followup_context)

    def test_scoped_frame_read_and_control_and_stale_scope(self):
        self.page('<div>OUTSIDE</div><iframe id="frame" srcdoc="&lt;section id=part&gt;&lt;input id=inside&gt;INSIDE&lt;/section&gt;"></iframe>')
        read = self.runner.run(action="read_text", options={"frame_selector": "#frame", "scope_selector": "#part"})
        self.assertTrue(read.ok, read.reason)
        self.assertIn("INSIDE", read.text)
        self.assertNotIn("OUTSIDE", read.text)
        self.execute("fill", selector="#inside", text="framed", frame_selector="#frame")
        self.assertEqual(self.runner._submit(lambda: self.runner._page.frame_locator("#frame").locator("#inside").input_value()), "framed")
        main = self.runner.run(action="snapshot")
        stale = self.runner.run(action="fill", selector="#inside", text="wrong", observation_id=main.observation_id, options={"frame_selector":"#frame"})
        self.assertFalse(stale.ok)
        self.assertEqual(stale.action_state, "not_started")

    def test_dialog_policy_is_one_action_and_batch_stops(self):
        self.page('<button id="prompt" onclick="document.querySelector(\'#value\').textContent=prompt(\'value?\')">Prompt</button><div id="value"></div><input id="after">')
        result = self.execute("run_actions", actions=[
            {"action":"click", "selector":"#prompt", "dialog":{"action":"accept", "prompt_text":"  exact  "}},
            {"action":"fill", "selector":"#after", "text":"must not run"}])
        self.assertIn("batch_stopped_for_new_observation", result.followup_context)
        self.assertNotIn('"index": 1', result.followup_context)
        self.assertEqual(self.runner._submit(lambda:self.runner._page.locator("#value").text_content()), "  exact  ")
        self.assertEqual(self.runner._submit(lambda:self.runner._page.locator("#after").input_value()), "")
        self.execute("click", selector="#prompt")
        self.assertEqual(self.runner._dialog_events[-1]["response"], "dismiss")

    def test_batch_preserves_progress_after_failure(self):
        self.page('<input id="first"><input id="last">')
        result = self.execute("run_actions", actions=[{"action":"fill","selector":"#first","text":"done"},
            {"action":"click","selector":"#missing"}, {"action":"fill","selector":"#last","text":"wrong"}])
        self.assertIn('"index": 0', result.followup_context)
        self.assertIn('"index": 1', result.followup_context)
        self.assertNotIn('"index": 2', result.followup_context)
        self.assertEqual(self.runner._submit(lambda:self.runner._page.locator("#first").input_value()), "done")
        self.assertEqual(self.runner._submit(lambda:self.runner._page.locator("#last").input_value()), "")

    def test_upload_existing_resource_with_session_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(Path(directory))
            item = harness.register_input(profile_user_id="p1", session_id="s1", content="synthetic upload")
            self.handler.generated_file_service = harness.generated_service
            self.page('<input type="file" id="upload">')
            result = self.execute("upload", selector="#upload", files=[item["attachment_handle"]])
            files = self.runner._submit(lambda:self.runner._page.locator("#upload").evaluate("el=>Array.from(el.files,f=>({name:f.name,size:f.size}))"))
            self.assertEqual(files, [{"name":"source.txt", "size":16}], result.followup_context)
            paths, reason = self.handler._resolve_upload_files([item["attachment_handle"]], _make_context(session_id="other"))
            self.assertEqual(paths, [])
            self.assertTrue(reason)

    def test_backend_facts_and_specific_rejection(self):
        for source in ("managed", "personal_chrome"):
            call = self.handler.normalize_call({"type":"browser_page", "action":"capabilities", "session_source":source})
            facts = json.loads(self.handler.execute(call=call, context=self.context).followup_context)
            self.assertEqual(facts["backend"], source)
            self.assertEqual("upload" in facts["actions"], source == "managed")
        raw = {"type":"browser_page", "session_source":"personal_chrome", "action":"upload", "files":["file_1"]}
        self.assertIsNone(self.handler.normalize_call(raw))
        self.assertIn("尚未支持", self.handler.argument_error(raw))


class BrowserShutdownTests(unittest.TestCase):
    def test_slow_repeated_shutdown_closes_once_on_owner_thread(self):
        release, closed = threading.Event(), threading.Event()
        owners = []
        class Runner(ManagedBrowserPageRunner):
            def _close_objects(self):
                owners.append(threading.get_ident())
                closed.set()
        runner = Runner()
        runner._executor = ThreadPoolExecutor(max_workers=1)
        worker_id = runner._executor.submit(threading.get_ident).result()
        runner._executor.submit(release.wait, 3)
        try:
            runner.shutdown(timeout=.01)
            runner.shutdown(timeout=.01)
            self.assertFalse(closed.is_set())
            with self.assertRaisesRegex(RuntimeError, "browser_runner_shutdown"):
                runner._submit(lambda: None)
        finally:
            release.set()
        self.assertTrue(closed.wait(2))
        self.assertEqual(owners, [worker_id])
