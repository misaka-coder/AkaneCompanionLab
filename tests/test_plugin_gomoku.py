"""Exercise the real SDK adapter used by desktop and model invocations."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "akane_gomoku" / "src"))

from akane_plugin.contracts import PluginInvocationContext
from akane_gomoku import create_plugin

UI_CAP = "akane.gomoku.service.board.v1.control"


class GomokuPluginIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = create_plugin()
        adapters = []
        self.plugin.register(SimpleNamespace(add_capability_adapter=adapters.append))
        self.adapter = adapters[0]
        self.ui = PluginInvocationContext("master", "master", "plugin_admin", character_pack_id="cecilia")
        self.model = PluginInvocationContext("master", "master", "desktop_pet", character_pack_id="cecilia")

    async def board(self, action="state", **kwargs):
        return await self.adapter.invoke(UI_CAP, {"action": action, "game_id": "test-board", **kwargs}, self.ui)

    async def move(self, x, y, version, **kwargs):
        return await self.adapter.invoke("akane.gomoku.place_piece",
                                         {"x": x, "y": y, "version": version, "game_id": "test-board", **kwargs},
                                         self.model)

    async def test_real_sdk_context_shares_player_and_model_board(self):
        opened = await self.board("open", character_name="塞西莉亚")
        self.assertFalse(opened.is_error)
        self.assertEqual(opened.content["state"]["version"], 1)
        player = await self.board("play", x=7, y=14, version=1)
        self.assertEqual(player.content["state"]["moves"][0]["notation"], "H15")
        state = await self.adapter.invoke("akane.gomoku.get_game_state", {"game_id": "test-board"}, self.model)
        self.assertIn("H15", state.content)
        self.assertIn("塞西莉亚执白", state.content)
        white = await self.move(8, 14, 2)
        self.assertFalse(white.is_error)
        refreshed = (await self.board()).content["state"]
        self.assertEqual(refreshed["grid"][14][8], 2)
        self.assertEqual(refreshed["activeColor"], 1)
        self.assertEqual(refreshed["version"], 3)
        # Default state lookup refers to the same active game, not a second board.
        default = await self.adapter.invoke("akane.gomoku.get_game_state", {}, self.model)
        self.assertIn("version=3", default.content)

    async def test_duplicate_and_out_of_turn_moves_are_rejected(self):
        await self.board("open")
        premature = await self.move(7, 7, 1)
        self.assertTrue(premature.is_error)
        self.assertEqual(premature.reason, "not_your_turn")
        results = await __import__("asyncio").gather(
            self.board("play", x=7, y=7, version=1), self.board("play", x=7, y=7, version=1))
        self.assertEqual(sum(not result.is_error for result in results), 1)
        self.assertEqual(len((await self.board()).content["state"]["moves"]), 1)
        await self.move(7, 8, 2)
        again = await self.move(8, 8, 3)
        self.assertTrue(again.is_error)
        self.assertEqual(again.reason, "not_your_turn")

    async def test_reset_and_pending_undo_reject_late_model_move(self):
        await self.board("open")
        await self.board("play", x=0, y=0, version=1)
        undone = await self.board("undo")
        self.assertEqual(undone.content["state"]["moves"], [])
        self.assertTrue((await self.move(1, 1, 2)).is_error)
        await self.board("play", x=0, y=0, version=3)
        reset = await self.board("reset")
        self.assertEqual(reset.content["state"]["version"], 5)
        self.assertTrue((await self.move(1, 1, 4)).is_error)
        self.assertEqual((await self.board()).content["state"]["moves"], [])

    async def test_profile_session_character_and_plugin_instance_isolation(self):
        await self.board("open")
        await self.board("play", x=7, y=7, version=1)
        for context in (
            PluginInvocationContext("visitor", "master", "desktop_pet", character_pack_id="cecilia"),
            PluginInvocationContext("master", "other", "desktop_pet", character_pack_id="cecilia"),
            PluginInvocationContext("master", "master", "desktop_pet", character_pack_id="reimu"),
        ):
            result = await self.adapter.invoke("akane.gomoku.get_game_state", {"game_id": "test-board"}, context)
            self.assertTrue(result.is_error)
            self.assertEqual(result.reason, "game_not_found")
        another = create_plugin()
        adapters = []
        another.register(SimpleNamespace(add_capability_adapter=adapters.append))
        missing = await adapters[0].invoke("akane.gomoku.get_game_state", {"game_id": "test-board"}, self.model)
        self.assertTrue(missing.is_error)

    async def test_assist_mode_and_service_are_separate_from_model_tools(self):
        descriptors = {item.descriptor.id: item.descriptor for item in self.plugin._functions}
        self.assertFalse(descriptors[UI_CAP].prompt_exposed)
        self.assertTrue(descriptors["akane.gomoku.place_piece"].prompt_exposed)
        forbidden = await self.adapter.invoke(UI_CAP, {"action": "open"}, self.model)
        self.assertTrue(forbidden.is_error)
        await self.board("open")
        await self.board("reset", mode="assist")
        await self.board("play", x=7, y=7, version=2)
        self.assertEqual((await self.move(7, 8, 3)).reason, "advice_only")
        computer = await self.board("computer_move", x=7, y=8, version=3)
        self.assertFalse(computer.is_error)
        self.assertEqual(computer.content["state"]["activeColor"], 1)

    async def test_invalid_coordinates_and_complete_win(self):
        await self.board("open")
        invalid = await self.board("play", x=15, y=0, version=1)
        self.assertTrue(invalid.is_error)
        for index in range(5):
            version = (await self.board()).content["state"]["version"]
            await self.board("play", x=index, y=0, version=version)
            if index < 4:
                await self.move(index, 2, version + 1)
        snapshot = (await self.board()).content["state"]
        self.assertEqual(snapshot["status"], "black_won")
        self.assertTrue((await self.move(7, 7, snapshot["version"])).is_error)

    async def test_player_white_and_companion_black_flow(self):
        opened = await self.board("open", character_name="塞西莉亚", player_color=2)
        self.assertFalse(opened.is_error)
        state_snap = opened.content["state"]
        self.assertEqual(state_snap["playerColor"], 2)
        self.assertEqual(state_snap["activeColor"], 1)

        # 玩家执白，但当前是黑棋回合（伴侣先手），玩家抢先落子被拒
        player_premature = await self.board("play", x=7, y=8, version=1)
        self.assertTrue(player_premature.is_error)
        self.assertEqual(player_premature.reason, "not_your_turn")

        # 伴侣作为黑方先手落第一颗黑子于天元 H8
        companion_first = await self.move(7, 7, 1)
        self.assertFalse(companion_first.is_error)
        refreshed = (await self.board()).content["state"]
        self.assertEqual(refreshed["moves"][0]["color"], 1)
        self.assertEqual(refreshed["moves"][0]["notation"], "H8")
        self.assertEqual(refreshed["activeColor"], 2)

        # 轮到玩家落白子
        player_move = await self.board("play", x=7, y=8, version=2)
        self.assertFalse(player_move.is_error)
        after_player = player_move.content["state"]
        self.assertEqual(after_player["moves"][1]["color"], 2)
        self.assertEqual(after_player["activeColor"], 1)

        # 读取游戏状态文本，确认身份和持子标注准确
        state_text = (await self.adapter.invoke("akane.gomoku.get_game_state", {"game_id": "test-board"}, self.model)).content
        self.assertIn("玩家执白 ○", state_text)
        self.assertIn("塞西莉亚执黑 ●", state_text)


if __name__ == "__main__":
    unittest.main()
