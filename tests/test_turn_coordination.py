from __future__ import annotations

import asyncio
import unittest

from companion_v01.turn_coordination import TurnCoordinator


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


if __name__ == "__main__":
    unittest.main()
