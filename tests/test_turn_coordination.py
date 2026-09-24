from __future__ import annotations

import asyncio
import unittest

from companion_v01.turn_coordination import SessionWorkQueue, TurnCoordinator


class TurnCoordinatorTests(unittest.TestCase):
    def test_addressed_input_preempts_optional_attention_instead_of_becoming_steer(self) -> None:
        async def run() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold(
                "profile",
                "session",
                actor_id="qq:42",
                channel="qq",
                turn_kind="qq_attention",
            ) as token:
                result = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="qq:42",
                    content="@Akane 这是明确的新消息",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "preempting_optional_turn")
                self.assertEqual(result["turn_token"], token)
                drained = coordinator.drain(token)
                self.assertTrue(drained["stop_requested"])
                self.assertEqual(drained["stop_reason"], "addressed_input_preempts_optional_turn")
                self.assertEqual(drained["steers"], [])

        asyncio.run(run())

    def test_same_actor_steers_active_turn_and_drain_coalesces_inputs(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold("profile", "session", actor_id="qq:1", channel="qq") as token:
                first = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="qq:1",
                    content="先别做页面，先补测试",
                    timestamp=100,
                    channel="qq",
                )
                second = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="qq:1",
                    content="测试跑通后再继续",
                    timestamp=101,
                    channel="qq",
                )
                self.assertTrue(first["ok"])
                self.assertEqual(second["pending_count"], 2)
                drained = coordinator.drain(token)
                self.assertEqual([item.content for item in drained["steers"]], [
                    "先别做页面，先补测试",
                    "测试跑通后再继续",
                ])
                self.assertEqual(coordinator.drain(token)["steers"], [])

        asyncio.run(exercise())

    def test_same_actor_steer_carries_ready_native_images_without_resolving_again(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            ready_image = {
                "attachment_id": "attachment-1",
                "attachment_handle": "img_001",
                "data_url": "data:image/png;base64,cGl4ZWxz",
            }
            async with coordinator.hold("profile", "session", actor_id="qq:1", channel="qq") as token:
                accepted = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="qq:1",
                    content="再看这张图",
                    native_user_images=[ready_image, {"data_url": "https://not-an-image.invalid"}],
                )
                self.assertTrue(accepted["ok"])
                self.assertEqual(accepted["native_image_count"], 1)
                steer = coordinator.drain(token)["steers"][0]
                self.assertEqual(steer.native_user_images, (ready_image,))
                self.assertIsNot(steer.native_user_images[0], ready_image)

        asyncio.run(exercise())

    def test_same_actor_steer_preserves_durable_receipt_identity(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold("profile", "session", actor_id="qq:1", channel="qq") as token:
                accepted = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="qq:1",
                    content="补一条持久化调整",
                    source_id="steer_inbox_1",
                    receipt_item_id="inbox_1",
                    receipt_claim_token="claim_1",
                )
                self.assertTrue(accepted["ok"])
                self.assertEqual(accepted["source_id"], "steer_inbox_1")
                steer = coordinator.drain(token)["steers"][0]
                self.assertEqual(steer.receipt_item_id, "inbox_1")
                self.assertEqual(steer.receipt_claim_token, "claim_1")

        asyncio.run(exercise())

    def test_different_actor_cannot_hijack_active_group_turn(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold("shared", "group", actor_id="qq:1", channel="qq"):
                result = coordinator.offer_steer(
                    profile_user_id="shared",
                    session_id="group",
                    actor_id="qq:2",
                    content="改成我的要求",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "busy_other_actor")

        asyncio.run(exercise())

    def test_addressed_input_preempts_plugin_event(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold(
                "shared",
                "group",
                actor_id="qq-profile:shared",
                channel="qq",
                turn_kind="plugin_event",
            ) as token:
                result = coordinator.offer_steer(
                    profile_user_id="shared",
                    session_id="group",
                    actor_id="qq:2",
                    content="现在先回答我",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "preempting_optional_turn")
                self.assertTrue(coordinator.drain(token)["stop_requested"])

        asyncio.run(exercise())

    def test_stop_is_requested_then_observed_at_safe_boundary(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold("profile", "session", actor_id="desktop:u", channel="desktop") as token:
                requested = coordinator.request_stop(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="desktop:u",
                )
                self.assertEqual(requested["status"], "requested")
                drained = coordinator.drain(token)
                self.assertTrue(drained["stop_requested"])

        asyncio.run(exercise())

    def test_finalization_rejects_late_steer_instead_of_losing_it(self) -> None:
        async def exercise() -> None:
            coordinator = TurnCoordinator()
            async with coordinator.hold("profile", "session", actor_id="desktop:u", channel="desktop") as token:
                self.assertEqual(coordinator.begin_finalization(token)["status"], "finalizing")
                result = coordinator.offer_steer(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="desktop:u",
                    content="再改一下",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "finalizing")
                stop = coordinator.request_stop(
                    profile_user_id="profile",
                    session_id="session",
                    actor_id="desktop:u",
                )
                self.assertFalse(stop["ok"])
                self.assertEqual(stop["status"], "finalizing")

        asyncio.run(exercise())

    def test_waiting_turns_remain_fifo(self) -> None:
        async def exercise() -> list[str]:
            coordinator = TurnCoordinator()
            order: list[str] = []
            release = asyncio.Event()
            entered = asyncio.Event()

            async def first() -> None:
                async with coordinator.hold("p", "s", actor_id="qq:1", channel="qq"):
                    order.append("first")
                    entered.set()
                    await release.wait()

            async def second() -> None:
                await entered.wait()
                async with coordinator.hold("p", "s", actor_id="qq:2", channel="qq"):
                    order.append("second")

            tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
            await entered.wait()
            await asyncio.sleep(0)
            self.assertEqual(order, ["first"])
            release.set()
            await asyncio.gather(*tasks)
            return order

        self.assertEqual(asyncio.run(exercise()), ["first", "second"])

    def test_same_session_never_runs_two_active_turns(self) -> None:
        async def exercise() -> tuple[int, list[str]]:
            coordinator = TurnCoordinator()
            active_count = 0
            max_active_count = 0
            order: list[str] = []
            first_entered = asyncio.Event()
            release_first = asyncio.Event()

            async def run_turn(name: str) -> None:
                nonlocal active_count, max_active_count
                async with coordinator.hold("profile", "session", actor_id=name, channel="qq"):
                    active_count += 1
                    max_active_count = max(max_active_count, active_count)
                    order.append(name)
                    if name == "first":
                        first_entered.set()
                        await release_first.wait()
                    active_count -= 1

            first = asyncio.create_task(run_turn("first"))
            await first_entered.wait()
            second = asyncio.create_task(run_turn("second"))
            await asyncio.sleep(0)
            self.assertEqual(order, ["first"])
            release_first.set()
            await asyncio.gather(first, second)
            return max_active_count, order

        self.assertEqual(asyncio.run(exercise()), (1, ["first", "second"]))

    def test_different_sessions_can_run_at_the_same_time(self) -> None:
        async def exercise() -> int:
            coordinator = TurnCoordinator()
            active_count = 0
            max_active_count = 0
            both_entered = asyncio.Event()
            release = asyncio.Event()

            async def run_turn(session_id: str) -> None:
                nonlocal active_count, max_active_count
                async with coordinator.hold("profile", session_id, actor_id="qq:1", channel="qq"):
                    active_count += 1
                    max_active_count = max(max_active_count, active_count)
                    if active_count == 2:
                        both_entered.set()
                    await release.wait()
                    active_count -= 1

            tasks = [
                asyncio.create_task(run_turn("session-a")),
                asyncio.create_task(run_turn("session-b")),
            ]
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            release.set()
            await asyncio.gather(*tasks)
            return max_active_count

        self.assertEqual(asyncio.run(exercise()), 2)

    def test_input_rejected_during_finalization_can_start_the_next_turn(self) -> None:
        async def exercise() -> list[str]:
            coordinator = TurnCoordinator()
            order: list[str] = []
            current_finalizing = asyncio.Event()
            release_current = asyncio.Event()

            async def current_turn() -> None:
                async with coordinator.hold(
                    "profile",
                    "session",
                    actor_id="desktop:u",
                    channel="desktop",
                ) as token:
                    order.append("current")
                    self.assertEqual(coordinator.begin_finalization(token)["status"], "finalizing")
                    rejected = coordinator.offer_steer(
                        profile_user_id="profile",
                        session_id="session",
                        actor_id="desktop:u",
                        content="这是下一轮输入",
                    )
                    self.assertEqual(rejected["status"], "finalizing")
                    current_finalizing.set()
                    await release_current.wait()

            async def next_turn() -> None:
                await current_finalizing.wait()
                async with coordinator.hold(
                    "profile",
                    "session",
                    actor_id="desktop:u",
                    channel="desktop",
                ):
                    order.append("next")

            tasks = [asyncio.create_task(current_turn()), asyncio.create_task(next_turn())]
            await current_finalizing.wait()
            await asyncio.sleep(0)
            self.assertEqual(order, ["current"])
            release_current.set()
            await asyncio.gather(*tasks)
            return order

        self.assertEqual(asyncio.run(exercise()), ["current", "next"])

    def test_session_work_queue_batches_only_adjacent_passive_items_in_fifo_order(self) -> None:
        async def exercise() -> list[tuple[str, list[str]]]:
            handled: list[tuple[str, list[str]]] = []
            first_started = asyncio.Event()
            release_first = asyncio.Event()
            drained = asyncio.Event()

            async def handler(_key, items) -> None:
                handled.append((items[0].kind, [str(item.payload) for item in items]))
                if items[0].payload == "turn-1":
                    first_started.set()
                    await release_first.wait()
                if items[0].payload == "turn-3":
                    drained.set()

            queue = SessionWorkQueue(handler, batchable_kinds={"passive"})
            first = queue.enqueue("group", kind="turn", payload="turn-1")
            self.assertTrue(first["ok"])
            await first_started.wait()
            queue.enqueue("group", kind="passive", payload="passive-1")
            queue.enqueue("group", kind="passive", payload="passive-2")
            queue.enqueue("group", kind="turn", payload="turn-2")
            queue.enqueue("group", kind="passive", payload="passive-3")
            queue.enqueue("group", kind="turn", payload="turn-3")
            self.assertTrue(queue.has_work("group"))
            self.assertEqual(queue.pending_count("group"), 5)
            release_first.set()
            await asyncio.wait_for(drained.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertFalse(queue.has_work("group"))
            return handled

        self.assertEqual(
            asyncio.run(exercise()),
            [
                ("turn", ["turn-1"]),
                ("passive", ["passive-1", "passive-2"]),
                ("turn", ["turn-2"]),
                ("passive", ["passive-3"]),
                ("turn", ["turn-3"]),
            ],
        )

    def test_session_work_queue_serializes_each_key_but_runs_keys_concurrently(self) -> None:
        async def exercise() -> tuple[int, list[str]]:
            active_count = 0
            max_active_count = 0
            order: list[str] = []
            both_keys_entered = asyncio.Event()
            release_first_batch = asyncio.Event()
            all_done = asyncio.Event()

            async def handler(key, items) -> None:
                nonlocal active_count, max_active_count
                active_count += 1
                max_active_count = max(max_active_count, active_count)
                order.append(str(items[0].payload))
                if active_count == 2:
                    both_keys_entered.set()
                if str(items[0].payload).endswith("-1"):
                    await release_first_batch.wait()
                active_count -= 1
                if len(order) == 4:
                    all_done.set()

            queue = SessionWorkQueue(handler)
            queue.enqueue("session-a", kind="turn", payload="a-1")
            queue.enqueue("session-a", kind="turn", payload="a-2")
            queue.enqueue("session-b", kind="turn", payload="b-1")
            queue.enqueue("session-b", kind="turn", payload="b-2")

            await asyncio.wait_for(both_keys_entered.wait(), timeout=1)
            self.assertNotIn("a-2", order)
            self.assertNotIn("b-2", order)
            release_first_batch.set()
            await asyncio.wait_for(all_done.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertLess(order.index("a-1"), order.index("a-2"))
            self.assertLess(order.index("b-1"), order.index("b-2"))
            return max_active_count, order

        max_active_count, order = asyncio.run(exercise())
        self.assertEqual(max_active_count, 2)
        self.assertCountEqual(order, ["a-1", "a-2", "b-1", "b-2"])


if __name__ == "__main__":
    unittest.main()
