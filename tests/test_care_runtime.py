from __future__ import annotations

import unittest
import unittest.mock as mock
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.care_runtime import CareModulePort, CareRuntimeStore
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
            care_runtime = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            engine.care_module = CareModulePort(enabled=True, _runtime=care_runtime)
            qq_context = ClientProtocolContext(
                requested_mode=ClientMode.QQ_TEXT,
                effective_mode=ClientMode.QQ_TEXT,
            )
            care_runtime.sync_from_client(
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


class DesktopCareAuthorityTests(unittest.TestCase):
    CONFIG = {
        "enabled": True,
        "initial_coins": 10,
        "initial_hunger": 50,
        "initial_energy": 80,
        "initial_affection": 15,
        "decay": {"hunger_per_hour": 4, "energy_per_reply": 1},
        "shop_items": [
            {
                "id": "dango",
                "name": "团子",
                "price": 4,
                "usable_in": ["desktop_pet"],
                "effects": {"hunger": 20, "energy": 3, "affection": 2},
            }
        ],
        "work": {
            "enabled": True,
            "duration_seconds": 10,
            "reward_coins_min": 7,
            "reward_coins_max": 7,
            "min_hunger": 20,
            "min_energy": 25,
            "hunger_cost": 12,
            "energy_cost": 25,
        },
        "allowance": {"enabled": True, "coins": 4, "cooldown_seconds": 300, "max_coins": 6},
    }

    def test_legacy_import_is_one_shot_and_survives_reopen(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "care_runtime.json"
            store = CareRuntimeStore(path)
            first = store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                legacy_state={
                    "coins": 9,
                    "hunger": 42,
                    "energy": 61,
                    "affection": 33,
                    "inventory": {"dango": 2, "unknown": 99},
                    "lastAllowanceAt": 800,
                },
                now_ms=1_000,
            )
            self.assertTrue(first["migration"]["imported"])
            self.assertEqual(first["snapshot"]["inventory"], {"dango": 2})
            self.assertEqual(first["snapshot"]["hunger"], 42)

            ignored = store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                legacy_state={"coins": 999, "hunger": 100, "inventory": {"dango": 99}},
                now_ms=1_001,
            )
            self.assertFalse(ignored["migration"]["imported"])
            self.assertEqual(ignored["snapshot"]["coins"], 9)
            self.assertEqual(ignored["snapshot"]["inventory"], {"dango": 2})

            store.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={"coins": 777, "hunger": 99, "affection": 99},
                now_ms=1_002,
            )
            reopened = CareRuntimeStore(path).snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                now_ms=1_003,
            )
            self.assertEqual(reopened["snapshot"]["coins"], 9)
            self.assertEqual(reopened["snapshot"]["hunger"], 42)
            self.assertEqual(reopened["snapshot"]["affection"], 33)

    def test_desktop_snapshot_applies_server_side_hunger_decay(self) -> None:
        with TemporaryDirectory() as tmp:
            store = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                legacy_state={"hunger": 50, "energy": 80},
                now_ms=1_000,
            )
            decayed = store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                now_ms=3_601_000,
            )
            self.assertEqual(decayed["snapshot"]["hunger"], 46)
            self.assertEqual(decayed["snapshot"]["energy"], 80)

    def test_desktop_actions_are_validated_and_return_authoritative_snapshots(self) -> None:
        with TemporaryDirectory() as tmp:
            store = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                legacy_state={"coins": 10, "hunger": 40, "energy": 80, "affection": 15},
                now_ms=1_000,
            )
            buy = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="buy",
                item_id="dango",
                now_ms=2_000,
            )
            self.assertTrue(buy["ok"])
            self.assertEqual(buy["snapshot"]["coins"], 6)
            self.assertEqual(buy["snapshot"]["inventory"], {"dango": 1})

            feed = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="feed",
                item_id="dango",
                now_ms=3_000,
            )
            self.assertEqual(feed["effects_applied"], {"hunger": 20, "energy": 3, "affection": 2})
            self.assertEqual(feed["snapshot"]["inventory"], {})
            self.assertEqual(feed["snapshot"]["hunger"], 60)

            missing = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="feed",
                item_id="dango",
                now_ms=3_001,
            )
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["reason"], "item_not_in_inventory")

            with mock.patch("companion_v01.care_runtime.random.randint", return_value=7):
                started = store.perform_desktop_action(
                    profile_user_id="master",
                    character_pack_id="reimu_demo",
                    care_config=self.CONFIG,
                    action="start_work",
                    now_ms=4_000,
                )
            self.assertTrue(started["ok"])
            self.assertIsNotNone(started["snapshot"]["work_task"])
            not_due = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="settle_work",
                now_ms=13_999,
            )
            self.assertEqual(not_due["reason"], "work_not_due")
            completed = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="settle_work",
                now_ms=14_000,
            )
            self.assertEqual(completed["reward_coins"], 7)
            self.assertIsNone(completed["snapshot"]["work_task"])

    def test_desktop_allowance_enforces_balance_and_cooldown(self) -> None:
        with TemporaryDirectory() as tmp:
            store = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            store.snapshot_for_desktop(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                legacy_state={"coins": 0},
                now_ms=1_000,
            )
            claimed = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="claim_allowance",
                now_ms=301_000,
            )
            self.assertEqual(claimed["coins_granted"], 4)
            cooldown = store.perform_desktop_action(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                care_config=self.CONFIG,
                action="claim_allowance",
                now_ms=301_001,
            )
            self.assertEqual(cooldown["reason"], "allowance_cooldown")

    def test_engine_desktop_turn_uses_store_for_input_cost_and_affinity(self) -> None:
        with TemporaryDirectory() as tmp:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            runtime = CareRuntimeStore(Path(tmp) / "care_runtime.json")
            engine.care_module = CareModulePort(enabled=True, _runtime=runtime)
            engine._load_desktop_care_config = mock.Mock(return_value=self.CONFIG)
            desktop_context = ClientProtocolContext(
                requested_mode=ClientMode.DESKTOP_PET,
                effective_mode=ClientMode.DESKTOP_PET,
            )
            prepared = engine._prepare_care_context_for_turn(
                {
                    "message": "hello",
                    "turn_kind": "desktop_pet_chat",
                    "desktop_care": {"coins": 9, "hunger": 44, "energy": 70, "affection": 20},
                },
                desktop_context,
                profile_user_id="master",
                character_pack_id="reimu_demo",
                now_ts=1_000,
            )
            self.assertEqual(prepared["desktop_care"]["authority"], "care_runtime")
            self.assertEqual(prepared["desktop_care"]["coins"], 9)

            final_output = {"speech": "hi", "state_request": {"affinity": 3}}
            engine._apply_care_state_request(
                final_output,
                desktop_context,
                profile_user_id="master",
                character_pack_id="reimu_demo",
                payload=prepared,
                now_ts=1_001,
            )
            self.assertEqual(final_output["care_state"]["energy"], 69)
            self.assertEqual(final_output["care_state"]["affection"], 23)
            self.assertEqual(final_output["care_state"]["authority"], "care_runtime")

    def test_engine_desktop_character_gate_rejects_disabled_care(self) -> None:
        with TemporaryDirectory() as tmp:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.care_module = CareModulePort(
                enabled=True,
                _runtime=CareRuntimeStore(Path(tmp) / "care_runtime.json"),
            )
            engine._load_desktop_care_config = mock.Mock(return_value={"enabled": False})
            desktop_context = ClientProtocolContext(
                requested_mode=ClientMode.DESKTOP_PET,
                effective_mode=ClientMode.DESKTOP_PET,
            )
            prepared = engine._prepare_care_context_for_turn(
                {"message": "hello", "desktop_care": {"coins": 999}},
                desktop_context,
                profile_user_id="master",
                character_pack_id="plain_character",
                now_ts=1_000,
            )
            self.assertEqual(prepared, {"message": "hello"})

            final_output = {"speech": "hi", "state_request": {"affinity": 5}}
            engine._apply_care_state_request(
                final_output,
                desktop_context,
                profile_user_id="master",
                character_pack_id="plain_character",
                payload=prepared,
                now_ts=1_001,
            )
            self.assertEqual(final_output, {"speech": "hi"})


