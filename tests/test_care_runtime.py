from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.care_runtime import CareRuntimeStore
from companion_v01.engine import AkaneMemoryEngine


class CareRuntimeStoreTests(unittest.TestCase):
    def test_qq_shares_vitals_but_keeps_affection_separate(self) -> None:
        with TemporaryDirectory() as tmp:
            store = CareRuntimeStore(Path(tmp) / "care_runtime.json")

            desktop_snapshot = store.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={
                    "enabled": True,
                    "hunger": 38,
                    "energy": 64,
                    "coins": 17,
                    "affection": 42,
                },
                now_ms=1716192000000,
            )
            qq_snapshot = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
                now_ms=1716192000000,
            )

            self.assertEqual(desktop_snapshot["affection"], 42)
            self.assertEqual(desktop_snapshot["affection_scope"], "desktop_pet")
            self.assertEqual(qq_snapshot["hunger"], 38)
            self.assertEqual(qq_snapshot["energy"], 64)
            self.assertEqual(qq_snapshot["coins"], 0)  # QQ user starts with 0 personal coins
            self.assertEqual(qq_snapshot["affection"], 10)
            self.assertEqual(qq_snapshot["affection_scope"], "qq_text")
            self.assertTrue(qq_snapshot["shared_vitals"])

            store.apply_affinity_delta(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
                delta=3,
                now_ms=1716192010000,
            )

            self.assertEqual(
                store.snapshot_for_client(
                    profile_user_id="qq_group_shared_10001",
                    character_pack_id="reimu_demo",
                    client_mode="qq_text",
                    relation_user_id="qq:111",
                    now_ms=1716192010000,
                )["affection"],
                13,
            )
            other_group_member = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:222",
                now_ms=1716192010000,
            )
            self.assertEqual(other_group_member["hunger"], 38)
            self.assertEqual(other_group_member["energy"], 64)
            self.assertEqual(other_group_member["affection"], 10)
            self.assertEqual(
                store.snapshot_for_client(
                    profile_user_id="master",
                    character_pack_id="reimu_demo",
                    client_mode="desktop_pet",
                    now_ms=1716192010000,
                )["affection"],
                42,
            )

    def test_engine_uses_qq_sender_id_for_group_affection(self) -> None:
        with TemporaryDirectory() as tmp:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.care_runtime = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            qq_context = ClientProtocolContext(
                requested_mode=ClientMode.QQ_TEXT,
                effective_mode=ClientMode.QQ_TEXT,
            )
            engine.care_runtime.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={
                    "enabled": True,
                    "hunger": 44,
                    "energy": 66,
                    "coins": 25,
                    "affection": 30,
                },
                now_ms=1716192000000,
            )

            first_payload = engine._prepare_care_context_for_turn(
                {
                    "client_mode": "qq_text",
                    "qq_delivery_context": {
                        "is_group": True,
                        "group_id": 10001,
                        "user_id": 111,
                        "profile_user_id": "qq_group_shared_10001",
                    },
                },
                qq_context,
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                now_ts=1716192000,
            )

            self.assertEqual(first_payload["desktop_care"]["hunger"], 44)
            self.assertEqual(first_payload["desktop_care"]["energy"], 66)
            self.assertEqual(first_payload["desktop_care"]["affection"], 10)

            final_output = {"state_request": {"affinity": 2}}
            engine._apply_care_state_request(
                final_output,
                qq_context,
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                payload=first_payload,
                now_ts=1716192001,
            )
            self.assertEqual(final_output["care_state"]["affection"], 12)

            second_payload = engine._prepare_care_context_for_turn(
                {
                    "client_mode": "qq_text",
                    "qq_delivery_context": {
                        "is_group": True,
                        "group_id": 10001,
                        "user_id": 222,
                        "profile_user_id": "qq_group_shared_10001",
                    },
                },
                qq_context,
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                now_ts=1716192002,
            )
            self.assertEqual(second_payload["desktop_care"]["hunger"], 44)
            self.assertEqual(second_payload["desktop_care"]["affection"], 10)


class CareRuntimeEconomyTests(unittest.TestCase):
    """Per-user QQ economy: coins isolated per relation_user_id."""

    def _store(self, tmp: str) -> "CareRuntimeStore":
        return CareRuntimeStore(Path(tmp) / "care_runtime.json")

    def test_checkin_adds_qq_coins_to_relation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            result = store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=5,
                now_ms=1000,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["coins_granted"], 5)
            self.assertEqual(result["snapshot"]["coins"], 5)

    def test_checkin_blocked_same_day(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=5,
                now_ms=1000,
            )
            result2 = store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=5,
                now_ms=2000,
            )
            self.assertEqual(result2["status"], "already")
            self.assertEqual(result2["coins_granted"], 0)
            self.assertEqual(result2["snapshot"]["coins"], 5)

    def test_checkin_per_user_isolation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=5,
                now_ms=1000,
            )
            result_b = store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:222",
                date_key="2024-01-15",
                coins=5,
                now_ms=2000,
            )
            self.assertEqual(result_b["status"], "ok")
            snap_a = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            snap_b = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:222",
            )
            self.assertEqual(snap_a["coins"], 5)
            self.assertEqual(snap_b["coins"], 5)

    def test_purchase_deducts_buyer_qq_coins_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=10,
                now_ms=1000,
            )
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:222",
                date_key="2024-01-15",
                coins=10,
                now_ms=1001,
            )
            result = store.purchase_item(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                price=8,
                effects={"hunger": 18, "affection": 4},
                client_mode="qq_text",
                now_ms=2000,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["coins_before"], 10)
            self.assertEqual(result["coins_after"], 2)
            snap_a = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            snap_b = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:222",
            )
            self.assertEqual(snap_a["coins"], 2)
            self.assertEqual(snap_b["coins"], 10)

    def test_purchase_insufficient_coins_leaves_state_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=3,
                now_ms=1000,
            )
            result = store.purchase_item(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                price=8,
                effects={"hunger": 18},
                client_mode="qq_text",
                now_ms=2000,
            )
            self.assertEqual(result["status"], "insufficient_coins")
            self.assertEqual(result["coins_before"], 3)
            self.assertEqual(result["snapshot"]["coins"], 3)

    def test_desktop_coins_isolated_from_qq_coins(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 60, "energy": 60, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            store.claim_daily_checkin(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:111",
                date_key="2024-01-15",
                coins=7,
                now_ms=2000,
            )
            desktop_snap = store.snapshot_for_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
            )
            qq_snap = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            self.assertEqual(desktop_snap["coins"], 50)
            self.assertEqual(qq_snap["coins"], 7)

    def test_desktop_purchase_updates_desktop_affection_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 50, "energy": 60, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            result = store.purchase_item(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                relation_user_id="master",
                price=5,
                effects={"affection": 4},
                client_mode="desktop_pet",
                now_ms=2000,
            )
            self.assertEqual(result["status"], "ok")
            desktop_snap = store.snapshot_for_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
            )
            qq_snap = store.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
            )
            self.assertEqual(desktop_snap["coins"], 45)
            self.assertEqual(desktop_snap["affection"], 34)
            self.assertEqual(qq_snap["affection"], 10)


if __name__ == "__main__":
    unittest.main()
