"""Tests for QQ-mode economy: offerings, usable_in filtering, isolation."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.care_runtime import CareRuntimeStore
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext


PROFILE = "qq_group_shared_10001"
CHAR_ID = "reimu_demo"


def _store(tmp: str) -> CareRuntimeStore:
    return CareRuntimeStore(Path(tmp) / "care_runtime.json")


def _checkin(store: CareRuntimeStore, relation_user_id: str, coins: int = 20) -> None:
    store.claim_daily_checkin(
        profile_user_id=PROFILE,
        character_pack_id=CHAR_ID,
        relation_user_id=relation_user_id,
        date_key="2024-05-01",
        coins=coins,
        now_ms=1000,
    )


class OfferingFreeTests(unittest.TestCase):
    """Free offering (no item) grants affection once per user per day."""

    def test_free_offering_grants_affection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            result = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=3,
                now_ms=1000,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["daily_bonus"])
            self.assertEqual(result["affection_granted"], 3)
            snap = result["snapshot"]
            self.assertEqual(snap["affection"], 13)

    def test_repeat_free_offering_grants_zero_affection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=3,
                now_ms=1000,
            )
            result2 = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=3,
                now_ms=2000,
            )
            self.assertEqual(result2["status"], "already")
            self.assertFalse(result2["daily_bonus"])
            self.assertEqual(result2["affection_granted"], 0)
            self.assertEqual(result2["snapshot"]["affection"], 13)

    def test_offering_resets_next_day(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=3,
                now_ms=1000,
            )
            result2 = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-02",
                affection_bonus=3,
                now_ms=2000,
            )
            self.assertEqual(result2["status"], "ok")
            self.assertTrue(result2["daily_bonus"])
            self.assertEqual(result2["affection_granted"], 3)


class OfferingItemTests(unittest.TestCase):
    """Offering with paid item deducts qq_coins and applies effects."""

    def test_item_offering_deducts_coins_and_applies_effects(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            _checkin(store, "qq:111", coins=20)
            result = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=0,
                item_price=8,
                item_effects={"affection": 5},
                now_ms=2000,
            )
            self.assertEqual(result["status"], "ok")
            snap = result["snapshot"]
            self.assertEqual(snap["coins"], 12)
            self.assertEqual(snap["affection"], 15)

    def test_insufficient_coins_blocks_offering(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            _checkin(store, "qq:111", coins=5)
            result = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=0,
                item_price=8,
                item_effects={"affection": 5},
                now_ms=2000,
            )
            self.assertEqual(result["status"], "insufficient_coins")
            snap = result["snapshot"]
            self.assertEqual(snap["coins"], 5)
            self.assertEqual(snap["affection"], 10)

    def test_repeat_item_offering_applies_hunger_not_affection(self) -> None:
        """On repeat offering, hunger/energy effects still apply but affection is 0."""
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            _checkin(store, "qq:111", coins=30)
            food_effects = {"hunger": 15, "affection": 5}
            store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=0,
                item_price=8,
                item_effects=food_effects,
                now_ms=1000,
            )
            snap1 = store.snapshot_for_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            aff_after_first = snap1["affection"]
            hunger_after_first = snap1["hunger"]

            result2 = store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=0,
                item_price=8,
                item_effects=food_effects,
                now_ms=2000,
            )
            self.assertEqual(result2["status"], "already")
            self.assertEqual(result2["affection_granted"], 0)
            snap2 = result2["snapshot"]
            self.assertEqual(snap2["affection"], aff_after_first)
            self.assertGreater(snap2["hunger"], hunger_after_first)

    def test_gateway_rejects_food_item_as_offering(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            _checkin(store, "qq:111", coins=20)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="供奉 三色团子",
                character_pack_id=CHAR_ID,
            )
            result = gateway.handle_economy_command(
                context,
                care_runtime=store,
                shop_items=[
                    {
                        "id": "sanshoku_dango",
                        "name": "三色团子",
                        "price": 7,
                        "category": "food",
                        "usable_in": ["qq"],
                        "effects": {"hunger": 20, "affection": 4},
                    }
                ],
                now_ms=2000,
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result["status"], "not_offering_item")
            snap = store.snapshot_for_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            self.assertEqual(snap["coins"], 20)
            self.assertEqual(snap["affection"], 10)


class QQShopMergeTests(unittest.TestCase):
    """QQ shop should include default gameplay items plus character-pack items."""

    def test_character_shop_items_do_not_hide_default_trick_items(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="商店",
                character_pack_id=CHAR_ID,
            )
            result = gateway.handle_economy_command(
                context,
                care_runtime=store,
                shop_items=[
                    {
                        "id": "custom_cookie",
                        "name": "角色曲奇",
                        "price": 5,
                        "category": "food",
                        "usable_in": ["qq"],
                        "effects": {"hunger": 8},
                    }
                ],
                now_ms=2000,
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result["status"], "ok")
            self.assertIn("角色曲奇", result["reply"])
            self.assertIn("歪门邪道", result["reply"])
            self.assertIn("饥饿置零卡", result["reply"])


class QQStatusDisplayTests(unittest.TestCase):
    """QQ status display should spell out vital semantics."""

    def test_natural_status_question_returns_deterministic_values(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            store.sync_from_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 0, "energy": 88, "coins": 20, "affection": 10},
                now_ms=1000,
            )
            context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="饥饿度现在多少",
                character_pack_id=CHAR_ID,
            )
            result = gateway.handle_economy_command(
                context,
                care_runtime=store,
                shop_items=[],
                now_ms=2000,
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result["status"], "ok")
            self.assertNotIn("_llm_passthrough", result)
            self.assertIn("饥饿 0/100（越低越饿）", result["reply"])
            self.assertIn("精力 88/100（越高越精神）", result["reply"])

    def test_status_reply_explains_hunger_and_energy_direction(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            store.sync_from_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 9, "energy": 88, "coins": 20, "affection": 10},
                now_ms=1000,
            )
            context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="养成状态",
                character_pack_id=CHAR_ID,
            )
            result = gateway.handle_economy_command(
                context,
                care_runtime=store,
                shop_items=[],
                now_ms=2000,
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result["status"], "ok")
            self.assertIn("饥饿 9/100（越低越饿）", result["reply"])
            self.assertIn("精力 88/100（越高越精神）", result["reply"])


class QQFeedItemPromptTests(unittest.TestCase):
    """Special item effects should be explained clearly to the LLM."""

    def test_reversal_card_note_prevents_state_swap_confusion(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            relation = "qq:111"
            store.sync_from_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 9, "energy": 88, "coins": 20, "affection": 10},
                now_ms=1000,
            )
            _checkin(store, relation, coins=20)
            buy_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="购买 逆转卡",
                character_pack_id=CHAR_ID,
            )
            buy_result = gateway.handle_economy_command(buy_context, care_runtime=store, shop_items=[], now_ms=2000)
            self.assertIsInstance(buy_result, dict)
            self.assertEqual(buy_result["status"], "ok")

            feed_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="投喂 逆转卡",
                character_pack_id=CHAR_ID,
            )
            feed_result = gateway.handle_economy_command(feed_context, care_runtime=store, shop_items=[], now_ms=3000)
            self.assertIsInstance(feed_result, dict)
            self.assertTrue(feed_result["_llm_passthrough"])
            self.assertIn("逆转卡", feed_result["qq_action_note"])
            self.assertIn("把饥饿值和精力值交换了", feed_result["qq_action_note"])
            self.assertIn("不要理解成用户说反了", feed_result["qq_action_note"])
            self.assertIn("饥饿 88/100，精力 9/100", feed_result["qq_action_note"])

    def test_energy_full_charm_note_requires_visible_recovery(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            relation = "qq:111"
            store.sync_from_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 45, "energy": 8, "coins": 20, "affection": 10},
                now_ms=1000,
            )
            _checkin(store, relation, coins=30)
            buy_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="购买 精力满格符",
                character_pack_id=CHAR_ID,
            )
            buy_result = gateway.handle_economy_command(buy_context, care_runtime=store, shop_items=[], now_ms=2000)
            self.assertIsInstance(buy_result, dict)
            self.assertEqual(buy_result["status"], "ok")

            feed_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="投喂 精力满格符",
                character_pack_id=CHAR_ID,
            )
            feed_result = gateway.handle_economy_command(feed_context, care_runtime=store, shop_items=[], now_ms=3000)
            self.assertIsInstance(feed_result, dict)
            self.assertTrue(feed_result["_llm_passthrough"])
            note = feed_result["qq_action_note"]
            self.assertIn("精力满格符", note)
            self.assertIn("精力恢复到 100/100", note)
            self.assertIn("必须明显表现出困意被驱散", note)
            self.assertIn("不能只有吐槽", note)
            self.assertIn("饥饿 45/100，精力 100/100", note)

    def test_hunger_zero_card_note_prevents_not_hungry_wording(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            gateway = NapCatQQGateway(state_path=Path(tmp) / "qq_gateway_state.json")
            relation = "qq:111"
            store.sync_from_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 45, "energy": 70, "coins": 20, "affection": 10},
                now_ms=1000,
            )
            _checkin(store, relation, coins=20)
            buy_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="购买 饥饿置零卡",
                character_pack_id=CHAR_ID,
            )
            buy_result = gateway.handle_economy_command(buy_context, care_runtime=store, shop_items=[], now_ms=2000)
            self.assertIsInstance(buy_result, dict)
            self.assertEqual(buy_result["status"], "ok")

            feed_context = QQMessageContext(
                should_respond=True,
                reason="direct_command",
                is_group=True,
                target_id=10001,
                user_id=111,
                group_id=10001,
                session_id=PROFILE,
                profile_user_id=PROFILE,
                clean_message="投喂 饥饿置零卡",
                character_pack_id=CHAR_ID,
            )
            feed_result = gateway.handle_economy_command(feed_context, care_runtime=store, shop_items=[], now_ms=3000)
            self.assertIsInstance(feed_result, dict)
            self.assertTrue(feed_result["_llm_passthrough"])
            note = feed_result["qq_action_note"]
            self.assertIn("饥饿置零卡", note)
            self.assertIn("0/100 不是不饿", note)
            self.assertIn("饿到极限", note)
            self.assertIn("禁止说", note)
            self.assertIn("胃不叫了", note)
            self.assertIn("饥饿 0/100，精力 70/100", note)


class OfferingIsolationTests(unittest.TestCase):
    """Shared vitals vs per-user state boundaries."""

    def test_offering_hunger_goes_to_shared_body(self) -> None:
        """Food offered in QQ affects the shared body hunger, not per-user."""
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.sync_from_client(
                profile_user_id="master",
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 30, "energy": 60, "coins": 10, "affection": 20},
                now_ms=500,
            )
            store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=3,
                item_price=0,
                item_effects={"hunger": 20},
                now_ms=1000,
            )
            desktop_snap = store.snapshot_for_client(
                profile_user_id="master",
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
            )
            self.assertEqual(desktop_snap["hunger"], 50)

    def test_offering_affection_stays_per_user(self) -> None:
        """QQ offering affection goes to relation, not desktop affection."""
        with TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.sync_from_client(
                profile_user_id="master",
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 50, "energy": 60, "coins": 10, "affection": 25},
                now_ms=500,
            )
            store.claim_daily_offering(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                relation_user_id="qq:111",
                date_key="2024-05-01",
                affection_bonus=5,
                now_ms=1000,
            )
            desktop_snap = store.snapshot_for_client(
                profile_user_id="master",
                character_pack_id=CHAR_ID,
                client_mode="desktop_pet",
            )
            self.assertEqual(desktop_snap["affection"], 25)

            qq_snap = store.snapshot_for_client(
                profile_user_id=PROFILE,
                character_pack_id=CHAR_ID,
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            self.assertEqual(qq_snap["affection"], 15)


if __name__ == "__main__":
    unittest.main()
