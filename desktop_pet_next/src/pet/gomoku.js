import { computeBestLocalMove, BLACK, WHITE } from "../gomoku-engine.js";
import { createPluginServiceClient } from "./plugin-service.js";

/** The main window owns chat and transport; the board only displays game state. */
export function createGomokuController({ state, platform, lifecycle, scope, chat, persistence, backend,
  characterName = () => "伴侣" }) {
  const services = createPluginServiceClient({ state, platform, scope, persistence, backend });
  let current = null;

  function acceptSnapshot(entry, snapshot) {
    if (snapshot && (!entry.snapshot || snapshot.version >= entry.snapshot.version)) entry.snapshot = snapshot;
  }

  async function invokeBoard(entry, action, args = {}) {
    let content;
    try {
      content = await services.invoke(entry.service, {
        action, game_id: entry.gameId, character_name: entry.characterName, ...args,
      });
    } catch (error) {
      acceptSnapshot(entry, error.content?.state);
      if (error.reason === "unknown_capability") error.message = "五子棋插件尚未启用，请在插件设置中启用。";
      throw error;
    }
    if (!content?.state) throw new Error("棋盘服务未返回盘面。");
    acceptSnapshot(entry, content.state);
    return entry.snapshot;
  }

  async function push(entry, requestId = "", error = "") {
    if (!scope.isCurrent(entry.ticket)) return;
    // Closing the board does not cancel chat or erase the game.
    await platform.emitTo("gomoku", "gomoku:state", {
      requestId, state: entry.snapshot, thinking: entry.modelPending, error,
    }).catch(() => {});
  }

  function getEntry() {
    if (current && scope.isCurrent(current.ticket)) return current;
    const service = services.bind("akane.gomoku.service.board.v1.control");
    current = {
      ticket: service.ticket, service, gameId: `gomoku-${globalThis.crypto.randomUUID()}`,
      characterName: characterName(),
      snapshot: null, operation: false, modelPending: false,
    };
    return current;
  }

  function buildMessage(snapshot, advice) {
    const last = snapshot.moves.at(-1);
    const playerColor = snapshot.playerColor ?? BLACK;
    const compColor = playerColor === BLACK ? WHITE : BLACK;
    const pStone = playerColor === BLACK ? "黑" : "白";
    const cStone = compColor === BLACK ? "黑" : "白";
    const isFirstMove = snapshot.moves.length === 0;

    const request = advice
      ? `这一局你是参谋，请给我${pStone}棋的走法建议，不替任何一方落子。`
      : `我执${pStone}，你执${cStone}。请用五子棋 place_piece 工具落一颗${cStone}子${isFirstMove ? "（开局第一手建议下在天元 H8 附近）" : ""}，再自然地回应我。`;
    return `【五子棋${advice ? "求助" : "对弈"}】${request}\n`
      + `game_id=${snapshot.gameId}，version=${snapshot.version}。坐标 A~O 对应 x=0~14，行 1~15 对应 y=0~14。\n`
      + (last ? `上一手：${last.color === BLACK ? "黑棋" : "白棋"} ${last.notation}。\n` : "")
      + snapshot.boardText;
  }

  async function runModel(entry, advice = false) {
    if (!scope.isCurrent(entry.ticket) || entry.modelPending) return;
    if (chat.sending) {
      await push(entry, "", "伴侣正在处理另一轮对话，稍后点击重试即可。");
      return;
    }
    entry.modelPending = true;
    await push(entry);
    let polling = false;
    const timer = setInterval(async () => {
      if (polling || !scope.isCurrent(entry.ticket)) return;
      polling = true;
      const version = entry.snapshot.version;
      try {
        await invokeBoard(entry, "state");
        if (entry.snapshot.version !== version) await push(entry);
      } catch { /* The final refresh below reports a lasting failure. */ }
      finally { polling = false; }
    }, 1000);
    let error = "";
    try {
      const replied = await chat.sendMessage(buildMessage(entry.snapshot, advice), {
        requireNewTurn: true, includeAttachments: false,
      });
      if (!scope.isCurrent(entry.ticket)) return;
      await invokeBoard(entry, "state");
      const playerColor = entry.snapshot.playerColor ?? BLACK;
      const compColor = playerColor === BLACK ? WHITE : BLACK;
      const compStone = compColor === BLACK ? "黑" : "白";
      if (!replied) error = "这轮对话未完成，可以重试。";
      else if (!advice && entry.snapshot.status === "in_progress" && entry.snapshot.activeColor === compColor) {
        error = `这轮尚未落${compStone}子，可以重试或悔棋。`;
      }
    } catch (cause) { error = String(cause.message || cause); }
    finally {
      clearInterval(timer);
      entry.modelPending = false;
      await push(entry, "", error);
    }
  }

  async function handleRequest(event) {
    const { requestId, action, gameId, x, y, version, mode, playerColor } = event.payload || {};
    if (!requestId || !["connect", "play", "undo", "reset", "retry", "advice"].includes(action)) return;
    const entry = getEntry();
    if (entry.operation) { await push(entry, requestId, "棋盘正在更新，请稍候。"); return; }
    entry.operation = true;
    let modelAction = "";
    try {
      if (!entry.snapshot) await invokeBoard(entry, "open", playerColor !== undefined ? { player_color: playerColor } : {});
      if (!scope.isCurrent(entry.ticket)) return;
      if (action !== "connect" && gameId !== entry.gameId) throw new Error("会话或角色已切换，棋盘已刷新，请重新操作。");
      if (["play", "retry", "advice"].includes(action) && (entry.modelPending || chat.sending)) {
        throw new Error("伴侣正在思考，稍后再操作。");
      }
      if (action === "connect") await invokeBoard(entry, "state");
      else if (action === "play") {
        await invokeBoard(entry, "play", { x, y, version });
        if (entry.snapshot.status === "in_progress") {
          const compColor = (entry.snapshot.playerColor ?? BLACK) === BLACK ? WHITE : BLACK;
          if (entry.snapshot.mode === "assist") {
            const move = computeBestLocalMove(entry.snapshot.grid, compColor);
            await invokeBoard(entry, "computer_move", { ...move, version: entry.snapshot.version });
          } else modelAction = "move";
        }
      } else if (action === "reset") {
        await invokeBoard(entry, "reset", { mode, player_color: playerColor });
        if (entry.snapshot.status === "in_progress") {
          const pColor = entry.snapshot.playerColor ?? BLACK;
          const cColor = pColor === BLACK ? WHITE : BLACK;
          if (entry.snapshot.activeColor === cColor) {
            if (entry.snapshot.mode === "assist") {
              const move = computeBestLocalMove(entry.snapshot.grid, cColor);
              await invokeBoard(entry, "computer_move", { ...move, version: entry.snapshot.version });
            } else {
              modelAction = "move";
            }
          }
        }
      }
      else if (action === "undo") await invokeBoard(entry, "undo");
      else if (action === "advice") modelAction = "advice";
      else {
        const pColor = entry.snapshot.playerColor ?? BLACK;
        const cColor = pColor === BLACK ? WHITE : BLACK;
        if (entry.snapshot.mode === "duel" && entry.snapshot.activeColor === cColor
            && entry.snapshot.status === "in_progress") modelAction = "move";
      }
      await push(entry, requestId);
    } catch (error) {
      if (error.reason === "game_not_found" && scope.isCurrent(entry.ticket)) {
        // A backend/plugin restart clears its in-memory boards. Reconnect to
        // a fresh game instead of keeping an unusable board on screen.
        entry.gameId = `gomoku-${globalThis.crypto.randomUUID()}`;
        entry.snapshot = null;
        try { await invokeBoard(entry, "open", playerColor !== undefined ? { player_color: playerColor } : {}); }
        catch (cause) { await push(entry, requestId, String(cause.message || cause)); return; }
        await push(entry, requestId, "后端棋盘已重置，已新开一局。");
        return;
      }
      await push(entry, requestId, String(error.message || error));
    } finally { entry.operation = false; }
    if (modelAction) void runModel(entry, modelAction === "advice");
  }

  async function registerBridge() {
    if (platform.isTauriRuntime) lifecycle.track(await platform.listen("gomoku:request", handleRequest));
  }
  return { registerBridge };
}
