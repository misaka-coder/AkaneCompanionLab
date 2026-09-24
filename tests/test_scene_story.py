import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from companion_v01.scene.contracts import Identity, Receipt, StoryStartRequest, StoryStepRequest
from companion_v01.scene.presentation.service import PresentationService
from companion_v01.scene.story.loader import StoryLoader
from companion_v01.scene.story.repository import StoryRepository
from companion_v01.scene.story.service import StoryService


class SceneStoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_rooms.sqlite3"
        self.repo = StoryRepository(self.db_path)
        self.mock_generated = []

        async def mock_generate(payload):
            self.mock_generated.append(payload)
            return {
                "beats": [
                    {"speech": "真的很好吃吗？太好啦！", "emotion_id": "脸红", "motion_id": "bounce"}
                ]
            }

        self.characters_dir = Path(self.tmp.name) / "characters"
        self.loader = StoryLoader(characters_dir=self.characters_dir, user_stories_dir=Path(self.tmp.name) / "stories")
        self.service = StoryService(
            self.repo,
            generate=mock_generate,
            loader=self.loader,
        )
        self.identity = Identity(
            profile_user_id="user_qa",
            session_id="session_story_01",
            character_pack_id="akane_v1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_story_catalog(self):
        catalog = self.service.catalog(self.identity)
        self.assertGreaterEqual(len(catalog.stories), 1)
        story = next(s for s in catalog.stories if s.story_id == "twilight_tea_party")
        self.assertEqual(story.title, "黄昏茶会")
        self.assertFalse(story.has_active_run)
        self.assertEqual(story.status, "not_started")

    async def test_story_start_and_choice_branches(self):
        # Start story
        run = self.service.start(self.identity, "twilight_tea_party")
        self.assertEqual(run.current_node_id, "intro_1")
        self.assertEqual(run.status, "in_progress")

        # Step 1: intro_1 -> intro_2
        step1 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="intro_1",
            )
        )
        self.assertEqual(step1.current_node_id, "intro_2")

        # Step 2: intro_2 -> choice_taste
        step2 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="intro_2",
            )
        )
        self.assertEqual(step2.current_node_id, "choice_taste")
        self.assertEqual(step2.current_node.kind, "choice")
        self.assertGreaterEqual(len(step2.current_node.options), 2)

        # Choice: pick branch_sweet
        step3 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_sweet",
            )
        )
        self.assertEqual(step3.current_node_id, "branch_sweet")

        # Advance to ending
        step4 = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="branch_sweet",
            )
        )
        self.assertEqual(step4.current_node_id, "ending_warmth")
        self.assertEqual(step4.status, "in_progress")
        completed = self.service.complete_ending(self.identity, run.run_id, "ending_warmth")
        self.assertEqual(completed.status, "completed")
        self.assertIn("ending_warmth", completed.completed_endings)
        with self.repo.read_connection() as db:
            events = db.execute("SELECT fact FROM outbox WHERE event_id LIKE 'story:%'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertIn('scene.story_completed', events[0][0])

    async def test_agent_node_free_dialogue_with_model_response(self):
        run = self.service.start(self.identity, "twilight_tea_party")
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_1"))
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_2"))

        # Choose agent option
        to_agent = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_agent",
            )
        )
        self.assertEqual(to_agent.current_node_id, "ai_reaction")
        self.assertEqual(to_agent.current_node.kind, "agent")

        # Player replies freely: "好吃哦"
        agent_reply = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="ai_reaction",
                user_message="好吃哦",
            )
        )
        self.assertEqual(agent_reply.current_node_id, "ai_reaction_reply")
        self.assertEqual(agent_reply.current_node.speech, "真的很好吃吗？太好啦！")
        self.assertEqual(agent_reply.current_node.emotion_id, "脸红")
        self.assertEqual(len(self.mock_generated), 1)
        self.assertIn("好吃哦", self.mock_generated[0]["message"])
        self.assertIn("黄昏茶会", self.mock_generated[0]["message"])

        # Advance from dynamic agent response to ending
        to_ending = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="ai_reaction_reply",
            )
        )
        self.assertEqual(to_ending.current_node_id, "ending_warmth")
        self.assertEqual(to_ending.status, "in_progress")
        completed = self.service.complete_ending(self.identity, run.run_id, "ending_warmth")
        self.assertEqual(completed.status, "completed")

    async def test_three_interrupt_points_and_resume_recovery(self):
        # Point 1: Waiting at script node
        run1 = self.service.start(self.identity, "twilight_tea_party")
        self.assertEqual(run1.current_node_id, "intro_1")

        # Simulate service restart / reload
        reloaded_service = StoryService(StoryRepository(self.db_path))
        resumed = reloaded_service.start(self.identity, "twilight_tea_party")
        self.assertEqual(resumed.run_id, run1.run_id)
        self.assertEqual(resumed.current_node_id, "intro_1")

        # Advance to Point 2: Waiting at choice node
        await reloaded_service.step(StoryStepRequest(identity=self.identity, run_id=run1.run_id, expected_node_id="intro_1"))
        await reloaded_service.step(StoryStepRequest(identity=self.identity, run_id=run1.run_id, expected_node_id="intro_2"))

        # Recreate service again at choice node
        service3 = StoryService(StoryRepository(self.db_path))
        resumed_at_choice = service3.start(self.identity, "twilight_tea_party")
        self.assertEqual(resumed_at_choice.current_node_id, "choice_taste")
        self.assertEqual(resumed_at_choice.current_node.kind, "choice")

        # Point 3: Idempotent step with stale node_id does not corrupt
        stale_step = await service3.step(
            StoryStepRequest(identity=self.identity, run_id=run1.run_id, expected_node_id="intro_1")
        )
        self.assertEqual(stale_step.current_node_id, "choice_taste")

        # Can pick different branch: opt_tea
        step_tea = await service3.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run1.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_tea",
            )
        )
        self.assertEqual(step_tea.current_node_id, "branch_tea")

    async def test_reset_and_replay_branch(self):
        run = self.service.start(self.identity, "twilight_tea_party")
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_1"))
        self.service.reset(self.identity, "twilight_tea_party")
        fresh = self.service.start(self.identity, "twilight_tea_party")
        self.assertEqual(fresh.current_node_id, "intro_1")

    def test_import_story(self):
        md_content = """---
story_id: imported_test
title: 导入测试故事
description: 这是一个导入的故事。
cover_image: scenes/家/白天客厅.png
initial_node_id: s1
---

## s1 [script]
角色: Akane
下一幕: e1
测试对白

## e1 [ending]
结局标题: 完结
结局总结: 总结
结束
"""
        catalog = self.service.import_story(self.identity, "imported_test.md", md_content)
        self.assertTrue(any(s.story_id == "imported_test" for s in catalog.stories))
        imported = next(s for s in catalog.stories if s.story_id == "imported_test")
        self.assertEqual(imported.title, "导入测试故事")
        self.assertTrue(imported.cover_image.startswith("/scene/assets/"))

    def test_custom_cover_formats_and_non_akane_character(self):
        # 1. Custom cover: external URL
        md_ext = """---
story_id: ext_cover_story
title: 外部封面故事
cover_image: https://images.unsplash.com/photo-test.jpg
initial_node_id: s1
---
## s1 [script]
说话人: 弥奈
下一幕: e1
外部封面测试
## e1 [ending]
结局标题: 完
结束
"""
        catalog = self.service.import_story(self.identity, "ext_cover.md", md_ext)
        story = next(s for s in catalog.stories if s.story_id == "ext_cover_story")
        self.assertEqual(story.cover_image, "https://images.unsplash.com/photo-test.jpg")

        # 2. Custom cover: base64 data URL
        md_b64 = """---
story_id: b64_cover_story
title: Base64封面故事
cover_image: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==
initial_node_id: s1
---
## s1 [script]
说话人: 弥奈
下一幕: e1
Base64测试
## e1 [ending]
结局标题: 完
结束
"""
        catalog = self.service.import_story(self.identity, "b64_cover.md", md_b64)
        story = next(s for s in catalog.stories if s.story_id == "b64_cover_story")
        self.assertTrue(story.cover_image.startswith("data:image/png;base64,"))

        # 3. Custom cover image upload & placement in character stories folder
        dummy_png_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        self.service.import_story(self.identity, "custom_cover.png", dummy_png_b64)
        expected_img_path = self.characters_dir / self.identity.character_pack_id / "stories" / "custom_cover.png"
        self.assertTrue(expected_img_path.is_file())

        md_local_cover = """---
story_id: local_cover_story
title: 本地封面故事
cover_image: custom_cover.png
initial_node_id: s1
---
## s1 [script]
说话人: 弥奈
下一幕: e1
本地封面测试
## e1 [ending]
结局标题: 完
结束
"""
        catalog = self.service.import_story(self.identity, "local_cover.md", md_local_cover)
        story = next(s for s in catalog.stories if s.story_id == "local_cover_story")
        self.assertEqual(
            story.cover_image,
            f"/scene/assets/characters/{self.identity.character_pack_id}/stories/custom_cover.png",
        )

        # 4. Multi-character support: switch character to "sakura_v1"
        sakura_id = Identity(
            profile_user_id="user_qa",
            session_id="session_story_02",
            character_pack_id="sakura_v1",
        )
        md_sakura = """---
story_id: sakura_exclusive_story
title: 樱花下的约定
cover_image: scenes/家/白天客厅.png
initial_node_id: s1
---
## s1 [script]
说话人: 樱
下一幕: e1
你好，我是樱！
## e1 [ending]
结局标题: 约定达成
结束
"""
        sakura_catalog = self.service.import_story(sakura_id, "sakura_story.md", md_sakura)
        self.assertTrue(any(s.story_id == "sakura_exclusive_story" for s in sakura_catalog.stories))
        sakura_file = self.characters_dir / "sakura_v1" / "stories" / "sakura_story.md"
        self.assertTrue(sakura_file.is_file())

    async def test_agent_node_model_failure_does_not_fake_success(self):
        # Set mock generator to fail
        async def mock_failing_generator(payload):
            raise RuntimeError("model offline")

        self.service.generate = mock_failing_generator

        run = self.service.start(self.identity, "twilight_tea_party")
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_1"))
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_2"))
        await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_agent",
            )
        )

        # Calling step when model is offline should raise ValueError, NOT fake a biscuit reply
        with self.assertRaises(ValueError) as ctx:
            await self.service.step(
                StoryStepRequest(
                    identity=self.identity,
                    run_id=run.run_id,
                    expected_node_id="ai_reaction",
                    user_message="我们明天去郊游吧",
                )
            )
        self.assertIn("model_generation_failed", str(ctx.exception))

        # Check repository state: run remains at ai_reaction, did NOT advance to reply or fake biscuits
        with self.service.repository.read_connection() as db:
            current_run = self.service.repository.get_run(db, self.identity, "twilight_tea_party")
            self.assertEqual(current_run.current_node_id, "ai_reaction")
            self.assertNotIn("饼干微焦", current_run.current_node.speech)

    async def test_agent_node_preserves_multiple_beats_and_emotions(self):
        # Return 3 distinct beats
        async def mock_multi_beat_generator(payload):
            return {
                "beats": [
                    {"speech": "第一句在这里。", "emotion_id": "normal", "motion_id": "idle"},
                    {"speech": "第二句有些害羞……", "emotion_id": "shy", "motion_id": "nod"},
                    {"speech": "第三句其实很得意！", "emotion_id": "smug", "motion_id": "bounce"},
                ]
            }

        self.service.generate = mock_multi_beat_generator

        run = self.service.start(self.identity, "twilight_tea_party")
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_1"))
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_2"))
        await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_agent",
            )
        )

        reply_step = await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="ai_reaction",
                user_message="测试多表情",
            )
        )
        self.assertEqual(len(reply_step.current_node.beats), 3)
        self.assertEqual(reply_step.current_node.beats[0].emotion_id, "normal")
        self.assertEqual(reply_step.current_node.beats[1].emotion_id, "shy")
        self.assertEqual(reply_step.current_node.beats[2].emotion_id, "smug")
        self.assertEqual(reply_step.current_node.beats[2].speech, "第三句其实很得意！")

    async def test_concurrent_step_does_not_deadlock_during_model_generation(self):
        # Simulate long-running LLM generation
        async def slow_generator(payload):
            await asyncio.sleep(0.2)
            return {"beats": [{"speech": "终于生成完啦！", "emotion_id": "normal"}]}

        self.service.generate = slow_generator

        run = self.service.start(self.identity, "twilight_tea_party")
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_1"))
        await self.service.step(StoryStepRequest(identity=self.identity, run_id=run.run_id, expected_node_id="intro_2"))
        await self.service.step(
            StoryStepRequest(
                identity=self.identity,
                run_id=run.run_id,
                expected_node_id="choice_taste",
                choice_id="opt_agent",
            )
        )

        task1 = asyncio.create_task(
            self.service.step(
                StoryStepRequest(
                    identity=self.identity,
                    run_id=run.run_id,
                    expected_node_id="ai_reaction",
                    user_message="慢速生成测试",
                )
            )
        )
        # Small delay to ensure task1 enters generation
        await asyncio.sleep(0.05)

        # While task1 is waiting for LLM, concurrent DB read/write must not deadlock or fail
        catalog = self.service.catalog(self.identity)
        self.assertTrue(len(catalog.stories) > 0)

        res = await task1
        self.assertEqual(res.current_node_id, "ai_reaction_reply")
        self.assertEqual(res.current_node.speech, "终于生成完啦！")

    async def test_story_node_registers_canonical_presentation_and_processes_receipts(self):
        run = self.service.start(self.identity, "twilight_tea_party")
        self.assertIsNotNone(run.presentation)
        self.assertTrue(run.presentation.turn_id.startswith("story_"))
        self.assertEqual(len(run.presentation.beats), 1)
        self.assertEqual(run.presentation.beats[0].speech, run.current_node.speech)

        # 1. Verify presentation is recorded in scene_turns with completed status
        with self.repo.read_connection() as db:
            row = db.execute(
                "SELECT status, presentation FROM scene_turns WHERE turn_id=?",
                (run.presentation.turn_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "completed")

        # 2. Verify receipt processing goes through canonical PresentationService without bypass
        class DummyRoom:
            def __init__(self, repo):
                self.repository = repo
            def flush_events(self):
                pass

        pres_service = PresentationService(room=DummyRoom(self.repo), generate=lambda _: None)

        receipt_started = Receipt(
            identity=self.identity,
            turn_id=run.presentation.turn_id,
            beat_id=run.presentation.beats[0].beat_id,
            generation=run.presentation.generation,
            kind="display_started",
        )
        res1 = pres_service.receipt(receipt_started)
        self.assertTrue(res1["ok"])
        self.assertFalse(res1["duplicate"])

        receipt_revealed = receipt_started.model_copy(update={"kind": "text_revealed"})
        res2 = pres_service.receipt(receipt_revealed)
        self.assertTrue(res2["ok"])

        # 3. Verify delivery outbox fact was written
        with self.repo.read_connection() as db:
            outbox_row = db.execute(
                "SELECT fact FROM outbox WHERE event_id=?",
                (f"receipt:{receipt_revealed.beat_id}:text_revealed",),
            ).fetchone()
            self.assertIsNotNone(outbox_row)
            self.assertIn("scene.delivery", outbox_row[0])


if __name__ == "__main__":
    unittest.main()
