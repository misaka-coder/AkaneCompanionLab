"""Ultra-fast (< 3ms) heuristic evaluator for Gomoku tactical analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .board import BOARD_SIZE, EMPTY, BLACK, WHITE, DIRECTIONS, Board, coord_to_notation

SCORE_FIVE = 100000
SCORE_OPEN_FOUR = 10000
SCORE_RUSH_FOUR = 1200
SCORE_OPEN_THREE = 900
SCORE_BLOCKED_THREE = 150
SCORE_OPEN_TWO = 60
SCORE_BLOCKED_TWO = 10


@dataclass
class PointEvaluation:
    x: int
    y: int
    notation: str
    attack_score: int
    defense_score: int
    total_score: int
    attack_patterns: list[str] = field(default_factory=list)
    defense_patterns: list[str] = field(default_factory=list)


@dataclass
class Threat:
    type_name: str
    description: str
    danger_point: str
    recommended_defense: str
    urgency: str  # "lethal", "high", "medium"


@dataclass
class Opportunity:
    type_name: str
    description: str
    recommended_point: str
    potential: str  # "decisive", "winning", "initiative"


@dataclass
class DualPurposePoint:
    notation: str
    description: str
    total_score: int


def _get_candidate_points(board: Board) -> list[tuple[int, int]]:
    """Prune search space to empty cells within Chebyshev distance 2 of existing stones."""
    if not board.moves:
        return [(7, 7)]  # 盘面为空，天元落子

    candidates = set()
    for move in board.moves:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                nx, ny = move.x + dx, move.y + dy
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE:
                    if board.grid[ny][nx] == EMPTY:
                        candidates.add((nx, ny))

    return list(candidates) or [(7, 7)]


def _eval_line_patterns(line: list[int], color: int) -> tuple[int, list[str]]:
    """Evaluate consecutive patterns for a 9-cell window with center at index 4."""
    opp = BLACK if color == WHITE else WHITE
    # 将网格转换为符号串: '1' 为当前棋子, '0' 为空位, '2' 为敌方/边界
    chars = []
    for val in line:
        if val == color:
            chars.append("1")
        elif val == EMPTY:
            chars.append("0")
        else:
            chars.append("2")
    s = "".join(chars)

    score = 0
    patterns = []

    if "11111" in s:
        score += SCORE_FIVE
        patterns.append("连五")
        return score, patterns

    if "011110" in s:
        score += SCORE_OPEN_FOUR
        patterns.append("活四")
    elif any(p in s for p in ("211110", "011112", "11011", "10111", "11101")):
        score += SCORE_RUSH_FOUR
        patterns.append("冲四")

    if any(p in s for p in ("01110", "010110", "011010")):
        score += SCORE_OPEN_THREE
        patterns.append("活三")
    elif any(p in s for p in ("211100", "001112", "210110", "011012")):
        score += SCORE_BLOCKED_THREE
        patterns.append("眠三")

    if any(p in s for p in ("001100", "01010", "010010")):
        score += SCORE_OPEN_TWO
        patterns.append("活二")
    elif any(p in s for p in ("211000", "000112", "210100")):
        score += SCORE_BLOCKED_TWO
        patterns.append("眠二")

    return score, patterns


def _evaluate_point(board: Board, x: int, y: int, actor_color: int) -> PointEvaluation:
    """Evaluate both attack and defense value of placing a stone at (x, y)."""
    opp_color = BLACK if actor_color == WHITE else WHITE
    notation = coord_to_notation(x, y)

    # 1. 计算进攻分 (actor_color)
    attack_score = 0
    attack_patterns = []
    board.grid[y][x] = actor_color
    for dx, dy in DIRECTIONS:
        line = []
        for step in range(-4, 5):
            nx, ny = x + dx * step, y + dy * step
            if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE:
                line.append(board.grid[ny][nx])
            else:
                line.append(-1)  # 边界
        s, p = _eval_line_patterns(line, actor_color)
        attack_score += s
        attack_patterns.extend(p)

    # 2. 计算防守分 (opp_color)
    defense_score = 0
    defense_patterns = []
    board.grid[y][x] = opp_color
    for dx, dy in DIRECTIONS:
        line = []
        for step in range(-4, 5):
            nx, ny = x + dx * step, y + dy * step
            if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE:
                line.append(board.grid[ny][nx])
            else:
                line.append(-1)
        s, p = _eval_line_patterns(line, opp_color)
        defense_score += s
        defense_patterns.extend(p)

    board.grid[y][x] = EMPTY

    # 天元/中心轻微位置加权 (距离中心越近，初始掌控力越强)
    dist_to_center = abs(x - 7) + abs(y - 7)
    center_bonus = max(0, 14 - dist_to_center)

    # 防守权重设为 1.15，保证面临绝杀或活四时优先救亡
    total_score = int(attack_score * 1.0 + defense_score * 1.15 + center_bonus)

    return PointEvaluation(
        x=x,
        y=y,
        notation=notation,
        attack_score=attack_score,
        defense_score=defense_score,
        total_score=total_score,
        attack_patterns=attack_patterns,
        defense_patterns=defense_patterns,
    )


class GomokuEvaluator:
    @staticmethod
    def evaluate_board(board: Board, actor_color: int = WHITE, character_name: str = "同伴") -> dict[str, Any]:
        """Perform full tactical assessment for current board."""
        candidates = _get_candidate_points(board)
        evaluations: list[PointEvaluation] = []
        for cx, cy in candidates:
            evaluations.append(_evaluate_point(board, cx, cy, actor_color))

        evaluations.sort(key=lambda item: item.total_score, reverse=True)

        threats: list[Threat] = []
        opportunities: list[Opportunity] = []
        dual_purposes: list[DualPurposePoint] = []

        opp_name = "玩家" if actor_color == WHITE else character_name

        # 扫描威胁与良机
        for ev in evaluations:
            # 防守威胁判定
            if ev.defense_score >= SCORE_FIVE:
                threats.append(Threat(
                    type_name="five_kill",
                    description=f"{opp_name}在 [{ev.notation}] 具备连五绝杀杀招！",
                    danger_point=ev.notation,
                    recommended_defense=ev.notation,
                    urgency="lethal",
                ))
            elif ev.defense_score >= SCORE_OPEN_FOUR:
                threats.append(Threat(
                    type_name="open_three",
                    description=f"{opp_name}已连成活三，若在 [{ev.notation}] 落子将形成无解活四！",
                    danger_point=ev.notation,
                    recommended_defense=ev.notation,
                    urgency="high",
                ))
            elif ev.defense_score >= SCORE_RUSH_FOUR and len(threats) < 2:
                threats.append(Threat(
                    type_name="rush_four",
                    description=f"{opp_name}在 [{ev.notation}] 具备冲四逼着能力。",
                    danger_point=ev.notation,
                    recommended_defense=ev.notation,
                    urgency="medium",
                ))

            # 进攻良机判定
            if ev.attack_score >= SCORE_FIVE:
                opportunities.append(Opportunity(
                    type_name="instant_win",
                    description=f"我方落子于 [{ev.notation}] 可直接连成五子获得胜利！",
                    recommended_point=ev.notation,
                    potential="decisive",
                ))
            elif ev.attack_score >= SCORE_OPEN_FOUR:
                opportunities.append(Opportunity(
                    type_name="open_four_setup",
                    description=f"我方落子于 [{ev.notation}] 将连成活四必胜杀招！",
                    recommended_point=ev.notation,
                    potential="winning",
                ))
            elif ev.attack_score >= SCORE_RUSH_FOUR and len(opportunities) < 2:
                opportunities.append(Opportunity(
                    type_name="initiative_attack",
                    description=f"我方落子于 [{ev.notation}] 可连成冲四/强活三，夺取对局先手！",
                    recommended_point=ev.notation,
                    potential="initiative",
                ))

            # 一子双用 (Dual Purpose)
            if ev.attack_score >= SCORE_RUSH_FOUR and ev.defense_score >= SCORE_RUSH_FOUR:
                dual_purposes.append(DualPurposePoint(
                    notation=ev.notation,
                    description=f"坐标 [{ev.notation}]：既阻断了{opp_name}的重要进攻线，又激活了自身进攻杀路！",
                    total_score=ev.total_score,
                ))

        # 局势体感评估
        best_ev = evaluations[0] if evaluations else None
        overall_score = 0
        if best_ev:
            overall_score = best_ev.attack_score - best_ev.defense_score

        if overall_score > 5000:
            sentiment = "【大优绝杀局】（胜券在握）"
        elif overall_score > 1000:
            sentiment = "【优势主导】（掌控进攻主动权）"
        elif overall_score < -5000:
            sentiment = "【濒临绝境】（面临致命杀法，必须全力防守）"
        elif overall_score < -1000:
            sentiment = "【被动吃紧】（黑方攻势凶猛，需稳妥化解）"
        else:
            sentiment = "【均势微优】（双方在交战区紧密胶着）"

        return {
            "evaluations": evaluations,
            "threats": threats,
            "opportunities": opportunities,
            "dual_purposes": dual_purposes,
            "overall_score": overall_score,
            "sentiment": sentiment,
            "best_move": best_ev.notation if best_ev else "H8",
        }

    @classmethod
    def format_analysis_report(cls, board: Board, actor_color: int = WHITE, character_name: str = "同伴", player_color: int = BLACK) -> str:
        """Format full markdown analysis report for model prompt."""
        result = cls.evaluate_board(board, actor_color=actor_color, character_name=character_name)
        role_label = character_name if actor_color != player_color else "玩家"
        stone_symbol = "○" if actor_color == WHITE else "●"

        lines = [
            "【棋局深度战术评估报告】",
            f"当前持方：{role_label} ({'白棋 ○' if actor_color == WHITE else '黑棋 ●'})",
            f"局势体感评估：{result['sentiment']}",
            "",
        ]

        # 威胁段落
        threats = result["threats"]
        if threats:
            lines.append("🚨【防守警报 (Threats)】")
            for t in threats[:3]:
                lines.append(f"- [{t.urgency.upper()}] {t.description}")
                lines.append(f"  建议阻截点：[{t.recommended_defense}]")
        else:
            lines.append("🚨【防守警报 (Threats)】\n- 暂无致命防守警报，后方防线稳固。")

        lines.append("")

        # 进攻段落
        opps = result["opportunities"]
        if opps:
            lines.append("⚔️【进攻与做局机会 (Opportunities)】")
            for o in opps[:3]:
                lines.append(f"- [{o.potential.upper()}] {o.description}")
                lines.append(f"  进攻建议点：[{o.recommended_point}]")
        else:
            lines.append("⚔️【进攻与做局机会 (Opportunities)】\n- 暂无一击必杀机会，宜借势布子或耐心缠斗。")

        lines.append("")

        # 一子双用
        duals = result["dual_purposes"]
        if duals:
            lines.append("⚖️【神之一手：一子双用点 (Dual-Purpose)】")
            for d in duals[:2]:
                lines.append(f"- {d.description}")
            lines.append("")

        return "\n".join(lines).strip()

    @classmethod
    def format_suggestions(cls, board: Board, count: int = 3, actor_color: int = WHITE, character_name: str = "同伴") -> str:
        """Format Top-N candidate move suggestions with tactical style labels."""
        result = cls.evaluate_board(board, actor_color=actor_color, character_name=character_name)
        evals = result["evaluations"][:count]

        lines = ["【候选落子与走法锦囊（按战术收益排序）】", ""]

        for idx, ev in enumerate(evals, 1):
            if ev.attack_score >= SCORE_FIVE:
                style = "【绝杀取胜】"
                exp = f"直接连成五子绝杀，锁定胜局！"
            elif ev.defense_score >= SCORE_FIVE or ev.defense_score >= SCORE_OPEN_FOUR:
                style = "【生死必堵】"
                exp = f"掐灭对方冲四/活三的绝杀火种，不可退让的生死防线。"
            elif ev.attack_score >= SCORE_OPEN_FOUR:
                style = "【必胜做杀】"
                exp = f"落子成活四，为下回合绝杀奠定不可逆优势。"
            elif ev.attack_score >= SCORE_RUSH_FOUR and ev.defense_score >= SCORE_RUSH_FOUR:
                style = "【攻守兼备】"
                exp = f"以攻代守，既打断对方节奏，又扩张自身攻势。"
            elif ev.attack_score >= SCORE_OPEN_THREE:
                style = "【抢攻做局】"
                exp = f"展开活三攻势，将主动权掌控在己方手中。"
            else:
                style = "【长远布子】"
                exp = f"稳扎稳打向外围拓展阵地，积蓄中盘争夺根据地。"

            lines.append(f"{idx}. [{ev.notation}] ｜ 风格：{style}")
            lines.append(f"   - 战术解析：{exp}")
            lines.append("")

        return "\n".join(lines).strip()
