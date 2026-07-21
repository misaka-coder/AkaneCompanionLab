from __future__ import annotations

import unittest

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_builder import _should_include_generated_file_context


class EngineVisibleContextExclusionTests(unittest.TestCase):
    def test_build_extra_context_audit_sections_keeps_order_and_drops_empty(self) -> None:
        sections = AkaneMemoryEngine._build_extra_context_audit_sections(
            [
                ("client_mode", " stable "),
                ("", "ignored"),
                ("empty", ""),
                ("number", 123),
                ("turn_extra_context", "dynamic"),
            ]
        )

        self.assertEqual(
            sections,
            [
                {"name": "client_mode", "text": "stable"},
                {"name": "number", "text": "123"},
                {"name": "turn_extra_context", "text": "dynamic"},
            ],
        )

    def test_split_history_records_only_peels_matching_current_user_message(self) -> None:
        history, current = AkaneMemoryEngine._split_history_records(
            recent_raw=[
                {"role": "assistant", "content": "上一句", "timestamp": 1712400000},
                {"role": "user", "content": "你好", "timestamp": 1712400060},
            ],
            user_message="你好",
            now_ts=1712400060,
        )

        self.assertEqual([item["content"] for item in history], ["上一句"])
        self.assertEqual(current["content"], "你好")

    def test_split_history_records_keeps_non_user_tail_for_tool_followup(self) -> None:
        history, current = AkaneMemoryEngine._split_history_records(
            recent_raw=[
                {"role": "user", "content": "查一下天气", "timestamp": 1712400000},
                {"role": "assistant", "content": "我看看。", "timestamp": 1712400001},
                {"role": "npc:Weather", "content": "天气晴朗。", "timestamp": 1712400002},
            ],
            user_message="查一下天气",
            now_ts=1712400000,
        )

        self.assertEqual([item["content"] for item in history], ["查一下天气", "我看看。", "天气晴朗。"])
        self.assertEqual(current["content"], "查一下天气")

    def test_build_history_turns_preserves_time_and_speaker_labels(self) -> None:
        turns = AkaneMemoryEngine._build_history_turns(
            [
                {
                    "role": "user",
                    "content": "你好",
                    "timestamp": 1712400000,
                    "_akane_prompt_user_content": "OLD ENVELOPE MUST STAY OUT",
                },
                {"role": "assistant", "content": "在哦", "timestamp": 1712400060},
                {"role": "npc:Weather", "content": "天气晴朗。", "timestamp": 1712400120},
            ]
        )

        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])
        self.assertIn("[2024-04-06 18:40]", turns[0]["content"])
        self.assertIn("User: 你好", turns[0]["content"])
        self.assertNotIn("OLD ENVELOPE", turns[0]["content"])
        self.assertIn("Akane: 在哦", turns[1]["content"])
        self.assertIn("Weather: 天气晴朗。", turns[2]["content"])

    def test_external_event_uses_same_rendering_as_current_and_later_history(self) -> None:
        event = {
            "source_id": "event-1",
            "role": "event.finance",
            "content": (
                "source: 东方财富\n"
                "published_at: 2026-07-20T08:52:00+08:00\n"
                "title: 提振消费政策评论\n"
                "summary: 关注收入、就业、财政和金融支持。\n"
                "url: https://finance.eastmoney.com/example.html"
            ),
            "timestamp": 1_784_512_320,
            "memory_metadata": {"categories": ["event_trace"]},
        }

        history, current = AkaneMemoryEngine._split_history_records(
            recent_raw=[event],
            user_message=event["content"],
            now_ts=event["timestamp"],
        )
        current_text = AkaneMemoryEngine._render_memory_record_for_prompt(current)
        later_history_text = AkaneMemoryEngine._build_history_turns([event])[0]["content"]

        self.assertEqual(history, [])
        self.assertEqual(current_text, later_history_text)
        self.assertIn("event.finance\nsource: 东方财富", current_text)
        self.assertNotIn("User:", current_text)

    def test_collect_visible_context_source_ids_excludes_only_directly_visible_records(self) -> None:
        visible_ids = AkaneMemoryEngine._collect_visible_context_source_ids(
            recent_raw=[
                {"source_id": "raw-1"},
                {"source_id": "current-user"},
                {"source_id": "raw-1"},
            ],
            recent_episodic_summaries=[
                {
                    "summary_id": "summary-1",
                    "source_id": "summary-1",
                    "source_ids": ["raw-summarized-1", "raw-summarized-2"],
                },
                {
                    "summary_id": "summary-2",
                    "source_ids": ["raw-summarized-3"],
                },
            ],
            recent_semantic_summaries=[
                {
                    "semantic_id": "semantic-1",
                    "source_id": "semantic-1",
                    "source_summary_ids": ["summary-semanticized-1"],
                },
                {
                    "semantic_id": "semantic-2",
                    "source_summary_ids": ["summary-semanticized-2"],
                },
            ],
            extra_source_ids=["current-user", "", "manual-extra"],
        )

        self.assertEqual(
            visible_ids,
            [
                "current-user",
                "manual-extra",
                "raw-1",
                "summary-1",
                "summary-2",
                "semantic-1",
                "semantic-2",
            ],
        )
        self.assertNotIn("raw-summarized-1", visible_ids)
        self.assertNotIn("raw-summarized-2", visible_ids)
        self.assertNotIn("raw-summarized-3", visible_ids)
        self.assertNotIn("summary-semanticized-1", visible_ids)
        self.assertNotIn("summary-semanticized-2", visible_ids)

    def test_qq_generated_file_context_only_appears_for_file_intent(self) -> None:
        qq_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        desktop_context = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
        )

        self.assertFalse(
            _should_include_generated_file_context(
                qq_context,
                "用户刚刚发送了一张图片。请只根据【本轮 QQ 图片内容】中的视觉摘要自然回应。",
            )
        )
        self.assertTrue(_should_include_generated_file_context(qq_context, "把 gen_001 发给我"))
        self.assertTrue(_should_include_generated_file_context(qq_context, "帮我转成 mp3"))
        self.assertTrue(_should_include_generated_file_context(desktop_context, "普通聊天"))


if __name__ == "__main__":
    unittest.main()
