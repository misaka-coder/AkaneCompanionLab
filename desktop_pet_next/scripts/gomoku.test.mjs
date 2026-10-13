import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BOARD_SIZE,
  BLACK,
  WHITE,
  EMPTY,
  coordToNotation,
  checkWin,
  computeBestLocalMove,
  placePiece,
  undoPiece,
  resetState,
  createGameState
} from "../src/gomoku-engine.js";

test("Gomoku coordinates are rendered as board notation", () => {
  assert.equal(coordToNotation(7, 7), "H8");
  assert.equal(coordToNotation(0, 0), "A1");
  assert.equal(coordToNotation(14, 14), "O15");
});

test("Gomoku 4-direction win detection", () => {
  const makeGrid = () => Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(EMPTY));

  // 1. 水平连五
  const g1 = makeGrid();
  for (let i = 0; i < 5; i++) g1[7][3 + i] = BLACK;
  assert.equal(checkWin(g1, 7, 7, BLACK), true);
  assert.equal(checkWin(g1, 7, 7, WHITE), false);

  // 2. 垂直连五
  const g2 = makeGrid();
  for (let i = 0; i < 5; i++) g2[3 + i][7] = WHITE;
  assert.equal(checkWin(g2, 7, 7, WHITE), true);

  // 3. 主对角线 (↘) 连五
  const g3 = makeGrid();
  for (let i = 0; i < 5; i++) g3[2 + i][2 + i] = BLACK;
  assert.equal(checkWin(g3, 4, 4, BLACK), true);

  // 4. 副对角线 (↗) 连五
  const g4 = makeGrid();
  for (let i = 0; i < 5; i++) g4[6 - i][6 + i] = WHITE;
  assert.equal(checkWin(g4, 8, 4, WHITE), true);
});

test("Local AI blocks opponent lethal win and seizes victory", () => {
  const makeGrid = () => Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(EMPTY));

  // 场景 1: 黑棋有 4 子连线 (7, 4), (7, 5), (7, 6), (7, 7) -> 白棋必须封堵 (7, 3) 或 (7, 8)
  const g1 = makeGrid();
  g1[4][7] = BLACK;
  g1[5][7] = BLACK;
  g1[6][7] = BLACK;
  g1[7][7] = BLACK;
  const move1 = computeBestLocalMove(g1, WHITE);
  assert.ok(
    (move1.x === 7 && move1.y === 3) || (move1.x === 7 && move1.y === 8),
    `AI should block opponent rush four, but got (${move1.x}, ${move1.y})`
  );

  // 场景 2: 白棋自身有 4 子 (4, 7), (5, 7), (6, 7), (7, 7) -> 白棋应直接绝杀连五
  const g2 = makeGrid();
  g2[7][4] = WHITE;
  g2[7][5] = WHITE;
  g2[7][6] = WHITE;
  g2[7][7] = WHITE;
  const move2 = computeBestLocalMove(g2, WHITE);
  assert.ok(
    (move2.x === 3 && move2.y === 7) || (move2.x === 8 && move2.y === 7),
    `AI should complete five-in-a-row to win, but got (${move2.x}, ${move2.y})`
  );
});

test("Game actions: placePiece, turn alternation, and undo", () => {
  const state = createGameState();
  assert.equal(state.version, 1);
  assert.equal(state.activeColor, BLACK);

  // 玩家落子 H8 (7, 7)
  const p1 = placePiece(state, 7, 7, BLACK);
  assert.equal(p1, true);
  assert.equal(state.moves.length, 1);
  assert.equal(state.activeColor, WHITE);
  assert.equal(state.version, 2);

  // 尝试在已占格子下子 -> 失败
  const pFail = placePiece(state, 7, 7, WHITE);
  assert.equal(pFail, false);

  // 伴侣落子 H9 (7, 8)
  const p2 = placePiece(state, 7, 8, WHITE);
  assert.equal(p2, true);
  assert.equal(state.moves.length, 2);
  assert.equal(state.activeColor, BLACK);

  // 悔棋 2 步
  const u = undoPiece(state, 2);
  assert.equal(u, true);
  assert.equal(state.moves.length, 0);
  assert.equal(state.activeColor, BLACK);
  assert.equal(state.grid[7][7], EMPTY);

  // 重置
  const beforeReset = state.version;
  resetState(state);
  assert.equal(state.version, beforeReset + 1);
  assert.equal(state.status, "in_progress");
});

test("players cannot double move and resets invalidate pending moves", () => {
  const state = createGameState();
  assert.equal(placePiece(state, 0.5, 1, BLACK), false);
  assert.equal(placePiece(state, 0, 0, WHITE), false);
  assert.equal(placePiece(state, 0, 0, BLACK), true);
  assert.equal(placePiece(state, 1, 0, BLACK), false);
  const pendingVersion = state.version;
  resetState(state);
  assert.ok(state.version > pendingVersion);
  assert.equal(state.activeColor, BLACK);
  assert.equal(placePiece(state, 1, 0, WHITE), false);
});

test("resetState supports switching playerColor to white", () => {
  const state = createGameState();
  assert.equal(state.playerColor, BLACK);
  resetState(state, "duel", WHITE);
  assert.equal(state.playerColor, WHITE);
  assert.equal(state.activeColor, BLACK);
  assert.equal(state.mode, "duel");
});
