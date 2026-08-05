from __future__ import annotations

import unittest

from companion_v01.tool_runtime import ToolExecutionContext
from scripts.tools.run_native_web_search_smoke import (
    SmokeBrowseMemoryHandler,
    SmokeCheckInventoryHandler,
    SmokeInspectMediaInfoHandler,
    SmokeListRemindersHandler,
    SmokeOpenMemoryHandler,
    SmokeReadMemoryTimelineHandler,
    SmokeRetrieveMemoryHandler,
    _build_smoke_tools,
    default_message_for_toolset,
    toolset_allowlist,
)


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="smoke_user",
        session_id="smoke_session",
        now_ts=0,
        visual_payload={},
    )


class NativeToolSmokeHelpersTests(unittest.TestCase):
    def test_toolset_allowlist_maps_each_family(self) -> None:
        self.assertEqual(toolset_allowlist("web_search"), "web_search")
        self.assertEqual(
            toolset_allowlist("memory"),
            "retrieve_memory,browse_memory,read_memory_timeline,open_memory",
        )
        # `all` mirrors the shipped default native allowlist (the full set that
        # fires under native-first).
        self.assertEqual(
            set(toolset_allowlist("all").split(",")),
            {
                "web_search",
                "retrieve_memory",
                "browse_memory",
                "read_memory_timeline",
                "open_memory",
                "list_reminders",
                "check_inventory",
                "inspect_media_info",
            },
        )
        # Unknown / empty falls back to web_search (fail-safe to existing behavior).
        self.assertEqual(toolset_allowlist(""), "web_search")
        self.assertEqual(toolset_allowlist("bogus"), "web_search")

    def test_default_message_is_toolset_appropriate(self) -> None:
        self.assertIn("天气", default_message_for_toolset("web_search"))
        self.assertIn("咖啡", default_message_for_toolset("memory"))

    def test_build_smoke_tools_memory_exposes_only_memory_tools(self) -> None:
        handlers, selection = _build_smoke_tools(
            engine=None,  # memory branch never touches the engine
            toolset="memory",
            real_web_search=False,
            base_dir=None,
        )
        self.assertEqual(
            set(handlers.keys()),
            {"retrieve_memory", "browse_memory", "read_memory_timeline", "open_memory"},
        )
        self.assertEqual(
            selection.tool_names,
            ("retrieve_memory", "browse_memory", "read_memory_timeline", "open_memory"),
        )
        self.assertNotIn("web_search", selection.tool_names)

    def test_build_smoke_tools_all_exposes_full_default_allowlist(self) -> None:
        from pathlib import Path

        handlers, selection = _build_smoke_tools(
            engine=None,  # not touched when real_web_search is False
            toolset="all",
            real_web_search=False,
            base_dir=Path("."),
        )
        expected = {
            "web_search",
            "retrieve_memory",
            "browse_memory",
            "read_memory_timeline",
            "open_memory",
            "list_reminders",
            "check_inventory",
            "inspect_media_info",
        }
        self.assertEqual(set(handlers.keys()), expected)
        self.assertEqual(set(selection.tool_names), expected)
        # No write/control tool leaked into the smoke selection.
        self.assertNotIn("send_file", selection.tool_names)
        self.assertNotIn("compose_file", selection.tool_names)


