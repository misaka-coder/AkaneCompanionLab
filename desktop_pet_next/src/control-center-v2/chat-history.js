export function mergeChatSessions(current, incoming, options = {}) {
  if (!incoming || typeof incoming !== "object") return current || null;
  if (!current || typeof current !== "object" || chatSessionId(current) !== chatSessionId(incoming)) return incoming;
  for (const [left, right] of [[current.bot_id, incoming.bot_id],
    [current.session?.character_pack_id || current.character_pack_id, incoming.session?.character_pack_id || incoming.character_pack_id]]) {
    if (left && right && left !== right) return incoming;
  }
  const currentMessages = Array.isArray(current.messages) ? current.messages : [];
  const incomingMessages = Array.isArray(incoming.messages) ? incoming.messages : [];
  const byId = new Map();
  for (const message of [...currentMessages, ...incomingMessages]) {
    if (!message || typeof message !== "object") continue;
    const sourceId = String(message.source_id || message.sourceId || "").trim();
    const seqNo = Number(message.seq_no ?? message.seqNo);
    const key = sourceId || (Number.isFinite(seqNo) && seqNo > 0
      ? `seq:${seqNo}`
      : `content:${String(message.role || "")}:${String(message.timestamp || "")}:${String(message.content || "")}`);
    byId.set(key, message);
  }
  const messages = Array.from(byId.values()).sort((left, right) => {
    const leftSeq = Number(left.seq_no ?? left.seqNo);
    const rightSeq = Number(right.seq_no ?? right.seqNo);
    if (Number.isFinite(leftSeq) && Number.isFinite(rightSeq) && leftSeq !== rightSeq) return leftSeq - rightSeq;
    return Number(left.timestamp || 0) - Number(right.timestamp || 0);
  });
  const currentFirstSeq = Number(currentMessages[0]?.seq_no ?? currentMessages[0]?.seqNo);
  const incomingFirstSeq = Number(incomingMessages[0]?.seq_no ?? incomingMessages[0]?.seqNo);
  const currentHasOlderWindow = Number.isFinite(currentFirstSeq) && Number.isFinite(incomingFirstSeq) && currentFirstSeq < incomingFirstSeq;
  const messagePage = options.preferIncomingPage || !currentHasOlderWindow
    ? incoming.message_page || current.message_page
    : current.message_page || incoming.message_page;
  return {
    ...current,
    ...incoming,
    session: incoming.session || current.session,
    messages,
    ...(messagePage ? { message_page: messagePage } : {})
  };
}

export function chatSessionId(value) {
  return String(value?.session?.session_id || value?.session?.sessionId || value?.session_id || value?.sessionId || "").trim();
}

export function prependedHistoryScrollTop(anchor, nextScrollHeight) {
  const previousHeight = Math.max(0, Number(anchor?.scrollHeight) || 0);
  const previousTop = Math.max(0, Number(anchor?.scrollTop) || 0);
  const nextHeight = Math.max(0, Number(nextScrollHeight) || 0);
  return previousTop + Math.max(0, nextHeight - previousHeight);
}

export function createTrailingAsyncRefresh(run) {
  if (typeof run !== "function") throw new TypeError("refresh_runner_required");
  let active = null;
  let trailing = false;

  const request = () => {
    if (active) {
      trailing = true;
      return active;
    }
    const current = (async () => {
      let result = null;
      do {
        trailing = false;
        result = await run();
      } while (trailing);
      return result;
    })();
    active = current;
    const settle = () => {
      if (active === current) active = null;
      if (trailing) void request();
    };
    void current.then(settle, settle);
    return current;
  };

  return request;
}
