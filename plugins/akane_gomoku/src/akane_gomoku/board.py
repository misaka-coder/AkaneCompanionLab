"""Gomoku board state, dynamic active bounding box ASCII, and move rules."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

BOARD_SIZE = 15
EMPTY = 0
BLACK = 1  # 玩家执黑
WHITE = 2  # 伴侣执白

COLOR_SYMBOLS = {
    EMPTY: "·",
    BLACK: "●",
    WHITE: "○",
}

DIRECTIONS = (
    (1, 0),   # 水平横向 ─
    (0, 1),   # 垂直纵向 │
    (1, 1),   # 主对角线 ↘
    (1, -1),  # 副对角线 ↗
)

_NOTATION_PATTERN = re.compile(r"^\s*\[?\s*([A-Oa-o])\s*([0-9]{1,2})\s*\]?\s*$")


def coord_to_notation(x: int, y: int) -> str:
    """Convert (x, y) 0-indexed coordinates to board notation (e.g. 7, 7 -> H8)."""
    if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
        raise ValueError(f"coordinate out of bounds: ({x}, {y})")
    col_letter = chr(ord("A") + x)
    row_number = y + 1
    return f"{col_letter}{row_number}"


def notation_to_coord(notation: str) -> tuple[int, int]:
    """Convert notation string (e.g. 'H8', '[H8]', 'h8') to 0-indexed (x, y)."""
    match = _NOTATION_PATTERN.match(str(notation or "").strip())
    if not match:
        raise ValueError(f"invalid notation: {notation!r}")
    col_char, row_str = match.groups()
    x = ord(col_char.upper()) - ord("A")
    y = int(row_str) - 1
    if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
        raise ValueError(f"notation coordinates out of bounds: {notation!r} -> ({x}, {y})")
    return x, y


@dataclass(frozen=True)
class Move:
    x: int
    y: int
    color: int
    notation: str
    turn: int


@dataclass
class Board:
    grid: list[list[int]] = field(default_factory=lambda: [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)])
    moves: list[Move] = field(default_factory=list)

    @classmethod
    def create(cls) -> "Board":
        return cls()

    def get_piece(self, x: int, y: int) -> int:
        if 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE:
            return self.grid[y][x]
        return -1

    def is_empty(self, x: int, y: int) -> bool:
        return self.get_piece(x, y) == EMPTY

    def place_piece(self, x: int, y: int, color: int) -> tuple[bool, str]:
        """Attempt to place a piece on the board."""
        if type(x) is not int or type(y) is not int or color not in (BLACK, WHITE):
            return False, "坐标必须是 0~14 的整数，棋子必须为黑或白。"
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return False, f"坐标 ({x}, {y}) 超出棋盘边界 (0~14)"
        if self.grid[y][x] != EMPTY:
            existing = "黑子" if self.grid[y][x] == BLACK else "白子"
            return False, f"位置 [{coord_to_notation(x, y)}] 已有{existing}，不可重复落子"
        if color not in (BLACK, WHITE):
            return False, f"非法棋子颜色: {color}"

        notation = coord_to_notation(x, y)
        self.grid[y][x] = color
        move = Move(x=x, y=y, color=color, notation=notation, turn=len(self.moves) + 1)
        self.moves.append(move)
        return True, ""

    def undo_last_move(self) -> Move | None:
        """Undo the most recent move and restore empty cell."""
        if not self.moves:
            return None
        last = self.moves.pop()
        self.grid[last.y][last.x] = EMPTY
        return last

    def check_win(self, x: int, y: int, color: int) -> bool:
        """Check if placing piece at (x, y) creates 5-in-a-row for color."""
        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
            return False
        for dx, dy in DIRECTIONS:
            count = 1
            # 正向延伸
            step = 1
            while step < 5:
                nx, ny = x + dx * step, y + dy * step
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE and self.grid[ny][nx] == color:
                    count += 1
                    step += 1
                else:
                    break
            # 反向延伸
            step = 1
            while step < 5:
                nx, ny = x - dx * step, y - dy * step
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE and self.grid[ny][nx] == color:
                    count += 1
                    step += 1
                else:
                    break
            if count >= 5:
                return True
        return False

    def is_full(self) -> bool:
        return len(self.moves) >= BOARD_SIZE * BOARD_SIZE

    @property
    def last_move(self) -> Move | None:
        return self.moves[-1] if self.moves else None

    def list_stones(self) -> tuple[list[Move], list[Move]]:
        black_stones = [m for m in self.moves if m.color == BLACK]
        white_stones = [m for m in self.moves if m.color == WHITE]
        return black_stones, white_stones

    def render_ascii(self, padding: int = 1, character_name: str = "同伴", player_color: int = BLACK) -> str:
        """Render a compact Active Bounding Box ASCII view for LLM comprehension."""
        if not self.moves:
            # 空棋盘：默认聚焦在天元 H8 (7, 7) 附近的 5x5 核心区
            min_c, max_c = 5, 9
            min_r, max_r = 5, 9
        else:
            xs = [m.x for m in self.moves]
            ys = [m.y for m in self.moves]
            min_c = max(0, min(xs) - padding)
            max_c = min(BOARD_SIZE - 1, max(xs) + padding)
            min_r = max(0, min(ys) - padding)
            max_r = min(BOARD_SIZE - 1, max(ys) + padding)

        col_letters = [chr(ord("A") + c) for c in range(min_c, max_c + 1)]
        header_cols = "   " + "".join(f"{c:>4}" for c in col_letters)
        border = "   +" + "-" * (len(col_letters) * 4 - 1) + "+"

        lines = [
            f"【当前交战区局势（行 {min_r + 1}~{max_r + 1}，列 {col_letters[0]}~{col_letters[-1]}）】",
            header_cols,
            border,
        ]

        last = self.last_move
        for r in range(min_r, max_r + 1):
            row_num = r + 1
            cells = []
            for c in range(min_c, max_c + 1):
                sym = COLOR_SYMBOLS[self.grid[r][c]]
                cells.append(f"{sym:>2}  ")
            row_content = "".join(cells).rstrip()
            line = f"{row_num:>2} | {row_content:<{len(col_letters) * 4 - 3}}|"
            if last and last.y == r:
                mover = "玩家" if last.color == player_color else character_name
                line += f"  <- {mover}刚落在 [{last.notation}]"
            lines.append(line)

        lines.append(border)

        # 统计活跃点
        blacks, whites = self.list_stones()
        b_notations = ", ".join(m.notation for m in blacks[-6:]) or "无"
        w_notations = ", ".join(m.notation for m in whites[-6:]) or "无"
        lines.append(f"（黑子 ●: {b_notations} ｜ 白子 ○: {w_notations} ｜ 中心天元: H8）")

        return "\n".join(lines)
