/**
 * Pure Gomoku Game Engine & Tactical Heuristic AI
 * Zero external dependencies, pure isomorphic JS (runs in Node & Browser).
 */

export const BOARD_SIZE = 15;
export const EMPTY = 0;
export const BLACK = 1; // 玩家执黑
export const WHITE = 2; // 伴侣/AI执白

export const DIRS = [
  [1, 0], [0, 1], [1, 1], [1, -1]
];

export function createGameState() {
  return {
    grid: Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(EMPTY)),
    moves: [], // { x, y, color, notation, turn }
    activeColor: BLACK,
    playerColor: BLACK,
    version: 1,
    status: "in_progress", // "in_progress", "black_won", "white_won", "draw"
    winner: null,
    mode: "duel", // "duel", "assist", "coop"
    characterName: "伴侣",
    companionThinking: false,
  };
}

export function coordToNotation(x, y) {
  return `${String.fromCharCode(65 + x)}${y + 1}`;
}

export function checkWin(grid, x, y, color) {
  for (const [dx, dy] of DIRS) {
    let count = 1;
    // 正向延伸
    for (let step = 1; step < 5; step++) {
      const nx = x + dx * step;
      const ny = y + dy * step;
      if (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE && grid[ny][nx] === color) {
        count++;
      } else break;
    }
    // 反向延伸
    for (let step = 1; step < 5; step++) {
      const nx = x - dx * step;
      const ny = y - dy * step;
      if (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE && grid[ny][nx] === color) {
        count++;
      } else break;
    }
    if (count >= 5) return true;
  }
  return false;
}

export function computeBestLocalMove(grid, actorColor) {
  const oppColor = actorColor === WHITE ? BLACK : WHITE;
  let bestScore = -Infinity;
  let bestPoint = { x: 7, y: 7 };

  const candidates = new Set();
  let hasStones = false;
  for (let y = 0; y < BOARD_SIZE; y++) {
    for (let x = 0; x < BOARD_SIZE; x++) {
      if (grid[y][x] !== EMPTY) {
        hasStones = true;
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) {
            const nx = x + dx;
            const ny = y + dy;
            if (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE && grid[ny][nx] === EMPTY) {
              candidates.add(`${nx},${ny}`);
            }
          }
        }
      }
    }
  }

  if (!hasStones) return { x: 7, y: 7 };

  for (const key of candidates) {
    const [x, y] = key.split(",").map(Number);
    let atkScore = 0;
    let defScore = 0;

    grid[y][x] = actorColor;
    for (const [dx, dy] of DIRS) {
      atkScore += scoreLine(grid, x, y, dx, dy, actorColor);
    }
    grid[y][x] = oppColor;
    for (const [dx, dy] of DIRS) {
      defScore += scoreLine(grid, x, y, dx, dy, oppColor);
    }
    grid[y][x] = EMPTY;

    const centerDist = Math.abs(x - 7) + Math.abs(y - 7);
    const score = atkScore * 1.0 + defScore * 1.15 + (14 - centerDist);

    if (score > bestScore) {
      bestScore = score;
      bestPoint = { x, y };
    }
  }

  return bestPoint;
}

function scoreLine(grid, x, y, dx, dy, color) {
  let count = 1;
  let openEnds = 0;

  let step = 1;
  while (step < 5) {
    const nx = x + dx * step;
    const ny = y + dy * step;
    if (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE) {
      if (grid[ny][nx] === color) { count++; step++; }
      else if (grid[ny][nx] === EMPTY) { openEnds++; break; }
      else break;
    } else break;
  }

  step = 1;
  while (step < 5) {
    const nx = x - dx * step;
    const ny = y - dy * step;
    if (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE) {
      if (grid[ny][nx] === color) { count++; step++; }
      else if (grid[ny][nx] === EMPTY) { openEnds++; break; }
      else break;
    } else break;
  }

  if (count >= 5) return 100000;
  if (count === 4 && openEnds === 2) return 10000;
  if (count === 4 && openEnds === 1) return 1200;
  if (count === 3 && openEnds === 2) return 900;
  if (count === 3 && openEnds === 1) return 150;
  if (count === 2 && openEnds === 2) return 60;
  return 10;
}

export function placePiece(state, x, y, color) {
  if (state.status !== "in_progress") return false;
  if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || x >= BOARD_SIZE || y < 0 || y >= BOARD_SIZE) return false;
  if (![BLACK, WHITE].includes(color) || color !== state.activeColor) return false;
  if (state.grid[y][x] !== EMPTY) return false;

  state.grid[y][x] = color;
  const notation = coordToNotation(x, y);
  state.moves.push({ x, y, color, notation, turn: state.moves.length + 1 });
  state.version++;

  if (checkWin(state.grid, x, y, color)) {
    state.status = color === BLACK ? "black_won" : "white_won";
    state.winner = color;
  } else if (state.moves.length >= BOARD_SIZE * BOARD_SIZE) {
    state.status = "draw";
    state.winner = null;
  } else {
    state.activeColor = color === BLACK ? WHITE : BLACK;
  }
  return true;
}

export function undoPiece(state, steps = 2) {
  if (!state.moves.length) return false;
  for (let i = 0; i < steps; i++) {
    const m = state.moves.pop();
    if (m) state.grid[m.y][m.x] = EMPTY;
  }
  state.status = "in_progress";
  state.winner = null;
  state.version++;
  if (state.moves.length) {
    const last = state.moves[state.moves.length - 1];
    state.activeColor = last.color === BLACK ? WHITE : BLACK;
  } else {
    state.activeColor = BLACK;
  }
  return true;
}

export function resetState(state, mode, playerColor) {
  state.grid = Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(EMPTY));
  state.moves = [];
  state.activeColor = BLACK;
  state.version++;
  state.status = "in_progress";
  state.winner = null;
  state.companionThinking = false;
  if (mode !== undefined) state.mode = mode;
  if (playerColor !== undefined && [BLACK, WHITE].includes(playerColor)) {
    state.playerColor = playerColor;
  }
}
