import { normalizeActionPresentation } from "../view-model.js";
import { chatScope, friendlyError } from "./scope.js";

// Each request owns its feedback. A previous timer or conversation cannot clear a new result.
export function createActionController({ store, bridge,
  requestFrame = requestAnimationFrame, timers = window }) {
  const operations = new Map();
  const expiryTimers = new Map();
  let disposed = false;
  const scope = (actionId, vm = store.getState().viewModel) => actionId.startsWith("memory.")
    ? vm?.system?.memorySettings?.scopeKey || ""
    : actionId.startsWith("chat.") ? chatScope(vm) : null;
  const current = (actionId, operation) => !disposed && operations.get(actionId) === operation
    && operation.scope === scope(actionId) && operation.isCurrent();

  function updateAction(actionId, value) {
    if (disposed) return;
    store.patch(state => {
      const actionStates = { ...state.actionStates };
      if (value) actionStates[actionId] = value;
      else delete actionStates[actionId];
      return { ...state, actionStates };
    });
  }

  function present(actionId, value) {
    if (disposed) return;
    timers.clearTimeout(expiryTimers.get(actionId));
    updateAction(actionId, value);
    const timer = timers.setTimeout(() => {
      if (store.getState().actionStates[actionId] === value) updateAction(actionId, null);
      if (expiryTimers.get(actionId) === timer) expiryTimers.delete(actionId);
    }, value.phase === "confirmed" ? 1800 : 5000);
    expiryTimers.set(actionId, timer);
  }

  function abandon(actionId, operation) {
    if (operations.get(actionId) !== operation) return;
    operations.delete(actionId);
    updateAction(actionId, null);
  }

  async function runAction(actionId, payload = {}, { isCurrent = () => true } = {}) {
    if (disposed) return null;
    const pending = operations.get(actionId);
    if (pending && current(actionId, pending)) return null;
    const operation = { scope: scope(actionId), isCurrent };
    operations.set(actionId, operation);
    const actionMeta = actionId === "chat.jobControl"
      ? { jobId: String(payload.jobId || payload.job_id || "").trim() } : {};
    updateAction(actionId, { phase: "pressed", label: "已按下", detail: "", ...actionMeta });
    await new Promise(resolve => requestFrame(resolve));
    if (!current(actionId, operation)) {
      abandon(actionId, operation);
      return { ok: false, status: "stale", reason: "chat_scope_changed" };
    }
    updateAction(actionId, { phase: "pending", label: "处理中", detail: "", ...actionMeta });
    let result;
    try {
      result = await bridge.runAction(actionId, payload);
    } catch (error) {
      result = { ok: false, status: "failed", reason: friendlyError(error) };
    }
    if (!current(actionId, operation)) {
      abandon(actionId, operation);
      return { ok: false, status: "stale", reason: "chat_scope_changed" };
    }
    operations.delete(actionId);
    if (actionId === "chat.jobControl") rememberChatJobControl(payload, result);
    present(actionId, { ...normalizeActionPresentation(result), ...actionMeta });
    return result;
  }

  function rememberChatJobControl(payload, result) {
    const jobId = String(payload?.jobId || payload?.job_id || result?.jobId || result?.job_id || "").trim();
    if (!jobId) return;
    store.patch(state => {
      const previous = state.chatJobControls?.[jobId] || {};
      const next = {
        ...previous, jobId,
        lastAction: String(payload?.action || result?.controlAction || "").trim(),
        ...(result?.ok ? {
          jobStatus: String(result?.jobStatus || result?.job_status || result?.status || previous.jobStatus || "").trim(),
          controlState: String(result?.controlState || result?.control_state || result?.status || previous.controlState || "").trim(),
          error: ""
        } : { error: String(result?.reason || result?.error || result?.status || "任务控制没有完成").trim() })
      };
      return { ...state, chatJobControls: { ...(state.chatJobControls || {}), [jobId]: next } };
    });
  }

  function dispose() {
    disposed = true;
    operations.clear();
    for (const timer of expiryTimers.values()) timers.clearTimeout(timer);
    expiryTimers.clear();
  }
  function synchronizeScope(viewModel) {
    for (const [id, operation] of operations) {
      if (operation.scope !== null && operation.scope !== scope(id, viewModel)) {
        abandon(id, operation);
      }
    }
  }
  return { runAction, present, dispose, synchronizeScope };
}
