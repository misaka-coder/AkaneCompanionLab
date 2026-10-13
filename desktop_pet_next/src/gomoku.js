import { emitTo, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  BOARD_SIZE, BLACK, WHITE, EMPTY, createGameState, computeBestLocalMove,
  placePiece, undoPiece, resetState,
} from "./gomoku-engine.js";

const native = Boolean(window.__TAURI_INTERNALS__);
const appWindow = native ? getCurrentWindow() : null;
const state = createGameState();
let ready = !native;
let busy = false;
let error = "";
let previewTimer = null;
let audioCtx = null;
const requests = new Map();
const els = Object.fromEntries([
  "col-labels", "row-labels", "board-grid", "pieces-layer", "hover-reticle",
  "status-stone", "status-text", "mode-select", "color-select", "btn-undo", "btn-reset",
  "btn-close", "btn-ask-help", "btn-retry",
].map(id => [id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), document.getElementById(id)]));

function playStoneSound() {
  try {
    audioCtx ||= new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") void audioCtx.resume();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    const now = audioCtx.currentTime;
    osc.frequency.setValueAtTime(320, now);
    osc.frequency.exponentialRampToValueAtTime(140, now + 0.04);
    gain.gain.setValueAtTime(0.4, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.05);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + 0.06);
  } catch { /* Sound is optional; board actions are not. */ }
}

function renderBoard() {
  els.piecesLayer.replaceChildren();
  const last = state.moves.at(-1);
  for (const move of state.moves) {
    const stone = document.createElement("div");
    stone.className = "stone " + (move.color === BLACK ? "black" : "white");
    if (move === last) stone.classList.add("last-move");
    stone.style.left = move.x * 21 + "px";
    stone.style.top = move.y * 21 + "px";
    stone.dataset.notation = move.notation;
    els.piecesLayer.appendChild(stone);
  }
  els.boardGrid.dataset.version = String(state.version);
  els.boardGrid.dataset.gameId = state.gameId || "";
  updateStatus();
}

function updateStatus() {
  let message;
  const playerColor = state.playerColor ?? BLACK;
  const compColor = playerColor === BLACK ? WHITE : BLACK;
  const isPlayerTurn = state.activeColor === playerColor;
  const opponentName = state.mode === "assist" ? "人机" : state.characterName;
  const playerColorName = playerColor === BLACK ? "黑" : "白";
  const compColorName = compColor === BLACK ? "黑" : "白";

  if (error) message = error;
  else if (!ready) message = "正在连接棋盘…";
  else if (busy) message = "正在更新棋盘…";
  else if (state.status === "black_won") {
    message = playerColor === BLACK ? "黑棋五连，你赢啦！" : `${opponentName}黑棋五连获胜！`;
  }
  else if (state.status === "white_won") {
    message = playerColor === WHITE ? "白棋五连，你赢啦！" : `${opponentName}白棋五连获胜！`;
  }
  else if (state.status === "draw") message = "和棋啦！";
  else if (state.companionThinking) message = state.characterName + "正在思考…";
  else if (!isPlayerTurn) message = `等待${opponentName}落${compColorName}子，可点击重试。`;
  else message = native ? `你执${playerColorName} · 第 ${state.moves.length + 1} 手` : `浏览器预览 · 你执${playerColorName}`;

  els.statusText.textContent = message;
  els.statusText.title = message;
  els.statusStone.textContent = error ? "!" : state.activeColor === BLACK ? "●" : "○";
  els.btnUndo.disabled = busy || !ready || !state.moves.length;
  els.btnReset.disabled = busy || !ready;
  els.modeSelect.disabled = busy || !ready || state.companionThinking;
  els.modeSelect.value = state.mode;
  if (els.colorSelect) {
    els.colorSelect.disabled = busy || !ready || state.companionThinking;
    els.colorSelect.value = String(playerColor);
  }
  els.btnAskHelp.hidden = !native || state.mode !== "assist" || state.status !== "in_progress";
  els.btnAskHelp.disabled = busy || !ready || state.companionThinking;
  els.btnRetry.hidden = busy || state.companionThinking || (!error && isPlayerTurn);
  els.hoverReticle.hidden = true;
  els.boardGrid.setAttribute("aria-busy", String(busy || state.companionThinking));
}

function applySnapshot(snapshot) {
  if (!snapshot) return;
  if (snapshot.gameId === state.gameId && snapshot.version < state.version) return;
  const grew = snapshot.gameId === state.gameId && snapshot.moves.length > state.moves.length;
  Object.assign(state, snapshot);
  if (snapshot.playerColor !== undefined) {
    state.playerColor = snapshot.playerColor;
  }
  ready = true;
  renderBoard();
  if (grew) playStoneSound();
}

async function request(action, args = {}) {
  if (!native || busy) return;
  busy = true;
  error = "";
  updateStatus();
  const requestId = globalThis.crypto.randomUUID();
  try {
    const response = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        requests.delete(requestId);
        reject(new Error("棋盘连接未完成，请点击重试。"));
      }, 20000);
      requests.set(requestId, { resolve, reject, timer });
    });
    void response.catch(() => {});
    await emitTo("main", "gomoku:request", { requestId, action,
      gameId: state.gameId, version: state.version, mode: state.mode, playerColor: state.playerColor, ...args,
    }).catch(cause => {
      const pending = requests.get(requestId);
      if (pending) { clearTimeout(pending.timer); requests.delete(requestId); pending.reject(cause); }
    });
    await response;
  } catch (cause) { error = String(cause.message || cause); }
  finally { busy = false; updateStatus(); }
}

