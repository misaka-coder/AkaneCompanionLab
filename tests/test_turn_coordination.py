from __future__ import annotations

import asyncio
import unittest

from companion_v01.turn_coordination import SessionWorkQueue, TurnCoordinator


class TurnCoordinatorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
