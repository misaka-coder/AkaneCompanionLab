from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from companion_v01.care_runtime import CareRuntimeStore
from companion_v01.care_state_migration import migrate_care_state, restore_relationship_anchors
from companion_v01.desktop_context_engine import _build_anchor_lines


class CareConversationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "care.json"
        self.store = CareRuntimeStore(self.path)
        self.base = {"profile_user_id": "owner", "character_pack_id": "cecilia",
                     "relation_user_id": "qq:11", "now_ms": 1000}

    def snapshot(self, conversation="group87", **kw):
        return self.store.snapshot_for_client(
            **(self.base | kw), conversation_id=conversation, client_mode="qq_text",
        )

    def test_body_is_per_conversation_relation_per_member_account_per_character_user(self):
        self.store.sync_from_client(**self.base, conversation_id="group87", client_mode="qq_text",
                                    care_payload={"hunger": 20, "energy": 30, "affection": 44})
        self.store.claim_daily_checkin(**self.base, conversation_id="group87", date_key="2026-10-02", coins=15)
        same = self.snapshot()
        other_group = self.snapshot("group42")
        other_member = self.snapshot(relation_user_id="qq:22")
        other_role = self.snapshot(character_pack_id="reimu")
        self.assertEqual((same["hunger"], same["energy"], same["affection"], same["coins"]), (20, 30, 44, 15))
        self.assertEqual((other_group["hunger"], other_group["energy"], other_group["affection"], other_group["coins"]), (55, 70, 10, 15))
        self.assertEqual((other_member["hunger"], other_member["energy"], other_member["affection"], other_member["coins"]), (20, 30, 10, 0))
        self.assertEqual((other_role["hunger"], other_role["coins"]), (55, 0))

    def test_last_item_in_two_groups_has_only_one_consumer_and_replay_does_not_charge(self):
        self.store.grant_to_inventory(**self.base, conversation_id="group87", item_id="cake",
                                     item_name="蛋糕", item_effects={"hunger": 20, "affection": 4})
        def use(group):
            return self.store.use_from_inventory(**self.base, conversation_id=group,
                                                 item_id="cake", event_id=f"feed:{group}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(use, ["group87", "group42"]))
        self.assertEqual(sorted([a["status"], b["status"]]), ["not_in_inventory", "ok"])
        winner, loser = ("group87", "group42") if a["status"] == "ok" else ("group42", "group87")
        self.assertEqual(use(winner)["status"], "duplicate")
        self.assertEqual((self.snapshot(winner)["hunger"], self.snapshot(loser)["hunger"]), (75, 55))
        self.assertEqual((self.snapshot(winner)["affection"], self.snapshot(loser)["affection"]), (14, 10))
        self.assertEqual(self.snapshot()["inventory"], {})

    def test_migration_freezes_old_values_once_without_copying_money(self):
        old = {"schema_version": "akane.care_runtime.v2", "characters": {"cecilia": {
            "hunger": 24, "energy": 33, "coins": 51, "desktop_inventory": {"tea": 2}, "shrine_total": 77,
        }}, "relations": {"cecilia": {"qq_11": {"qq_affection": 65, "qq_coins": 90,
            "qq_redeem_quota": 12, "inventory": {"cake": {"count": 1, "name": "蛋糕"}}}}}}
        self.path.write_text(json.dumps(old), encoding="utf-8")
        origins = lambda: [{"character_pack_id": "cecilia", "session_id": x, "relation_user_ids": ["qq:11"]} for x in ("group87", "group42")]
        self.store = CareRuntimeStore(self.path, known_conversations=origins)
        self.assertEqual((self.snapshot()["hunger"], self.snapshot("group42")["affection"]), (24, 65))
        backup = self.path.with_name(f"{self.path.name}.pre-v3.bak")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), old)
        self.assertEqual(self.snapshot("group87", relation_user_id="qq:22")["affection"], 10)
        self.assertEqual((self.snapshot("new")["hunger"], self.snapshot("new")["affection"]), (55, 10))
        self.store.adjust_qq_coins(**self.base, conversation_id="group87", amount=-10)
        self.assertEqual(self.snapshot("group42")["coins"], 80)
        self.store.donate_to_shrine(**self.base, conversation_id="group42", amount=5)
        self.assertEqual(self.snapshot()["shrine_total"], 82)
        self.store = CareRuntimeStore(self.path, known_conversations=origins)
        self.assertEqual(self.snapshot("new")["coins"], 75)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(state["legacy_v2"]["characters"]["cecilia"]["desktop_inventory"], {"tea": 2})
        self.assertNotIn("inventory", state["relationships"]["cecilia"]["group87"]["qq_11"])
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), old)

    def test_migration_preserves_relationship_history_for_shared_owner(self):
        anchors = {"first_seen_ms": 1788756227000, "total_turns": 3432, "late_night_turns": 1306,
                   "first_fed": {"ms": 1788766302072, "name": "温热玄米茶"}, "max_checkin_streak": 2}
        old = {"schema_version": "akane.care_runtime.v2", "relations": {"cecilia": {"qq_11": {
            "qq_affection": 65, "qq_coins": 90, "anchors": anchors,
            "pending_tier_event": {"from_tier": "熟悉", "to_tier": "亲近"},
        }}}}
        self.path.write_text(json.dumps(old), encoding="utf-8")
        self.store = CareRuntimeStore(self.path, known_conversations=lambda: [{
            "character_pack_id": "cecilia", "session_id": "master", "relation_user_ids": ["qq:11"],
        }])
        now_ms = 1790966030000
        qq = self.snapshot("master", now_ms=now_ms)
        desktop = self.store.snapshot_for_desktop(**(self.base | {"now_ms": now_ms}),
            conversation_id="master", shared_account=True, care_config={"enabled": True})["snapshot"]
        self.assertEqual(qq["anchors"], anchors)
        self.assertEqual(desktop["anchors"], anchors)
        self.assertIn("pending_tier_event", qq)
        rendered = "\n".join(_build_anchor_lines(qq["anchors"], qq["now"]))
        self.assertIn("认识已经 25 天", rendered)
        self.assertIn("聊过 3432 轮", rendered)
        self.assertIn("其中深夜聊天 1306 轮", rendered)
        self.assertNotIn("今天刚认识", rendered)
        self.assertEqual(qq["anchors"], anchors)
        self.assertEqual(desktop["anchors"], anchors)
        settled = self.store.settle_turn(**(self.base | {"now_ms": now_ms}), conversation_id="master",
            client_mode="qq_text", energy_cost=1, event_id="after-migration")
        self.assertEqual(settled["anchors"]["total_turns"], 3433)
        self.assertEqual(settled["anchors"]["first_seen_ms"], anchors["first_seen_ms"])
        self.assertEqual(self.snapshot("new")["anchors"], {})

    def test_anchor_repair_keeps_new_interactions_and_only_original_membership(self):
        old = {"schema_version": "akane.care_runtime.v2", "characters": {}, "relations": {"cecilia": {
            "qq_11": {"qq_affection": 65, "qq_coins": 90, "anchors": {
                "first_seen_ms": 1000, "total_turns": 32, "late_night_turns": 6,
                "first_fed": {"ms": 2000, "name": "茶"}, "max_checkin_streak": 2,
            }},
        }}}
        baseline = migrate_care_state(deepcopy(old), known_conversations=[{
            "character_pack_id": "cecilia", "session_id": "master", "relation_user_ids": ["qq:11"],
        }])
        current = deepcopy(baseline)
        current.pop("relationship_anchors_version")
        relation = current["relationships"]["cecilia"]["master"]["qq_11"]
        relation.update(qq_affection=70, updated_at=6000, anchors={
            "first_seen_ms": 4000, "total_turns": 4, "late_night_turns": 1,
            "first_fed": {"ms": 5000, "name": "蛋糕"}, "max_checkin_streak": 3, "new_anchor": "kept",
        })
        current["relationships"]["cecilia"]["new-group"] = {"qq_11": deepcopy(relation)}
        current["accounts"]["cecilia"]["qq_11"]["qq_coins"] = 99
        current["care_events"] = [{"event_id": "new-transaction"}]
        before = deepcopy(current)
        repaired = restore_relationship_anchors(current, migration_baseline=baseline)
        self.assertEqual(current, before)
        restored = repaired["relationships"]["cecilia"]["master"]["qq_11"]
        self.assertEqual(restored["anchors"], {"first_seen_ms": 1000, "total_turns": 36,
            "late_night_turns": 7, "first_fed": {"ms": 2000, "name": "茶"},
            "max_checkin_streak": 3, "new_anchor": "kept"})
        self.assertEqual((restored["qq_affection"], restored["updated_at"]), (70, 6000))
        for key in ("accounts", "bodies", "economies", "care_events", "legacy_v2"):
            self.assertEqual(repaired[key], before[key])
        self.assertEqual(repaired["relationships"]["cecilia"]["new-group"],
                         before["relationships"]["cecilia"]["new-group"])
        self.assertEqual(restore_relationship_anchors(repaired, migration_baseline=baseline), repaired)
        repaired.pop("relationship_anchors_version")
        self.assertEqual(restore_relationship_anchors(repaired, migration_baseline=baseline)["relationships"],
                         repaired["relationships"])
        wrong_baseline = deepcopy(baseline)
        wrong_baseline["legacy_v2"]["relations"] = {}
        with self.assertRaisesRegex(ValueError, "baseline_mismatch"):
            restore_relationship_anchors(current, migration_baseline=wrong_baseline)

    def test_failed_write_keeps_inventory_and_balance_in_memory_and_disk(self):
        self.store.grant_to_inventory(**self.base, item_id="cake", item_name="蛋糕")
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.use_from_inventory(**self.base, item_id="cake", event_id="retry")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.snapshot()["inventory"]["cake"]["count"], 1)
        self.assertEqual(self.store.use_from_inventory(**self.base, item_id="cake", event_id="retry")["status"], "ok")

    def test_offering_inventory_effects_and_daily_reward_commit_together(self):
        self.store.grant_to_inventory(**self.base, item_id="gift", item_name="礼物")
        args = {**self.base, "conversation_id": "group87", "inventory_item_id": "gift", "date_key": "2026-10-02",
                "event_id": "offering-once", "item_effects": {"hunger": 10, "affection": 4}, "affection_bonus": 0}
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.claim_daily_offering(**args)
        self.assertEqual(self.snapshot()["inventory"]["gift"]["count"], 1)
        result = self.store.claim_daily_offering(**args)
        self.assertEqual(result["affection_granted"], 4)
        self.assertEqual(self.snapshot()["hunger"], 65)
        self.assertEqual(self.snapshot("group42")["hunger"], 55)
        self.assertEqual(self.store.claim_daily_offering(**args)["status"], "duplicate")
        self.assertEqual(self.snapshot()["affection"], 14)

    def test_cards_cannot_steal_tier_event_and_other_groups_cannot_consume_it(self):
        self.store.sync_from_client(**self.base, conversation_id="group87", client_mode="qq_text", care_payload={"affection": 18})
        self.store.apply_affinity_delta(**self.base, conversation_id="group87", client_mode="qq_text", delta=3)
        self.assertIn("pending_tier_event", self.snapshot())
        self.assertIn("pending_tier_event", self.snapshot())
        args = {k: v for k, v in self.base.items() if k != "now_ms"}
        self.assertIsNone(self.store.consume_tier_event(**args, conversation_id="group42"))
        self.assertEqual(self.store.consume_tier_event(**args, conversation_id="group87")["to_tier"], "熟悉")
        self.assertIsNone(self.store.consume_tier_event(**args, conversation_id="group87"))
        self.assertNotIn("pending_tier_event", self.snapshot())

    def test_corrupt_store_does_not_reset_assets(self):
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    def test_reply_settlement_is_atomic_and_retry_safe(self):
        self.store.sync_from_client(**self.base, conversation_id="group87", client_mode="qq_text", care_payload={"affection": 18})
        args = self.base | {"conversation_id": "group87", "client_mode": "qq_text", "energy_cost": 1,
                            "coin_reward": 1, "affinity_delta": 4, "event_id": "reply-1"}
        first = self.store.settle_turn(**args)
        repeated = self.store.settle_turn(**args)
        self.assertEqual((first["energy"], first["coins"], first["affection"]), (69, 1, 22))
        self.assertEqual((repeated["energy"], repeated["coins"], repeated["affection"]), (69, 1, 22))
        self.assertEqual(repeated["anchors"]["total_turns"], 1)
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.settle_turn(**(args | {"event_id": "reply-2"}))
        self.assertEqual(self.snapshot()["coins"], 1)
        self.store.settle_turn(**(args | {"event_id": "reply-2"}))
        self.assertEqual(self.snapshot()["coins"], 2)

    def test_shared_desktop_actions_use_qq_account_without_importing_local_money(self):
        self.store.claim_daily_checkin(**self.base, conversation_id="master", date_key="2026-10-02", coins=10)
        args = self.base | {"conversation_id": "master", "shared_account": True, "care_config": {
            "enabled": True, "shop_items": [{"id": "cake", "name": "蛋糕", "price": 3,
                "effects": {"hunger": 20, "affection": 4}}], "decay": {"hunger_per_hour": 4},
        }}
        snap = self.store.snapshot_for_desktop(**args, legacy_state={"coins": 9999, "hunger": 1})["snapshot"]
        self.assertEqual((snap["coins"], snap["hunger"]), (10, 55))
        self.assertTrue(self.store.perform_desktop_action(**args, action="buy", item_id="cake", request_id="buy-1")["ok"])
        self.assertEqual(self.snapshot("master")["inventory"]["cake"]["count"], 1)
        self.store.perform_desktop_action(**args, action="feed", item_id="cake", request_id="feed-1")
        self.store.perform_desktop_action(**args, action="feed", item_id="cake", request_id="feed-1")
        self.assertEqual((self.snapshot("master")["coins"], self.snapshot("master")["affection"]), (7, 14))
        self.assertEqual(self.snapshot("group87")["hunger"], 55)

    def test_frequent_desktop_polling_cannot_suppress_shared_energy_recovery(self):
        self.store.sync_from_client(**self.base, conversation_id="master", client_mode="qq_text",
                                    care_payload={"hunger": 80, "energy": 20})
        for now_ms in range(11000, 3610001, 10000):
            self.store.snapshot_for_desktop(**(self.base | {"now_ms": now_ms}), conversation_id="master",
                                            shared_account=True, care_config={"enabled": True})
        snap = self.snapshot("master", now_ms=3601000)
        self.assertEqual((snap["hunger"], snap["energy"]), (72, 50))


if __name__ == "__main__":
    unittest.main()
