from __future__ import annotations

import unittest
from datetime import datetime

from companion_v01.text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    render_chat_timeline,
    resolve_reminder_due_timestamp,
    tokenize,
)


class TextUtilsTests(unittest.TestCase):
    def test_tokenize_expands_long_chinese_terms(self) -> None:
        tokens = tokenize("糖葫芦摊 上课")
        self.assertIn("糖葫芦摊", tokens)
        self.assertIn("糖葫", tokens)
        self.assertIn("葫芦摊", tokens)
        self.assertIn("上课", tokens)

    def test_extract_semantic_tags_prefers_repeated_terms(self) -> None:
        tags = extract_semantic_tags("集市 集市 糖葫芦摊 上课", limit=5)
        self.assertGreaterEqual(len(tags), 3)
        self.assertEqual(tags[0], "集市")
        self.assertIn("上课", tags)

    def test_detect_time_of_day_from_text_supports_cn_and_en(self) -> None:
        self.assertEqual(detect_time_of_day_from_text("今晚早点休息"), "night")
        self.assertEqual(detect_time_of_day_from_text("tomorrow morning"), "morning")
        self.assertIsNone(detect_time_of_day_from_text("只是普通闲聊"))

    def test_render_chat_timeline_groups_rows_by_date(self) -> None:
        timeline = render_chat_timeline(
            [
                {"role": "user", "content": "你好", "timestamp": 1714543200},
                {"role": "assistant", "content": "欢迎回来", "timestamp": 1714543260},
            ]
        )
        self.assertIn("[日期", timeline)
        self.assertIn("User: 你好", timeline)
        self.assertIn("Akane: 欢迎回来", timeline)

    def test_resolve_reminder_due_timestamp_supports_relative_cn_time(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 0, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="明天晚上八点提醒我复习",
        )
        self.assertIsNotNone(due_ts)
        self.assertEqual(int(due_ts), int(datetime(2024, 4, 11, 20, 0).timestamp()))

    def test_resolve_reminder_due_timestamp_keeps_plain_clock_on_same_day_when_future(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 6, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="8点提醒我出门",
        )
        self.assertIsNotNone(due_ts)
        self.assertEqual(int(due_ts), int(datetime(2024, 4, 10, 8, 0).timestamp()))

    def test_resolve_reminder_due_timestamp_rolls_plain_clock_to_next_day_when_past(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 21, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="8点提醒我出门",
        )
        self.assertIsNotNone(due_ts)
        self.assertEqual(int(due_ts), int(datetime(2024, 4, 11, 8, 0).timestamp()))

    def test_resolve_reminder_due_timestamp_rejects_ambiguous_weekend_phrase(self) -> None:
        due_ts = resolve_reminder_due_timestamp(
            now_ts=int(datetime(2024, 4, 10, 0, 0).timestamp()),
            time_text="周末提醒我收快递",
        )
        self.assertIsNone(due_ts)

    def test_resolve_reminder_due_timestamp_supports_relative_minutes(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 12, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="五分钟后提醒我喝水",
        )
        self.assertEqual(int(due_ts), now_ts + 5 * 60)

    def test_resolve_reminder_due_timestamp_supports_half_hour(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 12, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="半小时后提醒我休息",
        )
        self.assertEqual(int(due_ts), now_ts + 30 * 60)

    def test_resolve_reminder_due_timestamp_prefers_explicit_offset_minutes(self) -> None:
        now_ts = int(datetime(2024, 4, 10, 12, 0).timestamp())
        due_ts = resolve_reminder_due_timestamp(
            now_ts=now_ts,
            time_text="过会儿提醒我站起来",
            offset_minutes=15,
        )
        self.assertEqual(int(due_ts), now_ts + 15 * 60)


if __name__ == "__main__":
    unittest.main()
