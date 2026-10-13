"""Gomoku multi-session manager with asyncio locks and optimistic version control."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .board import Board, BLACK, WHITE, EMPTY, coord_to_notation


@dataclass
class GameSession:
    game_id: str
    session_id: str
    character_pack_id: str
    board: Board = field(default_factory=Board.create)
    state_version: int = 1
    active_color: int = BLACK  # 默认黑棋先手
    player_color: int = BLACK  # 默认玩家执黑
    status: str = "in_progress"  # "in_progress", "black_won", "white_won", "draw"
    winner: int | None = None
    character_name: str = "同伴"
    mode: str = "duel"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def companion_color(self) -> int:
        return WHITE if self.player_color == BLACK else BLACK


class GomokuSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str], GameSession] = {}
        self._active_games: dict[tuple[str, str], str] = {}
        self._manager_lock = asyncio.Lock()

    def resolve_key(self, session_id: str, character_pack_id: str, game_id: str = "") -> tuple[str, str, str]:
        """Resolve isolated unique key per session and character pack."""
        clean_sid = str(session_id or "default").strip()
        clean_cid = str(character_pack_id or "default").strip()
        clean_gid = str(game_id or "").strip()
        return clean_sid, clean_cid, clean_gid

    async def get_or_create(
        self,
        session_id: str = "",
        character_pack_id: str = "",
        game_id: str = "",
    ) -> GameSession:
        async with self._manager_lock:
            scope = self.resolve_key(session_id, character_pack_id)[:2]
            resolved_gid = game_id or self._active_games.get(scope) or f"gomoku-{len(self._sessions) + 1:04d}"
            key = self.resolve_key(session_id, character_pack_id, resolved_gid)
            if key not in self._sessions:
                self._sessions[key] = GameSession(
                    game_id=resolved_gid,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            self._active_games[scope] = resolved_gid
            return self._sessions[key]

    def find(self, session_id: str, character_pack_id: str, game_id: str) -> GameSession | None:
        return self._sessions.get(self.resolve_key(session_id, character_pack_id, game_id))

    async def snapshot(self, session: GameSession) -> dict[str, Any]:
        async with session.lock:
            return {
                "gameId": session.game_id, "version": session.state_version,
                "grid": [row[:] for row in session.board.grid],
                "moves": [vars(move).copy() for move in session.board.moves],
                "activeColor": session.active_color,
                "playerColor": session.player_color,
                "status": session.status,
                "winner": session.winner, "mode": session.mode,
                "characterName": session.character_name,
                "boardText": session.board.render_ascii(character_name=session.character_name, player_color=session.player_color),
            }

    async def place_piece(
        self,
        session: GameSession,
        x: int,
        y: int,
        expected_version: int,
        color: int,
    ) -> dict[str, Any]:
        """Atomically place a piece with strict optimistic locking and turn checks."""
        async with session.lock:
            if session.status != "in_progress":
                winner_name = "玩家" if session.winner == session.player_color else session.character_name
                winner_color = "黑方" if session.winner == BLACK else "白方"
                return {
                    "ok": False,
                    "reason": "game_already_ended",
                    "message": f"对局已结束（胜者: {winner_color}({winner_name})），请点击[重开]开始新对局。",
                    "version": session.state_version,
                }

            if expected_version != session.state_version:
                return {
                    "ok": False,
                    "reason": "stale_version_rejected",
                    "message": f"落子版本号冲突（预期 {expected_version}，实际 {session.state_version}），说明盘面已有更新或已悔棋，请刷新棋盘后重试。",
                    "version": session.state_version,
                }

            if color != session.active_color:
                turn_str = "玩家" if session.active_color == session.player_color else session.character_name
                stone_str = "黑棋" if session.active_color == BLACK else "白棋"
                return {
                    "ok": False,
                    "reason": "not_your_turn",
                    "message": f"当前并非该持方回合（当前应由 {turn_str}{stone_str} 落子）。",
                    "version": session.state_version,
                }

            placed, err = session.board.place_piece(x, y, color)
            if not placed:
                return {
                    "ok": False,
                    "reason": "invalid_move",
                    "message": err,
                    "version": session.state_version,
                }

            # 校验胜负与和棋
            is_win = session.board.check_win(x, y, color)
            if is_win:
                session.status = "black_won" if color == BLACK else "white_won"
                session.winner = color
            elif session.board.is_full():
                session.status = "draw"
                session.winner = None
            else:
                session.active_color = WHITE if color == BLACK else BLACK

            session.state_version += 1
            notation = coord_to_notation(x, y)

            return {
                "ok": True,
                "x": x,
                "y": y,
                "notation": notation,
                "color": color,
                "version": session.state_version,
                "status": session.status,
                "winner": session.winner,
                "turn": len(session.board.moves),
            }

    async def undo(self, session: GameSession, steps: int = 2) -> dict[str, Any]:
        """Undo recent moves."""
        async with session.lock:
            actual_steps = min(steps, len(session.board.moves))
            undone_moves = []
            for _ in range(actual_steps):
                m = session.board.undo_last_move()
                if m:
                    undone_moves.append(m)

            if not undone_moves:
                return {
                    "ok": False,
                    "reason": "no_moves_to_undo",
                    "message": "当前棋盘为空，无法悔棋。",
                    "version": session.state_version,
                }

            # 恢复游戏进行状态
            session.status = "in_progress"
            session.winner = None
            if session.board.moves:
                # 下一手颜色为最后一手落子的相反方
                last = session.board.moves[-1]
                session.active_color = WHITE if last.color == BLACK else BLACK
            else:
                session.active_color = BLACK

            session.state_version += 1

            return {
                "ok": True,
                "undone_count": len(undone_moves),
                "remaining_moves": len(session.board.moves),
                "version": session.state_version,
                "active_color": session.active_color,
            }

    async def reset(self, session: GameSession, *, mode: str | None = None, player_color: int | None = None) -> dict[str, Any]:
        """Reset the game board for a fresh game."""
        async with session.lock:
            session.board = Board.create()
            session.active_color = BLACK
            session.status = "in_progress"
            session.winner = None
            if mode is not None:
                session.mode = mode
            if player_color in (BLACK, WHITE):
                session.player_color = player_color
            session.state_version += 1
            return {
                "ok": True,
                "version": session.state_version,
                "status": session.status,
            }
