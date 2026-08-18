from __future__ import annotations

import unittest
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.care_runtime import CareModulePort, CareRuntimeStore
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.qq_poke_reactor import PokeEventReactor


class FixedPokeReactor:
    def __init__(self, plan: dict):
        self.plan_value = dict(plan)

    def plan(self, *, snapshot: dict, shop_items: list[dict]) -> dict:
        return dict(self.plan_value)


class VariantRng:
    def random(self) -> float:
        return 0.93

    def choice(self, values):
        return values[0]


def _poke_event(message_id: str, *, group_id: int = 0) -> dict:
    event = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "self_id": 9000,
        "user_id": 7001,
        "sender_id": 7001,
        "target_id": 9000,
        "message_id": message_id,
        "time": int(time.time()),
    }
    if group_id:
        event["group_id"] = group_id
        event["sender"] = {"card": "休比", "nickname": "fallback"}
    return event


class QQPokeReactorTests(unittest.TestCase):
    def _runtime(self, tmp: str) -> CareRuntimeStore:
        store = CareRuntimeStore(Path(tmp) / "care_runtime.json")
        store.sync_from_client(
            profile_user_id="master",
            character_pack_id="reimu_demo",
            client_mode="desktop_pet",
            care_payload={
                "enabled": True,
                "hunger": 40,
                "energy": 60,
                "coins": 20,
                "affection": 30,
            },
            now_ms=1000,
        )
        return store

    def test_poke_consume_reports_real_inventory_and_effect_facts(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            runtime.buy_to_inventory(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                relation_user_id="qq:7001",
                item_id="sanshoku_dango",
                item_name="三色团子",
                price=0,
                count=2,
                item_effects={"hunger": 20, "energy": 4, "affection": 4},
                now_ms=2000,
            )
            gateway = NapCatQQGateway(
                default_character_pack_id="reimu_demo",
                poke_reactor=FixedPokeReactor(
                    {
                        "outcome_kind": "consume_inventory_item",
                        "item_id": "sanshoku_dango",
                        "item": {
                            "id": "sanshoku_dango",
                            "name": "三色团子",
                            "effects": {"hunger": 20, "energy": 4, "affection": 4},
                        },
                        "count": 2,
                    }
                ),
            )
            module = CareModulePort(enabled=True, _runtime=runtime)
            event = _poke_event("poke-consume-1")
            context = gateway.build_message_context(event)
            outcome = gateway.handle_poke_event(context, event, care_module=module, now_ms=3000)

            self.assertIsNotNone(outcome)
            self.assertEqual(outcome.status, "ok")
            self.assertIn("戳了戳你", outcome.memory_text)
            self.assertIn("从我的背包里偷吃了三色团子 x2", outcome.memory_text)
            self.assertNotIn("已真实消耗", outcome.prompt_text)
            self.assertIn("饥饿值 +40", outcome.prompt_text)
            self.assertIn("精力值 +8", outcome.prompt_text)
            self.assertIn("QQ 好感 +8", outcome.prompt_text)
            self.assertNotIn("请", outcome.prompt_text)
            snapshot = outcome.mutations[0]
            self.assertEqual(snapshot["count"], 2)
            inventory_snapshot = runtime.snapshot_for_client(
                profile_user_id="qq_group_shared_10001",
                character_pack_id="reimu_demo",
                client_mode="qq_text",
                relation_user_id="qq:7001",
                now_ms=3000,
            )
            self.assertEqual(inventory_snapshot.get("inventory"), {})

            duplicate = gateway.handle_poke_event(context, event, care_module=module, now_ms=3001)
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(
                runtime.snapshot_for_client(
                    profile_user_id="qq_group_shared_10001",
                    character_pack_id="reimu_demo",
                    client_mode="qq_text",
                    relation_user_id="qq:7001",
                    now_ms=3001,
                )["hunger"],
                80,
            )

    def test_group_stateful_pokes_are_rate_limited_after_success(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            gateway = NapCatQQGateway(
                default_character_pack_id="reimu_demo",
                poke_reactor=FixedPokeReactor({"outcome_kind": "coin_change", "coin_delta": 2}),
            )
            module = CareModulePort(enabled=True, _runtime=runtime)
            first_event = _poke_event("poke-coin-1", group_id=10001)
            first_context = gateway.build_message_context(first_event)
            first = gateway.handle_poke_event(first_context, first_event, care_module=module, now_ms=3000)
            self.assertEqual(first.status, "ok")
            self.assertIn("金币 +2", first.prompt_text)
            self.assertIn("顺手往休比的口袋里塞了2枚金币", first.memory_text)
            self.assertNotIn("余额发生了变化", first.memory_text)

            second_event = _poke_event("poke-coin-2", group_id=10001)
            second_gateway = NapCatQQGateway(
                default_character_pack_id="reimu_demo",
                poke_reactor=FixedPokeReactor({"outcome_kind": "coin_change", "coin_delta": 2}),
            )
            second_context = second_gateway.build_message_context(second_event)
            second = second_gateway.handle_poke_event(second_context, second_event, care_module=module, now_ms=4000)
            self.assertEqual(second.status, "cooldown")
            self.assertEqual(
                runtime.snapshot_for_client(
                    profile_user_id="qq_group_shared_10001",
                    character_pack_id="reimu_demo",
                    client_mode="qq_text",
                    relation_user_id="qq:7001",
                    now_ms=4000,
                )["coins"],
                2,
            )

    def test_variant_plan_uses_a_complete_recipient_fact(self) -> None:
        plan = PokeEventReactor(rng=VariantRng()).plan(snapshot={"coins": 0}, shop_items=[])
        self.assertEqual(plan["outcome_kind"], "variant")
        self.assertEqual(plan["variant"], "这一戳被你躲开了")


if __name__ == "__main__":
    unittest.main()
