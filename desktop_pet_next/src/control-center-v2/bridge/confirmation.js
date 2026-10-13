import {
  OBSERVED_SETTINGS_ACTION_IDS,
  getSettingsActionTimeout
} from "../../control-center/settings-command-contract.js";
import { isObservedActionConfirmation } from "../view-model.js";

export const OBSERVED_ACTION_IDS = OBSERVED_SETTINGS_ACTION_IDS;
let actionOperationSequence = 0;

export function createRuntimeConfirmation({ readSnapshot, schedule = setTimeout, cancel = clearTimeout, now = Date.now }) {
  let epoch = 0;
  const pending = new Map();
  async function wait(actionId, payload, beforeSnapshot, isCurrent = () => true) {
    const version = epoch, startedAt = now(), timeoutMs = getSettingsActionTimeout(actionId, payload);
    while (version === epoch && isCurrent() && now() - startedAt < timeoutMs) {
      const current = readSnapshot();
      if (current !== beforeSnapshot && isObservedActionConfirmation(actionId, beforeSnapshot, current, payload)) return current;
      await new Promise(resolve => {
        const timer = schedule(() => { pending.delete(timer); resolve(); }, 60);
        pending.set(timer, resolve);
      });
    }
    return null;
  }
  function stop() {
    epoch += 1;
    for (const [timer, resolve] of pending) { cancel(timer); resolve(); }
    pending.clear();
  }
  return { wait, stop };
}

export function createActionOperationId(actionId) {
  actionOperationSequence += 1;
  const action = String(actionId || "action").replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();
  return `${action || "action"}-${Date.now().toString(36)}-${actionOperationSequence.toString(36)}`;
}
