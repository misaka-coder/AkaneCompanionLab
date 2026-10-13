import { normalizeActionId } from "../shared.js";
import {
  CONTROL_CENTER_BRIDGED_ACTION_IDS,
  createNotImplementedActionResult
} from "../../action-router.js";
import { backendActionRunner } from "./dispatch.js";
import { runTauriControlCenterAction } from "../tauri.js";

const bridgedActionIds = new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS);

export function createSourceActionMethods({
  fetchImpl,
  botBaseUrl,
  sessionId,
  profileUserId,
  client,
  options
}) {
  return {
    handlesAction(actionId) {
      return bridgedActionIds.has(normalizeActionId(actionId));
    },
    async runAction(actionId, payload = {}, context = {}) {
      const normalizedActionId = normalizeActionId(actionId);
      if (!bridgedActionIds.has(normalizedActionId)) {
        return createNotImplementedActionResult(normalizedActionId);
      }

      const routeAction = backendActionRunner(normalizedActionId);
      if (routeAction) {
        if (typeof fetchImpl !== "function") {
          return createNotImplementedActionResult(normalizedActionId);
        }
        return routeAction(fetchImpl, botBaseUrl, normalizedActionId, payload, {
          user_id: sessionId,
          session_id: sessionId,
          real_user_id: profileUserId,
          profile_user_id: profileUserId,
          client,
          t: String(Date.now())
        });
      }

      const tauriResult = await runTauriControlCenterAction(normalizedActionId, payload, context, options);
      if (tauriResult.status !== "not-available") {
        return tauriResult;
      }
      return createNotImplementedActionResult(normalizedActionId);
    }
  };
}
