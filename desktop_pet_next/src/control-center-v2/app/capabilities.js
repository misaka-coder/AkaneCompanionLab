import { capabilityFormPayload } from "./capability-payload.js";

export function createCapabilityController({ root, store, actions, drafts, voice }) {
  function handleClick(event) {
    const exposure = event.target.closest("button[data-exposure-mode], button[data-exposure-search], button[data-exposure-refresh]");
    if (exposure) {
      if (exposure.disabled) return true;
      if (exposure.hasAttribute("data-exposure-refresh")) void actions.runAction("abilities.toolExposure.refresh");
      else {
        const data = exposure.dataset;
        void actions.runAction("abilities.toolExposure.save", {
          id: data.exposureId, source: data.exposureSource, mode: data.exposureMode,
          ...(data.exposureDefault ? { defaultMode: data.exposureMode } : {}),
          ...(data.exposureSearch !== undefined ? { searchEnabled: data.exposureSearch === "true" } : {})
        });
      }
      return true;
    }
    const market = event.target.closest("button[data-plugin-market-stage]");
    if (market) {
      const entry = store.getState().viewModel?.abilities?.plugins?.market?.entries?.find(item => item.pluginId === market.dataset.pluginMarketStage);
      if (!market.disabled && entry) void actions.runAction("abilities.plugin.stageMarket", { pluginId: entry.pluginId, digest: entry.digest });
      return true;
    }
    const install = event.target.closest("button[data-plugin-stage-install]");
    if (install) {
      const stageId = String(install.dataset.pluginStageInstall || "").trim();
      const stage = store.getState().viewModel?.abilities?.plugins?.stages?.find(item => item.stageId === stageId && item.ok);
      if (!install.disabled && stage) void actions.runAction("abilities.plugin.install", { stageId, approvedPermissions: [...stage.permissions] });
      return true;
    }
    const picker = event.target.closest("button[data-plugin-path-picker]");
    if (picker) {
      if (!picker.disabled) void pickPluginPath(picker);
      return true;
    }
    const discard = event.target.closest("button[data-plugin-stage-discard]");
    if (discard) {
      const stageId = String(discard.dataset.pluginStageDiscard || "").trim();
      if (!discard.disabled && stageId) void actions.runAction("abilities.plugin.discardStage", { stageId });
      return true;
    }
    const uninstall = event.target.closest("button[data-plugin-uninstall]");
    if (uninstall) {
      const pluginId = String(uninstall.dataset.pluginUninstall || "").trim();
      if (!uninstall.disabled && pluginId) void actions.runAction("abilities.plugin.uninstall", { pluginId });
      return true;
    }
    const request = event.target.closest("button[data-approval-request]");
    if (request) {
      if (!request.disabled) void actions.runAction("abilities.approvalRequest.decide", { requestId: request.dataset.approvalRequest, decision: request.dataset.decision });
      return true;
    }
    const approval = event.target.closest("button[data-approval-mode]");
    if (!approval) return false;
    if (!approval.disabled) void actions.runAction("abilities.approvalPolicy.save", { familyId: approval.dataset.approvalFamily, mode: approval.dataset.approvalMode });
    return true;
  }

  async function pickPluginPath(picker) {
    const form = picker.closest('[data-capability-form="plugin-stage"]');
    const ticket = drafts.ticket(form);
    const actionId = String(picker.dataset.pluginPathPicker || "").trim();
    const result = await actions.runAction(actionId, {}, ticket);
    const path = String(result?.path || "").trim();
    if (!result?.ok || !path || !ticket.isCurrent()) return result;
    const input = root.querySelector('[data-capability-form="plugin-stage"] [name="pluginPath"]');
    if (input) { input.value = path; input.dispatchEvent(new Event("input", { bubbles: true })); input.focus(); }
    return result;
  }

  function handleSubmit(event) {
    const form = event.target.closest("[data-capability-form]");
    if (!form) return false;
    event.preventDefault();
    const actionId = event.submitter?.dataset.action;
    if (!actionId || event.submitter?.disabled) return true;
    const payload = capabilityFormPayload(form, actionId);
    if (payload) void runCapabilityFormAction(actionId, payload, drafts.ticket(form));
    return true;
  }

  async function runCapabilityFormAction(actionId, payload, ticket) {
    if (actionId === "abilities.provider.voiceProfile.inspectFolder") return voice.inspectFolder(payload, ticket);
    const result = await actions.runAction(actionId, payload, ticket);
    if (actionId === "abilities.plugin.connection.save" && result?.ok && ticket.isCurrent()) {
      drafts.discard(ticket.key);
      store.patch({});
    }
    return result;
  }
  return { handleClick, handleSubmit, runCapabilityFormAction, pickPluginPath };
}
