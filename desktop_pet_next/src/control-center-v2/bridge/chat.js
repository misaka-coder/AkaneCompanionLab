import { chatSessionId, createTrailingAsyncRefresh, mergeChatSessions } from "../chat-history.js";
import { createBridgeScope } from "./scope.js";
import { formatError } from "./errors.js";

export function createBridgeChat({ readSource, readRuntime, readSnapshot, updateSnapshot, publish,
  isActive, schedule = setTimeout, cancel = clearTimeout }) {
  let refreshQueue = null, historyRead = null, timer = null, lastSignature = "";
  const scope = createBridgeScope({ readSource, readRuntime, isActive, onChange() {
    refreshQueue = null;
    historyRead = null;
    lastSignature = "";
    if (timer !== null) cancel(timer);
    timer = null;
    updateSnapshot(({ chatSession, chatRuntime, ...snapshot }) => ({ ...snapshot,
      chatRuntime: { status: chatSession || chatRuntime?.status === "switching" ? "switching" : "loading", reason: "" } }));
  } });

  function matchesSession(ticket, session) {
    const botId = String(session?.bot_id || "");
    const pack = session?.session?.character_pack_id ?? session?.character_pack_id;
    return (!ticket.sessionId || chatSessionId(session) === ticket.sessionId)
      && (!botId || botId === ticket.botId) && (pack == null || String(pack) === ticket.characterPackId);
  }
  async function readSession(ticket) {
    if (!ticket.matching || !scope.isCurrent(ticket) || typeof ticket.source?.readChatSession !== "function") return null;
    let session, reason;
    try {
      session = await ticket.source.readChatSession({ sessionId: ticket.sessionId, characterPackId: ticket.characterPackId });
      reason = session ? "" : ticket.source.getChatSessionError?.() || "chat-session-unavailable";
    } catch (error) { reason = formatError(error); }
    if (!scope.isCurrent(ticket) || !readSnapshot()) return null;
    if (session && !matchesSession(ticket, session)) {
      session = null;
      reason = "chat_session_scope_mismatch";
    }
    updateSnapshot(snapshot => session
      ? { ...snapshot, chatSession: mergeChatSessions(snapshot.chatSession, session), chatRuntime: { status: "available", reason: "" } }
      : { ...snapshot, chatRuntime: { status: "failed", reason } });
    publish();
    return session || null;
  }
  function refreshChatSession() {
    const ticket = scope.capture();
    if (!refreshQueue || !scope.isCurrent(refreshQueue.ticket)) {
      refreshQueue = { ticket, run: createTrailingAsyncRefresh(() => readSession(ticket)) };
    }
    return refreshQueue.run();
  }
  function scheduleChatRefresh() {
    const ticket = scope.capture(), runtime = readRuntime() || {};
    const active = runtime.active || {}, command = runtime.settingsCommandResult || {};
    const signature = JSON.stringify([ticket.version, Boolean(active.sending), Boolean(active.replyDisplayActive),
      runtime.runtimeMode, command.command, command.operationId, command.at, command.status]);
    if (!isActive() || signature === lastSignature) return;
    lastSignature = signature;
    if (timer !== null) cancel(timer);
    timer = schedule(() => { timer = null; void refreshChatSession(); }, active.sending ? 260 : 80);
  }
  async function loadOlderChatMessages(options = {}) {
    const ticket = scope.capture();
    if (historyRead && scope.isCurrent(historyRead.ticket)) return historyRead.promise;
    const currentSession = readSnapshot()?.chatSession;
    if (!ticket.matching || typeof ticket.source?.readChatHistoryPage !== "function" || !currentSession) {
      return { ok: false, status: "not-available", reason: "chat_history_paging_unavailable" };
    }
    if (!matchesSession(ticket, currentSession)
      || (options.characterPackId != null && String(options.characterPackId) !== ticket.characterPackId)) return stale();
    const sessionId = chatSessionId(currentSession);
    const beforeSeq = Number(options.beforeSeq ?? currentSession.message_page?.next_before_seq);
    if (!sessionId || !Number.isFinite(beforeSeq) || beforeSeq <= 0) {
      return { ok: false, status: "no-more-history", reason: "chat_history_cursor_unavailable" };
    }
    const operation = { ticket, promise: null };
    historyRead = operation;
    operation.promise = (async () => {
      let page;
      try {
        page = await ticket.source.readChatHistoryPage({ sessionId, characterPackId: ticket.characterPackId,
          beforeSeq, limit: options.limit ?? 60 });
      } catch (error) {
        return scope.isCurrent(ticket) ? { ok: false, status: "failed", reason: formatError(error) } : stale();
      }
      if (!scope.isCurrent(ticket) || !matchesSession(ticket, readSnapshot()?.chatSession)) return stale();
      if (!page) return { ok: false, status: "failed", reason: "chat_history_request_failed" };
      const previous = readSnapshot().chatSession, previousCount = previous.messages?.length || 0;
      const merged = mergeChatSessions(previous, { ...previous, messages: page.messages, message_page: page.message_page },
        { preferIncomingPage: true });
      updateSnapshot(snapshot => ({ ...snapshot, chatSession: merged }));
      publish();
      return { ok: true, status: "loaded", count: Math.max(0, (merged.messages?.length || 0) - previousCount),
        hasMore: Boolean(merged.message_page?.has_more) };
    })();
    try { return await operation.promise; } finally { if (historyRead === operation) historyRead = null; }
  }
  function stop() { scope.invalidate(); }
  return { refresh: refreshChatSession, schedule: scheduleChatRefresh, loadOlderChatMessages,
    synchronize: scope.synchronize, captureScope: scope.capture, isCurrent: scope.isCurrent, stop };
}

function stale() { return { ok: false, status: "stale", reason: "chat_session_changed" }; }