class CareActivationBoundaryTests(unittest.TestCase):
    def test_disabled_module_does_not_construct_store_or_touch_existing_data(self) -> None:
        with TemporaryDirectory() as tmp:
            storage_path = Path(tmp) / "care_runtime.json"
            original = b'{"keep":"unchanged"}\n'
            storage_path.write_bytes(original)

            module = CareModulePort.from_feature(enabled=False, storage_path=storage_path)

            self.assertFalse(module.enabled)
            self.assertIsNone(module.runtime)
            self.assertEqual(module.status_payload()["reason"], "feature_disabled")
            self.assertEqual(storage_path.read_bytes(), original)

    def test_disabled_engine_strips_care_input_and_output(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.care_module = CareModulePort.disabled()
        desktop_context = ClientProtocolContext(
            requested_mode=ClientMode.DESKTOP_PET,
            effective_mode=ClientMode.DESKTOP_PET,
        )
        prepared = engine._prepare_care_context_for_turn(
            {
                "message": "hello",
                "desktop_care": {"enabled": True, "hunger": 1},
                "care_state": {"affection": 99},
            },
            desktop_context,
            profile_user_id="master",
            now_ts=1716192000,
        )
        self.assertEqual(prepared, {"message": "hello"})

        final_output = {
            "speech": "hello",
            "state_request": {"affinity": 5},
            "care_state": {"affection": 99},
        }
        engine._apply_care_state_request(
            final_output,
            desktop_context,
            profile_user_id="master",
            now_ts=1716192000,
        )
        self.assertEqual(final_output, {"speech": "hello"})

    def test_explicit_activation_resets_server_decay_baseline_without_changing_values(self) -> None:
        with TemporaryDirectory() as tmp:
            storage_path = Path(tmp) / "care_runtime.json"
            seed = CareRuntimeStore(storage_path)
            seed.sync_from_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 80, "energy": 40},
                now_ms=1_000,
            )

            module = CareModulePort.from_feature(
                enabled=True,
                storage_path=storage_path,
                reset_baseline_on_start=True,
            )
            snapshot = module.runtime.snapshot_for_client(
                profile_user_id="master",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:111",
                now_ms=86_401_000,
            )

            self.assertEqual(snapshot["hunger"], 80)
            self.assertEqual(snapshot["energy"], 40)

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


