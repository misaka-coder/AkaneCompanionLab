"""Gomoku companion game plugin for Akane Companion Lab."""

from __future__ import annotations

import json
from typing import Any

from akane_plugin import Plugin, ToolContext, Result

from .board import BLACK, WHITE, coord_to_notation
from .evaluator import GomokuEvaluator
from .session import GomokuSessionManager

def _normalize_color(val: Any, default: int = BLACK) -> int:
    if val in (BLACK, WHITE):
        return int(val)
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("black", "1", "b"):
            return BLACK
        if low in ("white", "2", "w"):
            return WHITE
    return default

def resolve_session_and_pack(ctx: ToolContext) -> tuple[str, str]:
    """Use the real SDK invocation identity, separately from memory storage."""
    inv = ctx.invocation
    session_id = str(inv.session_id or "").strip() or "default"
    character_pack_id = str(getattr(inv, "character_pack_id", "") or "").strip() or "default"
    profile = str(inv.profile_user_id or "")
    return json.dumps([profile, session_id], separators=(",", ":")), character_pack_id


def create_plugin() -> Plugin:
    plugin = Plugin("akane.gomoku")
    _session_manager = GomokuSessionManager()

    async def current_session(game_id, ctx):
        sid, cid = resolve_session_and_pack(ctx)
        if game_id:
            return _session_manager.find(sid, cid, game_id)
        return await _session_manager.get_or_create(session_id=sid, character_pack_id=cid)

    def missing_game():
        return Result(is_error=True, status="not_found", reason="game_not_found",
                      content="这盘棋不属于当前会话或已经失效，请获取当前棋盘。")

    @plugin.service("board").method(description="桌面棋盘的状态与玩家操作；不作为模型工具公开。")
    async def control(action: str = "state", game_id: str = "", x: int = -1, y: int = -1,
                      version: int = 0, mode: str = "duel", character_name: str = "同伴",
                      player_color: Any = BLACK,
                      ctx: ToolContext = None):
        if getattr(getattr(ctx, "invocation", None), "client_mode", "") != "plugin_admin":
            return Result(is_error=True, status="rejected", reason="board_ui_only", content="请通过棋盘操作。")
        if mode not in ("duel", "assist"):
            return Result(is_error=True, status="validation_error", reason="invalid_mode")
        norm_player_color = _normalize_color(player_color, default=BLACK)
        if action == "open":
            sid, cid = resolve_session_and_pack(ctx)
            session = await _session_manager.get_or_create(session_id=sid, character_pack_id=cid, game_id=game_id)
            session.character_name = str(character_name or "同伴").strip()[:80]
            if player_color is not None:
                session.player_color = norm_player_color
        else:
            session = await current_session(game_id, ctx)
        if session is None:
            return missing_game()
        result = {"ok": True}
        if action == "play":
            result = await _session_manager.place_piece(session, x, y, version, session.player_color)
        elif action == "computer_move":
            if session.mode != "assist":
                result = {"ok": False, "reason": "not_computer_mode", "message": "当前应由伴侣落子。"}
            else:
                result = await _session_manager.place_piece(session, x, y, version, session.companion_color)
        elif action == "undo":
            steps = 1 if session.active_color != session.player_color and session.status == "in_progress" else 2
            result = await _session_manager.undo(session, steps)
        elif action == "reset":
            result = await _session_manager.reset(session, mode=mode, player_color=norm_player_color)
        elif action not in ("open", "state"):
            result = {"ok": False, "reason": "unknown_board_action", "message": "未知棋盘操作。"}
        snapshot = await _session_manager.snapshot(session)
        return Result(is_error=not result["ok"], status="ok" if result["ok"] else "rejected",
                      reason=result.get("reason", ""), content={"state": snapshot, "message": result.get("message", "")})

    @plugin.tool(
        name="get_game_state",
        description="读取当前五子棋盘、执子方和 version。字母 A~O 对应 x=0~14，行 1~15 对应 y=0~14。",
        risk="low",
    )
    async def get_game_state(game_id: str = "", ctx: ToolContext = None) -> str:
        session = await current_session(game_id, ctx)
        if session is None:
            return missing_game()
        character_name = session.character_name

        turn = len(session.board.moves)
        last = session.board.last_move
        last_str = f"{'玩家' if last.color == session.player_color else character_name}落子于 [{last.notation}]" if last else "开局尚未落子"

        player_stone = "黑 ●" if session.player_color == BLACK else "白 ○"
        comp_stone = "白 ○" if session.companion_color == WHITE else "黑 ●"

        if session.status == "black_won":
            winner_role = "玩家" if session.player_color == BLACK else character_name
            status_desc = f"黑方({winner_role})获胜！"
        elif session.status == "white_won":
            winner_role = "玩家" if session.player_color == WHITE else character_name
            status_desc = f"白方({winner_role})获胜！"
        elif session.status == "draw":
            status_desc = "棋盘已满，双方平局。"
        else:
            if session.active_color == session.player_color:
                turn_desc = f"玩家{'黑棋 ●' if session.player_color == BLACK else '白棋 ○'}"
            else:
                turn_desc = f"{character_name}{'白棋 ○' if session.companion_color == WHITE else '黑棋 ●'}"
            status_desc = f"对局进行中（当前回合: {turn_desc}）"

        ascii_board = session.board.render_ascii(character_name=character_name, player_color=session.player_color)

        return (
            f"【五子棋对局快照｜对局ID: {session.game_id}】\n"
            f"- 轮次：第 {turn} 手\n"
            f"- 执子状态：玩家执{player_stone}，{character_name}执{comp_stone}\n"
            f"- 上一步走棋：{last_str}\n"
            f"- 棋盘状态版本：version={session.state_version}（下次落子必须携带此版本号）\n"
            f"- 胜负判定：{status_desc}\n\n"
            f"{ascii_board}"
        )

    @plugin.tool(
        name="analyze_board",
        description="深度评估当前五子棋盘面，输出紧急防守警报（活三、冲四）、进攻做局杀招及一子双用点。",
        risk="low",
    )
    async def analyze_board(game_id: str = "", perspective: str = "companion", ctx: ToolContext = None) -> str:
        session = await current_session(game_id, ctx)
        if session is None:
            return missing_game()
        character_name = session.character_name

        p = str(perspective).strip().lower()
        if p in ("companion", "self"):
            actor_color = session.companion_color
        elif p in ("white",):
            actor_color = WHITE
        elif p in ("black",):
            actor_color = BLACK
        else:
            actor_color = session.player_color

        return GomokuEvaluator.format_analysis_report(
            board=session.board,
            actor_color=actor_color,
            character_name=character_name,
            player_color=session.player_color,
        )

    @plugin.tool(
        name="suggest_moves",
        description="计算当前局势下最佳的候选落子点，并附带稳健、进攻、做局等战术风格解释锦囊。",
        risk="low",
    )
    async def suggest_moves(game_id: str = "", count: int = 3, ctx: ToolContext = None) -> str:
        session = await current_session(game_id, ctx)
        if session is None:
            return missing_game()
        character_name = session.character_name

        return GomokuEvaluator.format_suggestions(
            board=session.board,
            count=max(1, min(count, 5)),
            actor_color=session.active_color,
            character_name=character_name,
        )

    @plugin.tool(
        name="place_piece",
        description="替自己落一颗棋子，棋盘立即更新。x、y 为 0~14 整数；携带当前棋盘的 game_id 和 version。",
        risk="low",
    )
    async def place_piece(x: int, y: int, version: int, game_id: str = "", ctx: ToolContext = None) -> str:
        session = await current_session(game_id, ctx)
        if session is None:
            return missing_game()
        character_name = session.character_name

        # Model moves belong to the companion; player moves come from the board service.
        if session.mode != "duel":
            return Result(is_error=True, status="rejected", reason="advice_only",
                          content="这一局你是参谋，请给建议，由玩家和本地人机落子。")
        color = session.companion_color
        color_name = f"{'黑子' if color == BLACK else '白子'}({character_name})"

        res = await _session_manager.place_piece(
            session=session,
            x=x,
            y=y,
            expected_version=version,
            color=color,
        )

        if not res["ok"]:
            return Result(is_error=True, status="rejected", reason=res["reason"], content=(
                f"【落子拒绝】\n"
                f"- 拒绝原因：{res['reason']}\n"
                f"- 详细说明：{res['message']}\n"
                f"- 当前棋盘最新版本：version={res['version']}"
            ))

        if res["winner"] == session.companion_color:
            stone_name = "黑方" if color == BLACK else "白方"
            status_desc = f"达成五子连珠！{stone_name}({character_name})获胜！"
            change_desc = f"{character_name}{'黑棋' if color == BLACK else '白棋'}绝杀胜出，对局结束。"
        elif res["winner"] == session.player_color:
            p_stone = "黑方" if session.player_color == BLACK else "白方"
            status_desc = f"达成五子连珠！{p_stone}(玩家)获胜！"
            change_desc = f"{p_stone}斩获对局胜利，对局结束。"
        elif res["status"] == "draw":
            status_desc = "棋盘已落满，平局。"
            change_desc = "无空位可下，和棋结束。"
        else:
            status_desc = "尚未分出胜负 (连五达成: 否)"
            opp_next = f"玩家{'黑棋' if session.player_color == BLACK else '白棋'}" if session.active_color == session.player_color else f"{character_name}{'白棋' if color == BLACK else '黑棋'}"
            change_desc = f"棋步已记录，轮次切换，下一回合由 {opp_next} 落子。"

        return (
            f"【落子执行确认】\n"
            f"- 执行状态：成功（OK）\n"
            f"- 动作：{color_name}落于 [{res['notation']}] (坐标 x={x}, y={y})\n"
            f"- 棋盘状态推进：version 更新为 {res['version']}\n"
            f"- 胜负判定：{status_desc}\n"
            f"- 战局变化：{change_desc}"
        )

    return plugin
