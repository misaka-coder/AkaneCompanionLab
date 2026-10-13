"""Comprehensive unit tests for Gomoku board, evaluator, and session management."""

import asyncio
import time
import unittest

from akane_gomoku.board import (
    BOARD_SIZE,
    EMPTY,
    BLACK,
    WHITE,
    Board,
    coord_to_notation,
    notation_to_coord,
)
from akane_gomoku.evaluator import GomokuEvaluator, SCORE_FIVE, SCORE_OPEN_FOUR
from akane_gomoku.session import GomokuSessionManager


class GomokuBoardTests(unittest.TestCase):
    def test_coordinate_and_notation_roundtrip(self):
        for x in range(BOARD_SIZE):
            for y in range(BOARD_SIZE):
                notation = coord_to_notation(x, y)
                rx, ry = notation_to_coord(notation)
                self.assertEqual((rx, ry), (x, y))

        self.assertEqual(coord_to_notation(7, 7), "H8")
        self.assertEqual(coord_to_notation(0, 0), "A1")
        self.assertEqual(coord_to_notation(14, 14), "O15")

        # 支持 [H8] 形式
        self.assertEqual(notation_to_coord("[H8]"), (7, 7))
        self.assertEqual(notation_to_coord("h8"), (7, 7))

        with self.assertRaises(ValueError):
            coord_to_notation(-1, 0)
        with self.assertRaises(ValueError):
            coord_to_notation(15, 0)
        with self.assertRaises(ValueError):
            notation_to_coord("Z99")

    def test_win_detection_four_directions(self):
        # 1. 水平连五
        b1 = Board.create()
        for i in range(5):
            b1.place_piece(i + 3, 7, BLACK)
        self.assertTrue(b1.check_win(7, 7, BLACK))
        self.assertFalse(b1.check_win(7, 7, WHITE))

        # 2. 垂直连五
        b2 = Board.create()
        for i in range(5):
            b2.place_piece(7, i + 3, WHITE)
        self.assertTrue(b2.check_win(7, 7, WHITE))

        # 3. 主对角线 (↘) 连五
        b3 = Board.create()
        for i in range(5):
            b3.place_piece(i + 2, i + 2, BLACK)
        self.assertTrue(b3.check_win(4, 4, BLACK))

        # 4. 副对角线 (↗) 连五
        b4 = Board.create()
        for i in range(5):
            b4.place_piece(6 + i, 6 - i, WHITE)
        self.assertTrue(b4.check_win(8, 4, WHITE))

    def test_active_bounding_box_ascii(self):
        board = Board.create()
        # 空盘渲染聚焦天元 H8
        empty_ascii = board.render_ascii()
        self.assertIn("行 6~10，列 F~J", empty_ascii)
        self.assertIn("H8", empty_ascii)

        # 玩家落子在 H8 (7, 7)
        board.place_piece(7, 7, BLACK)
        ascii_view = board.render_ascii(padding=1, character_name="塞西莉亚")
        self.assertIn("玩家刚落在 [H8]", ascii_view)
        self.assertIn("●", ascii_view)

        # 伴侣落子在 H9 (7, 8)
        board.place_piece(7, 8, WHITE)
        ascii_view2 = board.render_ascii(padding=1, character_name="塞西莉亚")
        self.assertIn("塞西莉亚刚落在 [H9]", ascii_view2)
        self.assertIn("○", ascii_view2)


class GomokuEvaluatorTests(unittest.TestCase):
    def test_threat_detection_open_three_and_win(self):
        board = Board.create()
        # 黑棋连三: (5, 7), (6, 7), (7, 7) -> F8, G8, H8
        board.place_piece(5, 7, BLACK)
        board.place_piece(6, 7, BLACK)
        board.place_piece(7, 7, BLACK)

        # 白棋评估局势
        report = GomokuEvaluator.evaluate_board(board, actor_color=WHITE)
        threats = report["threats"]
        self.assertTrue(any(t.urgency in ("high", "lethal") for t in threats))

        # 检查建议走法
        suggestions = GomokuEvaluator.format_suggestions(board, count=3, actor_color=WHITE)
        self.assertIn("生死必堵", suggestions)

    def test_dual_purpose_and_opportunity(self):
        board = Board.create()
        # 白棋在第 7 行有跳二 (5, 6), (7, 6)
        board.place_piece(5, 6, WHITE)
        board.place_piece(7, 6, WHITE)
        # 黑棋在第 6 列有纵向连二 (6, 5), (6, 7)
        board.place_piece(6, 5, BLACK)
        board.place_piece(6, 7, BLACK)

        report_text = GomokuEvaluator.format_analysis_report(board, actor_color=WHITE, character_name="Akane")
        self.assertIn("战术评估报告", report_text)
        self.assertIn("Akane", report_text)

    def test_evaluator_speed_benchmark_under_3ms(self):
        board = Board.create()
        # 模拟一场进行了 20 手的中盘棋局
        moves = [
            (7, 7, BLACK), (7, 8, WHITE),
            (8, 7, BLACK), (6, 6, WHITE),
            (8, 8, BLACK), (6, 8, WHITE),
            (9, 9, BLACK), (5, 5, WHITE),
            (8, 6, BLACK), (8, 5, WHITE),
            (9, 7, BLACK), (7, 9, WHITE),
            (10, 7, BLACK), (6, 7, WHITE),
            (10, 6, BLACK), (9, 5, WHITE),
            (10, 8, BLACK), (6, 9, WHITE),
            (11, 7, BLACK), (5, 9, WHITE),
        ]
        for x, y, c in moves:
            board.place_piece(x, y, c)

        # 预热一次
        GomokuEvaluator.evaluate_board(board, actor_color=WHITE)

        # 执行 50 次取平均时间
        iterations = 50
        start = time.perf_counter()
        for _ in range(iterations):
            GomokuEvaluator.evaluate_board(board, actor_color=WHITE)
        elapsed = time.perf_counter() - start
        avg_time = elapsed / iterations

        print(f"\n[Gomoku Benchmark] 20-move midgame evaluation average time: {avg_time * 1000:.3f} ms")
        # 严格断言在 3 毫秒以内
        self.assertLess(avg_time, 0.003, f"Evaluator exceeded 3ms threshold: {avg_time * 1000:.3f} ms")


class GomokuSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_optimistic_version_and_turn_locking(self):
        mgr = GomokuSessionManager()
        session = await mgr.get_or_create(session_id="s1", character_pack_id="pack_cecilia")

        self.assertEqual(session.state_version, 1)
        self.assertEqual(session.active_color, BLACK)

        # 1. 尝试白棋抢先出招 -> not_your_turn
        res1 = await mgr.place_piece(session, x=7, y=7, expected_version=1, color=WHITE)
        self.assertFalse(res1["ok"])
        self.assertEqual(res1["reason"], "not_your_turn")

        # 2. 正常黑棋第一手 H8 (7, 7)
        res2 = await mgr.place_piece(session, x=7, y=7, expected_version=1, color=BLACK)
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["version"], 2)
        self.assertEqual(session.active_color, WHITE)

        # 3. 携带过期版本号 1 再次尝试落子 -> stale_version_rejected
        res3 = await mgr.place_piece(session, x=7, y=8, expected_version=1, color=WHITE)
        self.assertFalse(res3["ok"])
        self.assertEqual(res3["reason"], "stale_version_rejected")

        # 4. 白棋携带正确版本号 2 正常落子 H9 (7, 8)
        res4 = await mgr.place_piece(session, x=7, y=8, expected_version=2, color=WHITE)
        self.assertTrue(res4["ok"])
        self.assertEqual(res4["version"], 3)
        self.assertEqual(session.active_color, BLACK)

    async def test_session_isolation_across_character_packs(self):
        mgr = GomokuSessionManager()
        # 同一个 session_id，不同 character_pack_id
        session_cecilia = await mgr.get_or_create(session_id="s1", character_pack_id="cecilia")
        session_akane = await mgr.get_or_create(session_id="s1", character_pack_id="akane")

        # 塞西莉亚局下了一颗子
        await mgr.place_piece(session_cecilia, x=7, y=7, expected_version=1, color=BLACK)

        self.assertEqual(len(session_cecilia.board.moves), 1)
        self.assertEqual(len(session_akane.board.moves), 0)
        self.assertEqual(session_akane.state_version, 1)

    async def test_undo_and_reset(self):
        mgr = GomokuSessionManager()
        session = await mgr.get_or_create(session_id="s2", character_pack_id="test")

        # 下 2 手棋
        await mgr.place_piece(session, x=7, y=7, expected_version=1, color=BLACK)
        await mgr.place_piece(session, x=7, y=8, expected_version=2, color=WHITE)
        self.assertEqual(len(session.board.moves), 2)
        self.assertEqual(session.active_color, BLACK)

        # 悔棋 2 步
        undo_res = await mgr.undo(session, steps=2)
        self.assertTrue(undo_res["ok"])
        self.assertEqual(len(session.board.moves), 0)
        self.assertEqual(session.active_color, BLACK)
        self.assertEqual(undo_res["version"], 4)

        # 重开
        reset_res = await mgr.reset(session)
        self.assertTrue(reset_res["ok"])
        self.assertEqual(session.state_version, 5)

        # 重开并指定玩家执白 (player_color=WHITE)
        reset_white = await mgr.reset(session, player_color=WHITE)
        self.assertTrue(reset_white["ok"])
        self.assertEqual(session.player_color, WHITE)
        self.assertEqual(session.companion_color, BLACK)
        self.assertEqual(session.active_color, BLACK)


if __name__ == "__main__":
    unittest.main()
