import { renderControlCenterShell } from "../components/shell.js";
import { perceptionRenderScope, modelRenderScope, isInteractingWithForm, renderDecision } from "./render-policy.js";

export function createControlCenterRenderer({ root, store, model, drafts, voice, chat, presentation,
  documentRef = document, renderShell = renderControlCenterShell }) {
  let previous = { page: "", scope: "", modelScope: "" }, deferred = false;
  let setupExpanded = null, setupCoreComplete = null;
  const pageScrollTop = new Map();

  function render(state) {
    drafts.synchronizePage(state.activePage);
    const decision = renderDecision(state, previous, documentRef.activeElement, chat.hasNewMessage(state), presentation.isDragging());
    if (decision === "defer") { deferred = true; return; }
    deferred = false;
    if (decision === "skip") return;
    const scopeChanged = previous.scope !== perceptionRenderScope(state);
    const modelFocus = scopeChanged ? null : model.captureFocusedModelField();
    const memoryFilter = state.activePage === "system" && !scopeChanged && documentRef.activeElement?.matches("[data-memory-group-filter]")
      ? { start: documentRef.activeElement.selectionStart, end: documentRef.activeElement.selectionEnd } : null;
    drafts.capture();
    const setup = root.querySelector(".setup-center");
    if (setup) setupExpanded = setup.open;
    const nextCoreComplete = Boolean(state.viewModel?.setup?.coreComplete);
    if (setupCoreComplete === false && nextCoreComplete) setupExpanded = false;
    setupCoreComplete = state.viewModel?.setup ? nextCoreComplete : null;
    const currentViewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
    if (currentViewport && previous.page) pageScrollTop.set(previous.page, currentViewport.scrollTop);
    const chatSelection = chat.beforeRender();
    voice.beforeRender();
    // Input/change handlers already keep the draft current. Reading the old
    // DOM here would overwrite a model selected by a completed probe/save.
    const renderState = state;
    renderShell(root, { ...renderState, setupExpanded });
    drafts.restore();
    const viewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
    if (viewport) viewport.scrollTop = Math.min(pageScrollTop.get(state.activePage) || 0,
      Math.max(0, viewport.scrollHeight - viewport.clientHeight));
    previous = { page: state.activePage, scope: perceptionRenderScope(state), modelScope: modelRenderScope(renderState) };
    presentation.applyCurrent();
    voice.afterRender();
    model.restoreFocusedModelField(modelFocus);
    if (memoryFilter) {
      const input = root.querySelector("[data-memory-group-filter]");
      input?.focus({ preventScroll: true });
      input?.setSelectionRange(memoryFilter.start, memoryFilter.end);
    }
    chat.afterRender(state, chatSelection);
  }
  function flush() {
    if (deferred && !presentation.isDragging() && !isInteractingWithForm(documentRef.activeElement)) render(store.getState());
  }
  return { render, flush };
}
