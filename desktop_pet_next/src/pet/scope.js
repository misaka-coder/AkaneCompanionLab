/** Rejects results captured before a connection, character or session change. */
export function createRuntimeScope(state, isDisposed = () => false) {
  let key = "";
  let revision = 0;
  const readKey = () => JSON.stringify([
    state.instanceId, state.backendUrl, state.boundBotId,
    state.profileUserId, state.characterPackId, state.sessionId
  ]);

  function capture() {
    const current = readKey();
    if (current !== key) { key = current; revision += 1; }
    return { key, revision };
  }

  function isCurrent(ticket) {
    const current = capture();
    return !isDisposed() && ticket.key === current.key && ticket.revision === current.revision;
  }

  function invalidate() { revision += 1; key = readKey(); }
  return { capture, isCurrent, invalidate };
}
