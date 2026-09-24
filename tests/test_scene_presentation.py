import asyncio
import tempfile
import unittest
from pathlib import Path

from companion_v01.client_protocol import ClientProtocolContext, ClientMode
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.scene.contracts import Identity, Receipt, TurnRequest
from companion_v01.scene.presentation.model_contract import normalize_scene_model_output
from companion_v01.scene.presentation.service import PresentationService
from tests import test_scene_room as fixture


class ScenePresentationTests(unittest.TestCase):
    setUp = fixture.SceneRoomTests.setUp
    make_service = fixture.SceneRoomTests.make_service
    request = fixture.SceneRoomTests.request

    def test_scene_contract_does_not_change_old_client_prompt(self):
        registry = PromptProfileRegistry()
        old = ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET, capabilities=("speech_segments",))
        scene = old.model_copy(update={"capabilities": ("speech_segments", "scene_presentation_v1")})
        self.assertNotIn("scene_presentation_v1", registry.resolve(old).system_prompt_override)
        new_prompt = registry.resolve(scene).system_prompt_override
        self.assertIn("beats", new_prompt)
        self.assertNotIn("speech 是唯一用户可见正文", new_prompt)
        self.assertEqual(registry.resolve(old).id, "desktop_pet")

    def test_three_beats_keep_distinct_expressions_and_canonical_memory_text(self):
        data = {"beats": [{"speech": "第一句。", "emotion_id": "normal"},
                          {"speech": "第二句！", "emotion_id": "smug"},
                          {"speech": "第三句……", "emotion_id": "shy"}]}
        normalized = normalize_scene_model_output(data)
        self.assertEqual([b["emotion_id"] for b in normalized["beats"]], ["normal", "smug", "shy"])
        self.assertEqual(normalized["speech"], "第一句。\n第二句！\n第三句……")
        self.assertNotIn("speech", data)

    def test_receipts_are_ordered_deduplicated_and_scope_checked(self):
        async def generate(payload):
            self.assertEqual(payload["client_mode"], "desktop_pet")
            return {"beats": [{"speech": "你好。", "emotion_id": "normal"}]}
        service = PresentationService(room=self.service, generate=generate)
        request = TurnRequest(identity=self.identity, request_id="model-turn01", generation=1, message="你好")
        presentation = asyncio.run(service.turn(request))
        repeated = asyncio.run(service.turn(request))
        self.assertEqual(repeated.turn_id, presentation.turn_id)
        receipt = Receipt(identity=self.identity, turn_id=presentation.turn_id,
                          beat_id=presentation.beats[0].beat_id, generation=1, kind="audio_completed")
        with self.assertRaisesRegex(ValueError, "delivery_order_invalid"):
            service.receipt(receipt)
        for kind in ("display_started", "text_revealed", "audio_started", "interrupted"):
            value = receipt.model_copy(update={"kind": kind})
            self.assertTrue(service.receipt(value)["ok"])
            self.assertTrue(service.receipt(value)["duplicate"])
        with self.assertRaisesRegex(ValueError, "delivery_order_invalid"):
            service.receipt(receipt)
        # audio_interrupted arriving after interrupted is allowed and succeeds
        audio_interrupted = receipt.model_copy(update={"kind": "audio_interrupted"})
        self.assertTrue(service.receipt(audio_interrupted)["ok"])
        self.assertTrue(service.receipt(audio_interrupted)["duplicate"])
        with self.assertRaisesRegex(ValueError, "presentation_not_found"):
            service.receipt(receipt.model_copy(update={"identity": self.identity.model_copy(update={"session_id": "other"})}))
        kinds = [e["event"]["event_type"] for e in self.events]
        self.assertIn("scene.presentation", kinds)
        self.assertIn("scene.delivery", kinds)

    def test_feedback_failure_keeps_purchase_without_replaying_action(self):
        item = self.service.snapshot(self.identity).shop[0]
        action = self.service.action(self.request("buy", item.id, "buy-feedback-01"))
        async def broken(_):
            raise RuntimeError("model offline")
        service = PresentationService(room=self.service, generate=broken)
        request = TurnRequest(identity=self.identity, request_id="feedback-fail01", generation=1,
                              event_id=action.event.event_id)
        with self.assertRaises(RuntimeError):
            asyncio.run(service.turn(request))
        after = self.service.snapshot(self.identity)
        self.assertEqual(after.care.coins, action.snapshot.care.coins)
        self.assertEqual(after.care.inventory[item.id], 1)

    def test_feed_action_formats_interaction_prompt_and_exempts_energy(self):
        item = self.service.snapshot(self.identity).shop[0]
        bought = self.service.action(self.request("buy", item.id, "feed-test-buy-01"))
        fed = self.service.action(self.request("feed", item.id, "feed-test-feed-01", bought.snapshot.room.revision))
        self.assertTrue(fed.ok)
        self.assertEqual(fed.event.item_name, "团子")
        self.assertEqual(fed.event.effects, {"hunger": 20, "energy": 4, "affection": 2})

        captured = {}
        async def mock_generate(payload):
            captured.update(payload)
            return {"beats": [{"speech": "谢谢你喂我吃团子！", "emotion_id": "normal"}]}

        service = PresentationService(room=self.service, generate=mock_generate)
        request = TurnRequest(identity=self.identity, request_id="feed-turn-test-01", generation=1,
                              event_id=fed.event.event_id)
        pres = asyncio.run(service.turn(request))
        self.assertEqual(pres.beats[0].speech, "谢谢你喂我吃团子！")
        self.assertEqual(captured.get("turn_kind"), "desktop_pet_care_feed")
        self.assertIn("刚才发生的互动：我投喂了你「团子」。", captured["message"])
        self.assertIn("状态变化：饥饿 +20，精力 +4，好感 +2。", captured["message"])
        self.assertIn(f"当前状态：饥饿 {fed.snapshot.care.hunger}/100", captured["message"])
        self.assertEqual(captured["memory_message"], "刚才发生的互动：我投喂了你「团子」。")

        # Test touch formatting
        touched = self.service.action(self.request("touch", "head", "touch-test-01", fed.snapshot.room.revision))
        captured.clear()
        touch_req = TurnRequest(identity=self.identity, request_id="touch-turn-test-01", generation=1,
                                event_id=touched.event.event_id)
        asyncio.run(service.turn(touch_req))
        self.assertIn("刚才发生的互动：我摸了摸你的头。", captured["message"])
        self.assertEqual(captured["memory_message"], "刚才发生的互动：我摸了摸你的头。")

        # Test batch feed formatting (quantity > 1)
        from companion_v01.care_runtime import format_care_feed_prompt
        pack = format_care_feed_prompt(
            item_name="团子",
            hunger_delta=60,
            energy_delta=12,
            affection_delta=6,
            current_hunger=80,
            current_energy=90,
            current_affection=50,
            actor_label="我",
            quantity=3,
        )
        self.assertIn("刚才发生的互动：我投喂了你「团子」 x3。", pack["turn_message"])
        self.assertIn("刚才发生的互动：我投喂了你「团子」 x3。", pack["memory_message"])
        self.assertEqual(pack["turn_kind"], "desktop_pet_care_feed")


if __name__ == "__main__":
    unittest.main()
