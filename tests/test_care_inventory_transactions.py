from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from companion_v01.care_runtime import CareModulePort, CareRuntimeStore
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway


class CareInventoryTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.path = root / "care.json"
        self.store = CareRuntimeStore(self.path)
        self.base = dict(
            profile_user_id="owner",
            character_pack_id="cecilia",
            relation_user_id="qq:12345",
            conversation_id="group-87",
            now_ms=1000,
        )
        self.store.claim_daily_checkin(**self.base, date_key="2026-01-01", coins=40)
        self.store.donate_to_shrine(**self.base, amount=20)
        self.purchase = dict(
            **self.base,
            item_id="receipt_tea",
            item_name="回执茶",
            price=6,
            count=2,
            event_id="purchase-one",
            item_effects={"hunger": 20},
        )
        self.gateway = NapCatQQGateway(state_path=root / "qq.json", default_character_pack_id="cecilia")
        self.context = QQMessageContext(
            True,
            "group_mention",
            is_group=True,
            user_id=12345,
            group_id=87,
            profile_user_id="owner",
            session_id="group-87",
            character_pack_id="cecilia",
            clean_message="/购买 回执茶",
            source_message_id="message-buy-one",
        )
        self.items = [{"id": "receipt_tea", "name": "回执茶", "price": 6, "effects": {"hunger": 20}}]

    def snapshot(self):
        return self.store.snapshot_for_client(**self.base, client_mode="qq_text")

    def command(self, context):
        return self.gateway.handle_economy_command(
            context,
            care_module=CareModulePort(enabled=True, _runtime=self.store),
            shop_items=self.items,
            now_ms=1000,
        )

    def test_purchase_replay_after_reload_does_not_spend_quota_or_add_items(self):
        first = self.store.buy_to_inventory(**self.purchase)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["quota_after"], 8)
        committed = self.path.read_bytes()
        self.store = CareRuntimeStore(self.path)
        replayed = self.store.buy_to_inventory(**self.purchase)
        self.assertEqual(replayed["status"], "duplicate")
        self.assertEqual(self.path.read_bytes(), committed)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["redeem_quota"], 8)
        self.assertEqual(snapshot["inventory"]["receipt_tea"]["count"], 2)

    def test_failed_purchase_save_remains_retryable_with_same_receipt(self):
        committed = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.buy_to_inventory(**self.purchase)
        self.assertEqual(self.path.read_bytes(), committed)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["redeem_quota"], 20)
        self.assertEqual(snapshot["inventory"], {})
        retried = self.store.buy_to_inventory(**self.purchase)
        self.assertEqual(retried["status"], "ok")
        self.assertEqual(retried["quota_after"], 8)
        self.assertEqual(self.store.buy_to_inventory(**self.purchase)["status"], "duplicate")

    def test_new_purchase_receipt_can_buy_same_item_again(self):
        self.store.buy_to_inventory(**{**self.purchase, "count": 1})
        second = self.store.buy_to_inventory(**{**self.purchase, "count": 1, "event_id": "purchase-two"})
        self.assertEqual((second["status"], second["quota_after"], second["item_count"]), ("ok", 8, 2))

    def test_real_qq_purchase_replay_reports_duplicate_without_charging_again(self):
        self.assertTrue(self.command(self.context)["ok"])
        second = self.command(self.context)
        self.assertEqual(second["status"], "duplicate")
        self.assertIn("已经处理", second["reply"])
        self.assertNotIn("commerce_card", second)
        snapshot = self.snapshot()
        self.assertEqual((snapshot["redeem_quota"], snapshot["inventory"]["receipt_tea"]["count"]), (14, 1))

    def test_real_qq_feed_replay_keeps_body_and_inventory_without_fake_second_feed(self):
        self.assertTrue(self.command(self.context)["ok"])
        context = replace(self.context, clean_message="/投喂 回执茶", source_message_id="message-feed-one")
        first = self.command(context)
        self.assertEqual(first["status"], "ok")
        second = self.command(context)
        self.assertEqual(second["status"], "duplicate")
        self.assertIn("已经处理", second["reply"])
        self.assertNotIn("_llm_passthrough", second)
        self.assertNotIn("qq_action_note", second)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["hunger"], 75)
        self.assertEqual(snapshot["inventory"], {})

    def test_real_qq_offering_replay_does_not_describe_another_item_consumption(self):
        self.items.append(
            {
                "id": "receipt_offering",
                "name": "回执供品",
                "price": 4,
                "category": "offering",
                "effects": {"affection": 4},
            }
        )
        self.store.grant_to_inventory(
            **self.base,
            item_id="receipt_offering",
            item_name="回执供品",
            item_category="offering",
            item_effects={"affection": 4},
        )
        context = replace(self.context, clean_message="/供奉 回执供品", source_message_id="message-offering-one")
        first = self.command(context)
        self.assertEqual(first["status"], "ok")
        second = self.command(context)
        self.assertEqual(second["status"], "duplicate")
        self.assertNotIn("_llm_passthrough", second)
        self.assertNotIn("qq_action_note", second)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["affection"], 14)
        self.assertEqual(snapshot["inventory"], {})


if __name__ == "__main__":
    unittest.main()
