import { createModelServiceDraft } from "../model-service.js";
import { loadPresentationPreferences } from "../presentation-preferences.js";
import { chatScope, friendlyError } from "./scope.js";

export function createSnapshotController({ store, bridge, actions, model, drafts, voice, chat, presentation }) {
  let disposed = false, refreshPromise = null, refreshing = false, publication = 0;

  function receive(viewModel) {
    if (disposed) return;
    publication += 1;
    actions.synchronizeScope(viewModel);
    const modelOwnerChanged = model.synchronizeScope(viewModel);
    const capabilityOwnerChanged = drafts.synchronizeScope(viewModel);
    voice.synchronizeScope(viewModel); chat.synchronizeScope(viewModel);
    const packId = String(viewModel?.character?.packId || "default").trim() || "default";
    presentation.synchronizePack(packId);
    store.patch(state => {
      const packChanged = state.presentationPackId !== packId;
      const firstModelRead = (!state.modelDraft || modelOwnerChanged) && viewModel?.model?.available;
      const changedChat = chatScope(state.viewModel) !== chatScope(viewModel);
      return {
        ...state, phase: refreshing ? "refreshing" : "ready", error: "", viewModel, refreshedAt: Date.now(),
        presentationPackId: packId,
        presentationPreferences: packChanged ? loadPresentationPreferences(packId) : state.presentationPreferences,
        presentationNotice: packChanged ? null : state.presentationNotice,
        modelDraft: firstModelRead ? createModelServiceDraft(viewModel.model) : modelOwnerChanged ? null : state.modelDraft,
        modelModels: modelOwnerChanged ? [] : state.modelModels,
        modelSubagentModels: modelOwnerChanged ? [] : state.modelSubagentModels,
        modelVisionModels: modelOwnerChanged ? [] : state.modelVisionModels,
        ...(capabilityOwnerChanged ? { voiceProfileSuggestion: null, voiceProfileInspection: null, voiceProfileEditorOpen: false } : {}),
        actionStates: changedChat ? Object.fromEntries(Object.entries(state.actionStates)
          .filter(([id]) => !id.startsWith("chat."))) : state.actionStates,
        chatHistory: changedChat ? { phase: "idle", error: "", sessionId: String(viewModel?.chat?.sessionId || "") } : state.chatHistory,
        chatJobControls: changedChat ? {} : state.chatJobControls
      };
    });
  }
  async function start() {
    try { await bridge.start(); }
    catch (error) {
      // Preserve the disconnected snapshot so local QQ recovery remains accessible.
      if (!disposed) store.patch({ phase: "failed", error: friendlyError(error) });
    } finally { if (disposed) bridge.stop(); }
  }
  function refresh() {
    if (disposed) return Promise.resolve(null);
    if (refreshPromise) return refreshPromise;
    refreshing = true;
    store.patch({ phase: "refreshing", error: "" });
    const requestedPublication = publication;
    refreshPromise = (async () => {
      try {
        const viewModel = await bridge.refresh();
        if (disposed) return null;
        // Bridge publications own the snapshot; a promise cannot replay an older one over them.
        if (publication === requestedPublication && viewModel) receive(viewModel);
        store.patch({ phase: "ready", refreshedAt: Date.now() });
        return viewModel;
      } catch (error) {
        if (!disposed) store.patch(state => ({ ...state,
          phase: state.viewModel ? "ready" : "failed", error: friendlyError(error) }));
        return null;
      } finally { refreshing = false; refreshPromise = null; }
    })();
    return refreshPromise;
  }
  function dispose() { disposed = true; bridge.stop(); }
  return { receive, start, refresh, dispose };
}
