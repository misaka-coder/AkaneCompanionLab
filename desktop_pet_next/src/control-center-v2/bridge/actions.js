import { serializeBrowserAttachments } from "../../pending-attachments.js";
import { modelServiceOperation, runModelServiceBridgeAction } from "../model-service.js";
import { isQqSetupAction } from "../qq-setup.js";
import { observedActionOutcome } from "../view-model.js";
import { OBSERVED_ACTION_IDS, createActionOperationId } from "./confirmation.js";
import { formatError } from "./errors.js";

export function createBridgeActions({ host, readRouter, readRuntime, captureBinding, isCurrentBinding,
  chat, confirmations, readComputerUse, toolExposure, memorySettings, qqSetup, rebindSource, refresh,
  serializeAttachments = serializeBrowserAttachments }) {
  let botSelectionVersion = 0;
  async function runAction(actionId, payload = {}) {
    const ticket = captureBinding();
    if (!isCurrentBinding(ticket) || !readRouter()) return { ok: false, status: "not-available", actionId, reason: "control_center_bridge_not_started" };
    if (actionId.startsWith("computerUse.")) return readComputerUse().run(actionId.slice("computerUse.".length), payload);
    if (actionId.startsWith("memory.")) return memorySettings.run(actionId, payload);
    if (actionId === "abilities.toolExposure.refresh") return toolExposure.refresh();
    if (actionId === "abilities.toolExposure.save") return toolExposure.save(payload);
    const selectionVersion = actionId === "settings.selectBot" ? ++botSelectionVersion : botSelectionVersion;
    const current = () => isCurrentBinding(ticket) && (actionId !== "settings.selectBot" || selectionVersion === botSelectionVersion);
    const beforeRuntimeSnapshot = readRuntime(), chatScope = chat.captureScope();
    if (actionId !== "settings.selectBot" && !chatScope.matching) return stale();
    if (actionId.startsWith("chat.")) payload = { ...payload, draftToken: payload.draftToken || beforeRuntimeSnapshot?.attachmentDraftToken || "" };
    if (actionId === "chat.attach" && payload.browserFiles) {
      try {
        const { browserFiles, ...rest } = payload;
        payload = { ...rest, files: await serializeAttachments(browserFiles) };
      } catch (error) {
        return current() && chat.isCurrent(chatScope) ? { ok: false, status: "failed", reason: formatError(error) } : stale();
      }
    }
    if (!current() || (actionId.startsWith("chat.") && !chat.isCurrent(chatScope))) return stale();
    if (isQqSetupAction(actionId)) return qqSetup.run(actionId);
    const actionPayload = OBSERVED_ACTION_IDS.has(actionId)
      ? { ...payload, operationId: createActionOperationId(actionId) } : payload;
    let result = modelServiceOperation(actionId)
      ? await runModelServiceBridgeAction(ticket.source, actionId, actionPayload)
      : await readRouter().run(actionId, actionPayload, { source: "control-center-v2" });
    if (!current()) return stale();
    if (result.ok && OBSERVED_ACTION_IDS.has(actionId)) {
      const observedSnapshot = await confirmations.wait(actionId, actionPayload, beforeRuntimeSnapshot, current);
      if (!current()) return stale();
      const outcome = observedSnapshot ? observedActionOutcome(actionId, actionPayload, observedSnapshot) : null;
      result = observedSnapshot
        ? { ...result, ...(outcome || { status: "completed" }), observed: true, refresh: actionId === "settings.selectBot" }
        : { ...result, ok: false, status: "execution_unknown", reason: "runtime_state_not_confirmed",
            observed: false, refresh: false };
    }
    if (result.ok && actionId === "settings.selectBot") {
      if (!await rebindSource(current, String(actionPayload.value || ""))) return stale();
    }
    if (result.refresh) {
      const refreshTicket = captureBinding();
      if (host.isTauri) {
        try { await host.requestSnapshot(); } catch { /* Backend refresh also works without live event delivery. */ }
      }
      if (isCurrentBinding(refreshTicket)) {
        try { await refresh(); } catch { /* The action's observed result remains authoritative. */ }
      }
    }
    return result;
  }
  return { runAction };
}

function stale() { return { ok: false, status: "stale", reason: "control_center_source_changed", refresh: false }; }
