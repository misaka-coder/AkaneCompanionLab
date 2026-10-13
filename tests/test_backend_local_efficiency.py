"""Local optimizations preserve estimates, session writes, and public contracts."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import patch

from capcore import CapabilityToolSpec

from companion_v01.capability_contracts import contract_snapshot
from companion_v01.memcore_integration.adapters import build_akane_token_counter
from companion_v01.model_runtime.prompt_audit import PromptAuditMixin
from companion_v01.store import MemoryStore
from companion_v01.text_utils import estimate_prompt_tokens


class PromptEstimateCompatibilityTests(unittest.TestCase):
    def test_audit_and_compaction_preserve_unicode_boundaries_and_rounding(self):
        audit = PromptAuditMixin()
        counter = build_akane_token_counter()
        self.assertEqual(counter.quality, "estimated")
        cases = [(None, 0), ("", 0), ("abc", 1), ("abcd", 1), ("abcde", 2),
                 ("你好", 2), ("你ab好c", 3), ("あア😀", 1),
                 ("\u4dff\u4e00\u9fff\ua000", 3), ("\ud800\udfff\x00\n", 1)]
        # Every Unicode code point, including surrogates, is a character here;
        # only the original U+4E00..U+9FFF block receives the CJK weight.
        all_characters = "".join(map(chr, range(0x110000)))
        cjk_count = 0xA000 - 0x4E00
        cases.append((all_characters, cjk_count + (0x110000 - cjk_count + 3) // 4))
        for text, expected in cases:
            with self.subTest(chars=len(text or "")):
                self.assertEqual(estimate_prompt_tokens(text), expected)
                self.assertEqual(audit._estimate_prompt_tokens(text), expected)
                self.assertEqual(counter.count_text(text), expected)

    def test_published_spec_stays_detached_from_the_live_schema_and_future_snapshots(self):
        schema = {"type": "object", "properties": {"mode": {"enum": ["quiet", "normal"]}}}
        spec = CapabilityToolSpec(capability_id="demo.test", display_name="Demo", description="Demo",
            input_schema=schema, risk="low", confirm="never", effects=(), visible_in=("desktop_pet",))
        latest = [spec]
        handler = SimpleNamespace(tool_spec=lambda: latest[0], policy_accepted_native_tool=True)
        first = contract_snapshot(handler, scope="one")
        schema["properties"]["mode"]["enum"].append("new")
        latest[0] = replace(spec, input_schema=schema, schema_hash="")
        self.assertEqual(first["spec"]["input_schema"]["properties"]["mode"]["enum"], ["quiet", "normal"])
        second = contract_snapshot(handler, scope="one")
        self.assertNotEqual(first["contract_ref"], second["contract_ref"])
        first["spec"]["input_schema"]["properties"]["mode"]["enum"].clear()
        self.assertEqual(second["spec"]["input_schema"]["properties"]["mode"]["enum"], ["quiet", "normal", "new"])


class SessionTouchCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = MemoryStore(Path(temporary.name))
        self.scope = {"profile_user_id": "owner", "session_id": "session", "character_pack_id": "cecilia"}
        self.original = self.store.ensure_session(**self.scope, display_title="会话", timestamp=100)

    def test_identical_touches_are_read_only_and_preserve_character_ownership(self):
        verbs = []
        connect = self.store._connect

        @contextmanager
        def traced():
            with connect() as conn:
                conn.set_trace_callback(lambda statement: verbs.append(statement.split()[0]))
                yield conn

        with patch.object(self.store, "_connect", traced):
            for pack, title in (("cecilia", None), ("", "会话"), ("another", None)):
                touched = self.store.ensure_session(**{**self.scope, "character_pack_id": pack},
                    display_title=title, timestamp=100)
                self.assertEqual(touched, self.original)
        self.assertNotIn("UPDATE", verbs)
        self.assertNotIn("INSERT", verbs)

    def test_real_changes_keep_titles_timestamps_focus_and_later_reload(self):
        focused = self.store.set_session_gift_focus(profile_user_id="owner", session_id="session",
            asset_id="gift", timestamp=100)
        renamed = self.store.ensure_session(**self.scope, display_title="新会话", timestamp=100)
        self.assertEqual(renamed["current_gift_focus_asset_id"], focused["current_gift_focus_asset_id"])
        self.assertEqual(renamed["display_title"], "新会话")
        later = self.store.ensure_session(**self.scope, timestamp=101)
        self.assertEqual(later["updated_at"], 101)
        earlier = self.store.ensure_session(**self.scope, timestamp=99)
        self.assertEqual(earlier["updated_at"], 99)
        self.assertEqual(self.store.get_session("owner", "session"), earlier)

    def test_failed_real_write_keeps_the_committed_session_retryable(self):
        connect = self.store._connect

        @contextmanager
        def read_only():
            with connect() as conn:
                conn.execute("PRAGMA query_only = ON")
                yield conn

        with patch.object(self.store, "_connect", read_only):
            self.assertEqual(self.store.ensure_session(**self.scope, timestamp=100), self.original)
            with self.assertRaises(sqlite3.OperationalError):
                self.store.ensure_session(**self.scope, display_title="Changed", timestamp=101)
        self.assertEqual(self.store.get_session("owner", "session"), self.original)
        self.assertEqual(self.store.ensure_session(**self.scope, timestamp=101)["updated_at"], 101)


if __name__ == "__main__":
    unittest.main()
