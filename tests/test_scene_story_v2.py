import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.scene.contracts import Identity, StoryNode, StoryPack, StoryChoiceOption, StoryStepRequest
from companion_v01.scene.story.conditions import evaluate_condition
from companion_v01.scene.story.loader import StoryLoader
from companion_v01.scene.story.repository import StoryRepository
from companion_v01.scene.story.service import StoryService


class SceneStoryV2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_rooms.sqlite3"
        self.repo = StoryRepository(self.db_path)
        self.mock_generated = []

        async def mock_generate(payload):
            self.mock_generated.append(payload)
            turn_idx = len(self.mock_generated)
            return {
                "beats": [
                    {"speech": f"这是茜茜的第{turn_idx}次回答~", "emotion_id": "happy", "motion_id": "nod"}
                ]
            }

        self.care_actions = []

        class MockCare:
            def __init__(self, actions_log):
                self.actions_log = actions_log
                self.coins = 100
                self.inventory = {"tea": 2}

            def action(self, identity, kind, target, count=1, request_id=""):
                self.actions_log.append({"kind": kind, "target": target, "count": count, "request_id": request_id})
                return {"ok": True, "event": {"event_id": "care_evt_1", "item_name": "温热玄米茶"}}

        self.mock_care = MockCare(self.care_actions)

        def mock_snapshot(identity):
            return {
                "care": {
                    "coins": self.mock_care.coins,
                    "inventory": dict(self.mock_care.inventory),
                },
                "room": {"outfit_id": "builtin-水手服"},
            }

        self.characters_dir = Path(self.tmp.name) / "characters"
        self.loader = StoryLoader(characters_dir=self.characters_dir, user_stories_dir=Path(self.tmp.name) / "stories")

        self.service = StoryService(
            self.repo,
            generate=mock_generate,
            loader=self.loader,
            care=self.mock_care,
            snapshot_fn=mock_snapshot,
        )

        self.identity = Identity(
            profile_user_id="user_v2",
            session_id="session_v2",
            character_pack_id="akane_v1",
        )

        # Build an in-memory V2 test story pack
        self.test_pack = StoryPack(
            story_id="v2_test_story",
            title="V2互动测试故事",
            description="用于测试多轮对话、条件过滤与原地买赠的故事",
            initial_node_id="conv_node",
            nodes={
                "conv_node": StoryNode(
                    node_id="conv_node",
                    kind="conversation",
                    speech="欢迎来到深夜小厨房，我们聊聊天吧！",
                    prompt_objective="深夜温馨陪伴，关怀玩家",
                    suggested_turns=2,
                    max_turns=3,
                    quick_reactions=["茜茜好呀", "今天好累"],
                    next_node_id="choice_node",
                ),
                "choice_node": StoryNode(
                    node_id="choice_node",
                    kind="choice",
                    title="准备分享点心",
                    options=[
                        StoryChoiceOption(
                            id="opt_free",
                            label="夸赞茜茜手艺",
                            next_node_id="ending_normal",
                        ),
                        StoryChoiceOption(
                            id="opt_feed_action",
                            label="投喂玄米茶",
                            next_node_id="ending_sweet",
                            action_kind="feed",
                            action_target="warm_genmaicha",
                            action_count=1,
                            cost_coins=6,
                            cost_label="6金币",
                        ),
                        StoryChoiceOption(
                            id="opt_needs_dango",
                            label="拿出珍藏的大福",
                            next_node_id="ending_sweet",
                            condition="inventory.red_bean_daifuku >= 1",
                        ),
                    ],
                ),
                "ending_normal": StoryNode(
                    node_id="ending_normal",
                    kind="ending",
                    ending_title="普通结局",
                    ending_summary="普通的深夜问候",
                    speech="早点休息哦！",
                ),
                "ending_sweet": StoryNode(
                    node_id="ending_sweet",
                    kind="ending",
                    ending_title="甜蜜结局",
                    ending_summary="分享宵夜的双向奔赴",
                    speech="最喜欢和你一起吃宵夜了！",
                ),
            },
        )
        self.loader.register_builtin_pack(self.test_pack)

    def tearDown(self):
        self.tmp.cleanup()

    def test_safe_condition_evaluator(self):
        # 1. Inventory checks
        ctx = {
            "inventory": {"tea": 3, "cookie": 0},
            "coins": 50,
            "outfit_id": "casual",
            "completed_endings": ["end_1", "end_2"],
            "variables": {"talked": True, "score": 10},
        }
        self.assertTrue(evaluate_condition("inventory.tea >= 2", ctx))
        self.assertFalse(evaluate_condition("inventory.tea > 5", ctx))
        self.assertFalse(evaluate_condition("inventory.cookie >= 1", ctx))
        self.assertTrue(evaluate_condition("inventory.cookie == 0", ctx))

        # 2. Coins and outfit
        self.assertTrue(evaluate_condition("coins >= 30", ctx))
        self.assertFalse(evaluate_condition("coins > 100", ctx))
        self.assertTrue(evaluate_condition("outfit == casual", ctx))
        self.assertFalse(evaluate_condition("outfit == pajamas", ctx))

        # 3. Completed endings
        self.assertTrue(evaluate_condition("completed_endings.has(end_1)", ctx))
        self.assertFalse(evaluate_condition("completed_endings.has(end_3)", ctx))

        # 4. Variables
        self.assertTrue(evaluate_condition("vars.talked == true", ctx))
        self.assertTrue(evaluate_condition("vars.score >= 5", ctx))

        # 5. Compound all / any
        self.assertTrue(evaluate_condition("all(inventory.tea >= 1, coins >= 10)", ctx))
        self.assertFalse(evaluate_condition("all(inventory.tea >= 1, coins >= 200)", ctx))
        self.assertTrue(evaluate_condition("any(inventory.tea >= 10, coins >= 10)", ctx))

    async def test_conversation_multi_turns_and_advance_only(self):
        run = self.service.start(self.identity, "v2_test_story")
        self.assertEqual(run.current_node_id, "conv_node")
        self.assertEqual(run.current_node.kind, "conversation")
        self.assertEqual(run.current_node.quick_reactions, ["茜茜好呀", "今天好累"])
        self.assertEqual(len(run.conversation_turns), 0)

        # Turn 1: Free reply
        step1 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="conv_node",
                user_message="茜茜好呀，刚刚写完作业",
            )
        )
        self.assertEqual(step1.current_node_id, "conv_node")
        self.assertEqual(step1.turn_count, 1)
        self.assertEqual(len(step1.conversation_turns), 2)
        self.assertEqual(step1.conversation_turns[0]["text"], "茜茜好呀，刚刚写完作业")
        self.assertEqual(len(self.mock_generated), 1)

        # Turn 2: Free reply reaching suggested_turns (2)
        step2 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="conv_node",
                user_message="有点肚子饿了呢",
            )
        )
        self.assertEqual(step2.current_node_id, "conv_node")
        self.assertEqual(step2.turn_count, 2)
        self.assertEqual(len(step2.conversation_turns), 4)
        self.assertEqual(len(self.mock_generated), 2)
        # Check that prompt includes winding-down hint
        prompt_sent = self.mock_generated[1]["message"]
        self.assertIn("温和引导玩家继续故事", prompt_sent)

        # Player clicks "继续故事" (advance_only=True)
        step_advance = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="conv_node",
                advance_only=True,
            )
        )
        self.assertEqual(step_advance.current_node_id, "choice_node")
        self.assertEqual(step_advance.current_node.kind, "choice")

    async def test_choice_condition_filtering_and_atomic_action(self):
        run = self.service.start(self.identity, "v2_test_story")
        # Advance directly to choice
        await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="conv_node",
                advance_only=True,
            )
        )

        # Try picking opt_needs_dango which requires inventory.red_bean_daifuku >= 1 (we don't have it)
        with self.assertRaises(ValueError) as ctx:
            await self.service.step(
                StoryStepRequest(
                    identity=self.identity,
                    run_id=run.run_id,
                    expected_node_id="choice_node",
                    choice_id="opt_needs_dango",
                )
            )
        self.assertIn("choice_condition_unmet", str(ctx.exception))

        # Pick opt_feed_action with atomic Care action
        step_action = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_node",
                choice_id="opt_feed_action",
            )
        )
        # Verify Care action was executed: buy then feed (atomic buy-and-gift)
        self.assertEqual(len(self.care_actions), 2)
        self.assertEqual(self.care_actions[0]["kind"], "buy")
        self.assertEqual(self.care_actions[0]["target"], "warm_genmaicha")
        self.assertEqual(self.care_actions[1]["kind"], "feed")
        self.assertEqual(self.care_actions[1]["target"], "warm_genmaicha")

        # Verify story moved to ending_sweet
        self.assertEqual(step_action.current_node_id, "ending_sweet")
        self.assertEqual(step_action.status, "in_progress")

        # Explicit ending confirmation
        completed = self.service.complete_ending(self.identity, run.run_id, "ending_sweet")
        self.assertEqual(completed.status, "completed")
        self.assertIn("ending_sweet", completed.completed_endings)


if __name__ == "__main__":
    unittest.main()
