from __future__ import annotations

import unittest

from companion_v01.finance import FinanceNewsModerationClient
from services.market_data import PublicNewsItem


def _item(item_id: str, title: str):
    return PublicNewsItem(
        adapter_id="eastmoney_fast_news",
        item_id=item_id,
        title=title,
        summary="摘要",
        published_at=100,
        fetched_at=101,
        source="东方财富",
        url="https://finance.eastmoney.com/a/test.html",
    )


class FakeLLMRuntime:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call_chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FinanceNewsModerationClientTests(unittest.TestCase):
    def test_structured_decisions_allow_foreign_and_block_domestic(self) -> None:
        runtime = FakeLLMRuntime(
            {
                "decisions": [
                    {"item_id": "foreign", "decision": "allow", "reason": "foreign", "confidence": 0.9},
                    {"item_id": "domestic", "decision": "block", "reason": "domestic", "confidence": 0.95},
                ]
            }
        )

        result = FinanceNewsModerationClient(runtime).moderate_items(
            (_item("foreign", "特朗普发表讲话"), _item("domestic", "国内重要政治活动"))
        )

        self.assertTrue(result["foreign"].allowed)
        self.assertFalse(result["domestic"].allowed)
        self.assertEqual(runtime.calls[0]["temperature"], 0.0)

    def test_missing_or_invalid_model_output_blocks(self) -> None:
        result = FinanceNewsModerationClient(FakeLLMRuntime({"decisions": []})).moderate_items(
            (_item("missing", "一条无法分类的新闻"),)
        )

        self.assertFalse(result["missing"].allowed)
        self.assertEqual(result["missing"].reason, "missing_moderation_decision")


if __name__ == "__main__":
    unittest.main()