class CareRuntimeGameplayTests(unittest.TestCase):
    """Tests for inventory buy/use, fortune draw, check-in streaks, tier-up events."""

    _PROFILE = "qq_group_shared_10001"
    _CHAR = "reimu_demo"
    _REL = "qq:111"

    def _store(self, tmp: str) -> CareRuntimeStore:
        return CareRuntimeStore(Path(tmp) / "care_runtime.json")

    def _give_coins(self, store: CareRuntimeStore, coins: int, date: str = "2024-01-01") -> None:
        store.claim_daily_checkin(
            profile_user_id=self._PROFILE,
            character_pack_id=self._CHAR,
            relation_user_id=self._REL,
            date_key=date,
            coins=coins,
            now_ms=1000,
        )

    def test_buy_places_item_in_inventory_without_affecting_vitals(self) -> None:
        """买商品只进个人背包，不立刻影响饥饿/精力。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 40, "energy": 70, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            self._give_coins(store, 20)

            result = store.buy_to_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_name="三色团子",
                price=8,
                count=1,
                now_ms=2000,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["coins_after"], 12)  # 20 - 8
            self.assertEqual(result["item_count"], 1)

            snap = store.snapshot_for_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="qq_text",
                relation_user_id=self._REL,
                now_ms=2000,
            )
            self.assertEqual(snap["hunger"], 40)  # unchanged
            self.assertEqual(snap["energy"], 70)  # unchanged

    def test_use_from_inventory_consumes_item_and_updates_vitals(self) -> None:
        """投喂消耗背包并影响共享饥饿/精力；用完后 not_in_inventory。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 40, "energy": 60, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            self._give_coins(store, 20)
            store.buy_to_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_name="三色团子",
                price=8,
                count=1,
                now_ms=2000,
            )

            result = store.use_from_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_effects={"hunger": 20},
                count=1,
                now_ms=3000,
            )
            self.assertEqual(result["status"], "ok")
            snap = result["snapshot"]
            self.assertEqual(snap["hunger"], 60)   # 40 + 20
            self.assertEqual(snap["energy"], 60)   # unchanged

            use2 = store.use_from_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_effects={"hunger": 20},
                count=1,
                now_ms=4000,
            )
            self.assertEqual(use2["status"], "not_in_inventory")

    def test_only_direct_feed_sets_first_fed_anchor(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 40, "energy": 60, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            store.buy_to_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_name="三色团子",
                price=0,
                count=2,
                item_effects={"hunger": 20},
                now_ms=2000,
            )

            poke_result = store.use_from_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_effects={"hunger": 20},
                source="poke_consume",
                event_id="poke-anchor-test",
                now_ms=3000,
            )
            self.assertEqual(poke_result["status"], "ok")
            self.assertNotIn("first_fed", poke_result["snapshot"]["anchors"])

            feed_result = store.use_from_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_effects={"hunger": 20},
                source="direct_feed",
                now_ms=4000,
            )
            self.assertEqual(feed_result["status"], "ok")
            self.assertEqual(feed_result["snapshot"]["anchors"]["first_fed"]["name"], "三色团子")

    def test_poke_lottery_persists_state_and_event_with_one_save(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._give_coins(store, 20)
            with mock.patch("companion_v01.care_runtime.random.random", return_value=0.05), mock.patch.object(
                store, "_save", wraps=store._save
            ) as save:
                result = store.apply_poke_plan(
                    profile_user_id=self._PROFILE,
                    character_pack_id=self._CHAR,
                    relation_user_id=self._REL,
                    plan={"outcome_kind": "lottery"},
                    event_id="poke-lottery-save-test",
                    scope_key="reimu_demo|private:111|actor:111",
                    now_ms=2000,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["fortune"], "大吉")
            self.assertEqual(save.call_count, 1)
            duplicate = store.apply_poke_plan(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                plan={"outcome_kind": "lottery"},
                event_id="poke-lottery-save-test",
                scope_key="reimu_demo|private:111|actor:111",
                now_ms=2001,
            )
            self.assertEqual(duplicate["status"], "duplicate")

    def test_use_from_inventory_insufficient_count_leaves_state_unchanged(self) -> None:
        """背包数量不足时 insufficient_count，饥饿/精力不变。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.sync_from_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="desktop_pet",
                care_payload={"enabled": True, "hunger": 40, "energy": 60, "coins": 50, "affection": 30},
                now_ms=1000,
            )
            self._give_coins(store, 20)
            store.buy_to_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_name="三色团子",
                price=8,
                count=1,
                now_ms=2000,
            )

            result = store.use_from_inventory(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                item_id="teamango",
                item_effects={"hunger": 20},
                count=3,
                now_ms=3000,
            )
            self.assertEqual(result["status"], "insufficient_count")
            self.assertEqual(result["available"], 1)
            self.assertEqual(result["requested"], 3)
            self.assertEqual(result["snapshot"]["hunger"], 40)

    def test_fortune_daiji_grants_coins_and_affection(self) -> None:
        """大吉签（roll < 0.10）：净 +20 金币，好感 +3。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._give_coins(store, 20)

            with mock.patch("companion_v01.care_runtime.random.random", return_value=0.05):
                result = store.draw_fortune_slip(
                    profile_user_id=self._PROFILE,
                    character_pack_id=self._CHAR,
                    relation_user_id=self._REL,
                    slip_cost=5,
                    now_ms=2000,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["fortune"], "大吉")
            self.assertEqual(result["net_coins"], 20)   # +25 - 5 cost
            self.assertEqual(result["coins_after"], 40)  # 20 + 20
            self.assertEqual(result["affection_delta"], 3)

    def test_fortune_xiong_deducts_cost_plus_penalty(self) -> None:
        """凶签（roll >= 0.90）：净 -8 金币（签钱 5 + 罚 3）。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._give_coins(store, 20)

            with mock.patch("companion_v01.care_runtime.random.random", return_value=0.95):
                result = store.draw_fortune_slip(
                    profile_user_id=self._PROFILE,
                    character_pack_id=self._CHAR,
                    relation_user_id=self._REL,
                    slip_cost=5,
                    now_ms=2000,
                )
            self.assertEqual(result["fortune"], "凶")
            self.assertEqual(result["net_coins"], -8)
            self.assertEqual(result["coins_after"], 12)  # 20 - 8

    def test_fortune_insufficient_coins(self) -> None:
        """金币不足时抽签失败，余额不变。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._give_coins(store, 3)

            result = store.draw_fortune_slip(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                slip_cost=5,
                now_ms=2000,
            )
            self.assertEqual(result["status"], "insufficient_coins")
            self.assertEqual(result["coins_before"], 3)

    def test_checkin_streak_milestone_at_day_three(self) -> None:
        """连续3天签到触发里程碑，金币×1.5。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i, date in enumerate(("2024-01-01", "2024-01-02", "2024-01-03"), 1):
                r = store.claim_daily_checkin(
                    profile_user_id=self._PROFILE,
                    character_pack_id=self._CHAR,
                    relation_user_id=self._REL,
                    date_key=date,
                    coins=10,
                    now_ms=i * 1000,
                )
                self.assertEqual(r["streak"], i)
            self.assertTrue(r["streak_milestone"])
            self.assertEqual(r["coins_granted"], 15)  # 10 × 1.5

    def test_checkin_streak_broken_after_skip(self) -> None:
        """跳过一天后断签：streak_broken=True，连击归 1。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for date in ("2024-01-01", "2024-01-02", "2024-01-03"):
                store.claim_daily_checkin(
                    profile_user_id=self._PROFILE,
                    character_pack_id=self._CHAR,
                    relation_user_id=self._REL,
                    date_key=date,
                    coins=10,
                    now_ms=1000,
                )

            r = store.claim_daily_checkin(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                relation_user_id=self._REL,
                date_key="2024-01-05",  # skip Jan 4
                coins=10,
                now_ms=5000,
            )
            self.assertTrue(r["streak_broken"])
            self.assertEqual(r["streak"], 1)
            self.assertEqual(r["days_absent"], 1)

    def test_affinity_tier_event_fires_once_and_clears(self) -> None:
        """好感跨档：pending_tier_event 首次 snapshot 可见，第二次已清除。"""
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            # Default QQ affection = 10 (陌生). Push to 15, then cross 20 (→ 熟悉).
            store.apply_affinity_delta(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="qq_text",
                relation_user_id=self._REL,
                delta=5,   # 10 → 15 (陌生)
                now_ms=1000,
            )
            store.apply_affinity_delta(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="qq_text",
                relation_user_id=self._REL,
                delta=5,   # 15 → 20 (熟悉) — tier crossing
                now_ms=2000,
            )

            snap1 = store.snapshot_for_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="qq_text",
                relation_user_id=self._REL,
                now_ms=2001,
            )
            self.assertIn("pending_tier_event", snap1)
            self.assertEqual(snap1["pending_tier_event"]["from_tier"], "陌生")
            self.assertEqual(snap1["pending_tier_event"]["to_tier"], "熟悉")

            snap2 = store.snapshot_for_client(
                profile_user_id=self._PROFILE,
                character_pack_id=self._CHAR,
                client_mode="qq_text",
                relation_user_id=self._REL,
                now_ms=2002,
            )
            self.assertNotIn("pending_tier_event", snap2)


if __name__ == "__main__":
    unittest.main()
