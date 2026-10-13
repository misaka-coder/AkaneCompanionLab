/** Feed satellite events into the ordinary chat stream, once per scoped turn. */
export function createBackgroundTurnInbox({
  lifecycle, scope, matches, available, begin, end, process, onError, idleTimeout
}) {
  let active = null;
  const retired = new Set();
  const pending = [];
  let pendingTimer = 0;

  function drainPending() {
    lifecycle.clearTimeout(pendingTimer);
    pendingTimer = 0;
    if (lifecycle.disposed || !pending.length) return;
    if (!active && !available()) {
      pendingTimer = lifecycle.setTimeout(drainPending, 200);
      return;
    }
    for (const envelope of pending.splice(0)) receive(envelope);
  }

  function retire(id) {
    retired.add(id);
    if (retired.size > 128) retired.delete(retired.values().next().value);
  }

  function wake(owner) {
    owner.wake?.();
    owner.wake = null;
  }

  function finish(owner, discard = false) {
    owner.closed = true;
    if (discard) owner.events.length = 0;
    lifecycle.clearTimeout(owner.timer);
    retire(owner.id);
    wake(owner);
  }

  function cancel() {
    pending.length = 0;
    lifecycle.clearTimeout(pendingTimer);
    if (active) finish(active, true);
  }

  async function* events(owner) {
    while (owner.events.length || !owner.closed) {
      if (!scope.isCurrent(owner.ticket)) { finish(owner, true); return; }
      if (!owner.events.length) await new Promise(resolve => { owner.wake = resolve; });
      if (owner.events.length) yield owner.events.shift();
    }
  }

  function receive(envelope) {
    if (lifecycle.disposed || !envelope || typeof envelope !== "object") return false;
    const id = String(envelope.stream_id || "");
    const sequence = envelope.sequence;
    const batch = envelope.events;
    if (!id || !Number.isSafeInteger(sequence) || sequence < 0 || !Array.isArray(batch) || !batch.length) return false;
    if (active && !scope.isCurrent(active.ticket)) cancel();
    if (retired.has(id) || !matches(envelope)) return false;
    if (active && active.id !== id) {
      if (active.closed && pending.length < 64) { pending.push(envelope); return true; }
      retire(id);
      return false;
    }
    if (!active) {
      if (!available()) {
        if (pending.length >= 64) return false;
        pending.push(envelope);
        if (!pendingTimer) pendingTimer = lifecycle.setTimeout(drainPending, 200);
        return true;
      }
      if (sequence !== 0 || batch[0]?.type !== "stream_start") return false;
      const owner = { id, sequence: -1, ticket: scope.capture(), token: begin(), events: [],
        wake: null, timer: 0, closed: false };
      active = owner;
      owner.done = Promise.resolve(process(events(owner), owner.token)).catch(error => {
        if (scope.isCurrent(owner.ticket)) onError(error, owner.token);
      }).finally(() => {
        finish(owner, true);
        end(owner.token);
        if (active === owner) active = null;
        drainPending();
      });
    }
    const owner = active;
    if (owner.closed || sequence <= owner.sequence) return false;
    if (sequence !== owner.sequence + 1) {
      onError(new Error("后台进度传输中断，请查看任务状态。"), owner.token);
      finish(owner, true);
      return false;
    }
    owner.sequence = sequence;
    owner.events.push(...batch);
    lifecycle.clearTimeout(owner.timer);
    if (batch.some(event => event?.type === "stream_end")) finish(owner);
    else owner.timer = lifecycle.setTimeout(() => {
      if (scope.isCurrent(owner.ticket)) onError(new Error("后台任务暂时没有反馈，请查看任务状态。"), owner.token);
      finish(owner, true);
    }, idleTimeout);
    wake(owner);
    return true;
  }

  lifecycle.track(cancel);
  return { receive, cancel, get active() { return active !== null; } };
}
