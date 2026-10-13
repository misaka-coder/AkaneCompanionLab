import { MODEL_SERVICE_ACTIONS } from "../model-service.js";

export function perceptionRenderScope(state) {
  const vm = state.viewModel;
  return JSON.stringify([state.activePage, vm?.shell?.instanceLabel, vm?.bots?.activeId,
    vm?.character?.packId, vm?.chat?.sessionId, vm?.perception?.available, vm?.shell?.connected]);
}

export function modelRenderScope(state) {
  const draft = state.modelDraft;
  const subagent = draft?.subagent;
  return JSON.stringify([state.viewModel?.model, state.phase, state.error,
    draft?.providerId, draft?.useForVision, draft?.standaloneVision, draft?.clearApiKey,
    subagent?.mode, subagent?.providerId, subagent?.clearApiKey,
    state.modelModels || [], state.modelSubagentModels || [], state.modelVisionModels || [],
    ...Object.values(MODEL_SERVICE_ACTIONS).map(id => state.actionStates?.[id])]);
}

export function isInteractingWithForm(element) {
  return Boolean(element && (
    element.matches("select, input[list], [data-model-field], [data-subagent-field], [data-perception-choice]") ||
    element.matches("input:not([type='button']):not([type='submit']):not([type='checkbox']):not([type='radio']), textarea")
  ));
}

export function renderDecision(state, previous, activeElement, hasNewChatMessage, dragging = false) {
  const pageChanged = previous.page !== state.activePage;
  const scopeChanged = previous.scope !== perceptionRenderScope(state);
  // A new scope must replace old controls even while their input/select is focused.
  if (pageChanged || scopeChanged) return "render";
  if (state.activePage === "system" && !dragging && activeElement?.matches("[data-memory-group-filter]")) return "render";
  if (dragging || (isInteractingWithForm(activeElement) &&
    (!activeElement.matches("[data-chat-input]") || !hasNewChatMessage))) return "defer";
  if (state.activePage === "model" && modelRenderScope(state) === previous.modelScope) return "skip";
  return "render";
}
