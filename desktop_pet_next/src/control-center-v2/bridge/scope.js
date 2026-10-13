const text = value => String(value ?? "").trim();
const backend = value => text(value).replace(/\/+$/, "");

export function matchesLiveSource(source, live = {}) {
  return Boolean(source) && (!live.boundBotId || live.boundBotId === source.botId)
    && (!live.profileUserId || live.profileUserId === source.profileUserId)
    && (!live.backendUrl || backend(live.backendUrl) === backend(source.backendUrl));
}

// Observe every transition, including A -> B -> A with identical session IDs.
export function createBridgeScope({ readSource, readRuntime, isActive, onChange = () => {} }) {
  let version = 0, currentSource = null, signature = "";
  function synchronize() {
    const source = readSource(), live = readRuntime()?.state || {};
    const next = JSON.stringify([backend(source?.backendUrl), source?.botId, source?.profileUserId,
      source?.sessionId, source?.characterPackId, live.hostId || live.instanceId,
      backend(live.backendUrl), live.boundBotId, live.profileUserId, live.sessionId, live.characterPackId]);
    if (source === currentSource && next === signature) return false;
    currentSource = source; signature = next; version += 1; onChange(); return true;
  }
  function capture() {
    synchronize();
    const source = readSource(), live = readRuntime()?.state || {};
    return { source, version, matching: isActive() && matchesLiveSource(source, live),
      sessionId: text(live.sessionId || source?.sessionId),
      characterPackId: text(live.characterPackId ?? source?.characterPackId),
      botId: text(live.boundBotId || source?.botId) };
  }
  function isCurrent(ticket) { synchronize(); return isActive() && ticket.version === version && ticket.source === readSource(); }
  function invalidate() { version += 1; currentSource = null; signature = ""; onChange(); }
  return { synchronize, capture, isCurrent, invalidate };
}