class SmokeMemoryHandlerTests(unittest.TestCase):
    def test_retrieve_memory_fixture_executes_and_records(self) -> None:
        handler = SmokeRetrieveMemoryHandler()
        normalized = handler.normalize_call({"type": "retrieve_memory", "query": "我最喜欢的咖啡"})
        self.assertIsNotNone(normalized)

        result = handler.execute(call=normalized, context=_context())

        self.assertEqual(result.tool_type, "retrieve_memory")
        self.assertEqual(result.stream_events[0]["type"], "retrieve_memory_completed")
        self.assertEqual(result.stream_events[0]["status"], "ok")
        self.assertTrue(result.followup_context.strip())
        self.assertEqual(handler.executed_calls[0]["query"], "我最喜欢的咖啡")
        # Internal native metadata must not be recorded as a tool argument.
        self.assertNotIn("_tool_source", handler.executed_calls[0])

    def test_read_memory_timeline_fixture_executes_and_records(self) -> None:
        handler = SmokeReadMemoryTimelineHandler()
        normalized = handler.normalize_call(
            {
                "type": "read_memory_timeline",
                "date_from": "2026-06-01",
                "date_to": "2026-06-01",
                "time_periods": ["morning"],
            }
        )
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["time_periods"], ["morning"])

        result = handler.execute(call=normalized, context=_context())

        self.assertEqual(result.tool_type, "read_memory_timeline")
        self.assertEqual(result.stream_events[0]["type"], "read_memory_timeline_completed")
        self.assertEqual(result.stream_events[0]["status"], "ok")
        self.assertEqual(handler.executed_calls[0]["date_from"], "2026-06-01")

    def test_open_memory_fixture_executes_and_records(self) -> None:
        handler = SmokeOpenMemoryHandler()
        normalized = handler.normalize_call(
            {"type": "open_memory", "memory_id": "episode_001", "view": "content"}
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None

        result = handler.execute(call=normalized, context=_context())

        self.assertEqual(result.tool_type, "open_memory")
        self.assertEqual(result.stream_events[0]["type"], "open_memory_completed")
        self.assertEqual(handler.executed_calls[0]["memory_id"], "episode_001")
        self.assertIn("固定记忆证据", result.followup_context)

    def test_browse_memory_fixture_executes_and_records(self) -> None:
        handler = SmokeBrowseMemoryHandler()
        normalized = handler.normalize_call(
            {"type": "browse_memory", "date_from": "2026-07-20", "date_to": "2026-07-24"}
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None

        result = handler.execute(call=normalized, context=_context())

        self.assertEqual(result.tool_type, "browse_memory")
        self.assertEqual(result.stream_events[0]["type"], "browse_memory_completed")
        self.assertEqual(handler.executed_calls[0]["date_from"], "2026-07-20")
        self.assertIn("episode_001", result.followup_context)


class SmokeReadTierHandlerTests(unittest.TestCase):
    """The reminder/inventory/media fixtures must be fully canned (no real store)."""

    def test_list_reminders_fixture_is_canned(self) -> None:
        handler = SmokeListRemindersHandler()
        normalized = handler.normalize_call({"type": "list_reminders", "status": "pending"})
        self.assertIsNotNone(normalized)
        result = handler.execute(call=normalized, context=_context())
        self.assertEqual(result.tool_type, "list_reminders")
        self.assertEqual(result.stream_events[0]["type"], "reminder_list")
        self.assertTrue(result.followup_context.strip())
        self.assertEqual(handler.executed_calls[0]["status"], "pending")
        self.assertNotIn("_tool_source", handler.executed_calls[0])

    def test_check_inventory_fixture_is_canned(self) -> None:
        handler = SmokeCheckInventoryHandler()
        normalized = handler.normalize_call({"type": "check_inventory", "scope": "pending_recent"})
        self.assertIsNotNone(normalized)
        result = handler.execute(call=normalized, context=_context())
        self.assertEqual(result.tool_type, "check_inventory")
        self.assertEqual(result.stream_events[0]["type"], "inventory_snapshot")
        self.assertTrue(result.followup_context.strip())
        self.assertEqual(handler.executed_calls[0]["scope"], "pending_recent")

    def test_inspect_media_info_fixture_is_canned(self) -> None:
        handler = SmokeInspectMediaInfoHandler()
        # source_id is required; normalize returns None without it.
        self.assertIsNone(handler.normalize_call({"type": "inspect_media_info"}))
        normalized = handler.normalize_call({"type": "inspect_media_info", "source_id": "audio_001"})
        self.assertIsNotNone(normalized)
        result = handler.execute(call=normalized, context=_context())
        self.assertEqual(result.tool_type, "inspect_media_info")
        self.assertEqual(result.stream_events[0]["type"], "media_info_inspected")
        self.assertEqual(result.stream_events[0]["source_id"], "audio_001")
        self.assertTrue(result.followup_context.strip())


if __name__ == "__main__":
    unittest.main()