function canPlace() {
  const playerColor = state.playerColor ?? BLACK;
  return ready && !busy && !state.companionThinking && state.status === "in_progress" && state.activeColor === playerColor;
}

function getCoord(event) {
  const rect = els.boardGrid.getBoundingClientRect();
  const x = Math.round((event.clientX - rect.left) / (rect.width / (BOARD_SIZE - 1)));
  const y = Math.round((event.clientY - rect.top) / (rect.height / (BOARD_SIZE - 1)));
  return x >= 0 && x < BOARD_SIZE && y >= 0 && y < BOARD_SIZE ? { x, y } : null;
}

function cancelPreview() {
  clearTimeout(previewTimer);
  previewTimer = null;
  state.companionThinking = false;
}

function previewTriggerAiFirst() {
  const playerColor = state.playerColor ?? BLACK;
  if (playerColor === WHITE && state.moves.length === 0 && state.status === "in_progress") {
    state.companionThinking = true;
    const version = state.version;
    previewTimer = setTimeout(() => {
      if (version !== state.version) return;
      state.companionThinking = false;
      const move = computeBestLocalMove(state.grid, BLACK);
      if (placePiece(state, move.x, move.y, BLACK)) playStoneSound();
      renderBoard();
    }, 300);
  }
}

function previewMove(x, y) {
  const playerColor = state.playerColor ?? BLACK;
  const compColor = playerColor === BLACK ? WHITE : BLACK;
  if (!placePiece(state, x, y, playerColor)) return;
  playStoneSound();
  if (state.status === "in_progress") {
    state.companionThinking = true;
    const version = state.version;
    previewTimer = setTimeout(() => {
      if (version !== state.version) return;
      state.companionThinking = false;
      const move = computeBestLocalMove(state.grid, compColor);
      if (placePiece(state, move.x, move.y, compColor)) playStoneSound();
      renderBoard();
    }, 300);
  }
  renderBoard();
}

els.colLabels.innerHTML = Array.from({ length: BOARD_SIZE }, (_, i) => "<span>" + String.fromCharCode(65 + i) + "</span>").join("");
els.rowLabels.innerHTML = Array.from({ length: BOARD_SIZE }, (_, i) => "<span>" + (i + 1) + "</span>").join("");
els.statusText.setAttribute("role", "status");
els.boardGrid.addEventListener("mousemove", event => {
  const coord = getCoord(event);
  if (canPlace() && coord && state.grid[coord.y][coord.x] === EMPTY) {
    els.hoverReticle.hidden = false;
    els.hoverReticle.style.left = coord.x * 21 + "px";
    els.hoverReticle.style.top = coord.y * 21 + "px";
  } else els.hoverReticle.hidden = true;
});
els.boardGrid.addEventListener("mouseleave", () => { els.hoverReticle.hidden = true; });
els.boardGrid.addEventListener("click", event => {
  if (!canPlace()) return;
  const coord = getCoord(event);
  if (!coord || state.grid[coord.y][coord.x] !== EMPTY) return;
  if (native) void request("play", coord);
  else previewMove(coord.x, coord.y);
});
els.btnUndo.addEventListener("click", () => {
  if (native) void request("undo");
  else {
    cancelPreview();
    const playerColor = state.playerColor ?? BLACK;
    const steps = state.activeColor !== playerColor ? 1 : 2;
    undoPiece(state, steps);
    renderBoard();
  }
});
els.btnReset.addEventListener("click", () => {
  if (native) void request("reset", { playerColor: state.playerColor, mode: state.mode });
  else {
    cancelPreview();
    resetState(state, state.mode, state.playerColor);
    renderBoard();
    previewTriggerAiFirst();
  }
});
els.modeSelect.addEventListener("change", event => {
  if (native) void request("reset", { mode: event.target.value, playerColor: state.playerColor });
  else {
    cancelPreview();
    state.mode = event.target.value;
    resetState(state, event.target.value, state.playerColor);
    renderBoard();
    previewTriggerAiFirst();
  }
});
if (els.colorSelect) {
  els.colorSelect.addEventListener("change", event => {
    const newColor = Number(event.target.value);
    if (native) void request("reset", { playerColor: newColor, mode: state.mode });
    else {
      cancelPreview();
      resetState(state, state.mode, newColor);
      renderBoard();
      previewTriggerAiFirst();
    }
  });
}
els.btnAskHelp.addEventListener("click", () => { void request("advice"); });
els.btnRetry.addEventListener("click", () => { void request(ready ? "retry" : "connect"); });
els.btnClose.addEventListener("click", async () => {
  try {
    if (appWindow) await appWindow.close();
    else window.close();
  } catch (cause) { error = "关闭失败：" + String(cause.message || cause); updateStatus(); }
});

async function initialize() {
  if (!native) {
    state.mode = "assist";
    renderBoard();
    return;
  }
  try {
    await listen("gomoku:state", ({ payload }) => {
      applySnapshot(payload.state);
      state.companionThinking = Boolean(payload.thinking);
      error = String(payload.error || "");
      const pending = requests.get(payload.requestId);
      if (pending) { clearTimeout(pending.timer); requests.delete(payload.requestId); pending.resolve(); }
      updateStatus();
    });
    await request("connect");
    window.addEventListener("focus", () => { if (!busy) void request("connect"); });
  } catch (cause) { error = String(cause.message || cause); updateStatus(); }
}
renderBoard();
void initialize();
