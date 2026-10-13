import assert from "node:assert/strict";
import { test } from "node:test";
import { createGomokuController } from "../src/pet/gomoku.js";
import { createRuntimeScope } from "../src/pet/scope.js";
import { createGameState, placePiece, resetState, undoPiece, BLACK, WHITE } from "../src/gomoku-engine.js";

function harness(send) {
  const state = { instanceId: "local", backendUrl: "http://localhost:12001", boundBotId: "bot",
    profileUserId: "master", sessionId: "desktop-random", characterPackId: "cecilia" };
  const scope = createRuntimeScope(state);
  let handler;
  const emitted = [], boards = new Map(), messages = [];
  const chat = { sending: false, async sendMessage(message, options) {
    messages.push({ message, options });
    chat.sending = true;
    try { return await send({ boards, message }); } finally { chat.sending = false; }
  } };
  const controller = createGomokuController({ state, scope, chat,
    persistence: { getProfileUserId: () => state.profileUserId }, lifecycle: { track() {} },
    backend: { buildBackendEndpointUrl: (_name, path, params) => "http://localhost:12001" + path + "?" + new URLSearchParams(params) },
    platform: { isTauriRuntime: true, listen: async (_event, fn) => { handler = fn; return () => {}; },
      emitTo: async (_window, _event, payload) => emitted.push(structuredClone(payload)),
      invoke: async (_command, { request }) => {
        const args = JSON.parse(request.body);
        if (args.action === "open") {
          const board = createGameState();
          Object.assign(board, { gameId: args.game_id, characterName: args.character_name, boardText: "A1 ● · ·\nB1 · · ·" });
          if (args.player_color) board.playerColor = args.player_color;
          boards.set(args.game_id, board);
        }
        const board = boards.get(args.game_id);
        if (!board) return { ok: false, body: JSON.stringify({ ok: false, reason: "game_not_found", content: {} }) };
        let ok = true;
        if (args.action === "play") {
          ok = args.version === board.version && placePiece(board, args.x, args.y, board.playerColor ?? BLACK);
        } else if (args.action === "computer_move") {
          const compColor = (board.playerColor ?? BLACK) === BLACK ? WHITE : BLACK;
          ok = args.version === board.version && placePiece(board, args.x, args.y, compColor);
        } else if (args.action === "reset") {
          resetState(board, args.mode, args.player_color);
        } else if (args.action === "undo") {
          const steps = board.activeColor !== (board.playerColor ?? BLACK) ? 1 : 2;
          ok = undoPiece(board, steps);
        }
        return { ok, body: JSON.stringify({ ok, content: { state: structuredClone(board), message: ok ? "" : "stale move" } }) };
      },
    },
  });
  return { state, boards, emitted, messages, chat, async start() { await controller.registerBridge(); },
    async request(action, args = {}) { await handler({ payload: { requestId: String(Math.random()), action,
      gameId: emitted.at(-1)?.state?.gameId, version: emitted.at(-1)?.state?.version, mode: "duel", ...args } }); },
  };
}

const tick = () => new Promise(resolve => setTimeout(resolve, 20));

test("a player move wakes normal chat and displays the actual tool move", async () => {
  const h = harness(async ({ boards }) => {
    const board = [...boards.values()][0];
    placePiece(board, 8, 14, WHITE);
    return true;
  });
  await h.start(); await h.request("connect"); await h.request("play", { x: 7, y: 14 }); await tick();
  assert.equal(h.messages.length, 1);
  assert.match(h.messages[0].message, /H15/);
  assert.match(h.messages[0].message, /version=2/);
  assert.deepEqual(h.messages[0].options, { requireNewTurn: true, includeAttachments: false });
  assert.equal(h.emitted.at(-1).state.grid[14][8], WHITE);
  assert.equal(h.emitted.at(-1).thinking, false);
});

test("a text coordinate does not substitute for a real move; retry is possible", async () => {
  const h = harness(async () => true);
  await h.start(); await h.request("connect"); await h.request("play", { x: 0, y: 0 }); await tick();
  assert.equal(h.emitted.at(-1).state.moves.length, 1);
  assert.match(h.emitted.at(-1).error, /尚未落白子/);
  await h.request("retry"); await tick();
  assert.equal(h.messages.length, 2);
});

test("reset during model work cannot be overwritten by a stale move", async () => {
  let finish;
  const h = harness(async ({ boards }) => {
    const board = [...boards.values()][0], version = board.version;
    await new Promise(resolve => { finish = resolve; });
    if (board.version === version) placePiece(board, 8, 8, WHITE);
    return true;
  });
  await h.start(); await h.request("connect"); await h.request("play", { x: 7, y: 7 }); await tick();
  await h.request("play", { x: 6, y: 6 });
  assert.equal(h.messages.length, 1);
  await h.request("reset");
  assert.equal(h.emitted.at(-1).state.moves.length, 0);
  finish(); await tick();
  assert.equal(h.emitted.at(-1).state.moves.length, 0);
  assert.equal(h.emitted.at(-1).thinking, false);
});

test("switching character ignores the old model completion", async () => {
  let finish;
  const h = harness(async () => { await new Promise(resolve => { finish = resolve; }); return true; });
  await h.start(); await h.request("connect"); const oldId = h.emitted.at(-1).state.gameId;
  await h.request("play", { x: 7, y: 7 }); await tick();
  h.state.characterPackId = "reimu";
  await h.request("connect"); const newId = h.emitted.at(-1).state.gameId;
  assert.notEqual(newId, oldId);
  finish(); await tick();
  assert.equal(h.emitted.at(-1).state.gameId, newId);
  assert.equal(h.emitted.at(-1).state.moves.length, 0);
});

test("assist uses one shared board and advice includes the real corner move", async () => {
  const h = harness(async () => true);
  await h.start(); await h.request("connect"); await h.request("reset", { mode: "assist" });
  await h.request("play", { x: 0, y: 0 });
  assert.equal(h.emitted.at(-1).state.moves.length, 2);
  assert.equal(h.messages.length, 0);
  await h.request("advice"); await tick();
  assert.equal(h.messages.length, 1);
  assert.match(h.messages[0].message, /不替任何一方落子/);
  assert.match(h.messages[0].message, /A1/);
});

test("reconnecting after backend restart replaces a lost board with a usable new game", async () => {
  const h = harness(async () => true);
  await h.start(); await h.request("connect");
  const oldId = h.emitted.at(-1).state.gameId;
  h.boards.clear();
  await h.request("connect");
  assert.notEqual(h.emitted.at(-1).state.gameId, oldId);
  assert.equal(h.emitted.at(-1).state.moves.length, 0);
  assert.match(h.emitted.at(-1).error, /新开一局/);
  await h.request("play", { x: 7, y: 7 }); await tick();
  assert.equal(h.emitted.at(-1).state.moves.length, 1);
  assert.equal(h.messages.length, 1);
});

test("reset with playerColor white prompts companion to make the first black move", async () => {
  const h = harness(async ({ boards }) => {
    const board = [...boards.values()][0];
    placePiece(board, 7, 7, BLACK);
    return true;
  });
  await h.start(); await h.request("connect");
  await h.request("reset", { playerColor: WHITE }); await tick();
  assert.equal(h.messages.length, 1);
  assert.match(h.messages[0].message, /我执白，你执黑/);
  assert.match(h.messages[0].message, /落一颗黑子/);
  assert.equal(h.emitted.at(-1).state.grid[7][7], BLACK);
  assert.equal(h.emitted.at(-1).state.activeColor, WHITE);
  assert.equal(h.emitted.at(-1).thinking, false);
});
