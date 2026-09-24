import { DEFAULT_PRESENTATION_PREFERENCES } from "./presentation-preferences.js";

export function createControlCenterStore(initialState) {
  let state = initialState;
  const listeners = new Set();

  return {
    getState() {
      return state;
    },
    setState(nextState) {
      state = typeof nextState === "function" ? nextState(state) : nextState;
      for (const listener of listeners) listener(state);
    },
    patch(patch) {
      this.setState((current) => typeof patch === "function" ? patch(current) : ({ ...current, ...patch }));
    },
    subscribe(listener) {
      listeners.add(listener);
      listener(state);
      return () => listeners.delete(listener);
    }
  };
}

export function createInitialControlCenterState(options = {}) {
  return {
    activePage: options.activePage || "overview",
    phase: "connecting",
    refreshedAt: 0,
    error: "",
    viewModel: null,
    actionStates: {},
    presentationPackId: "",
    presentationPreferences: DEFAULT_PRESENTATION_PREFERENCES,
    framingTarget: "portrait",
    presentationNotice: null,
    modelDraft: null,
    modelModels: [],
    chatHistory: { phase: "idle", error: "", sessionId: "" },
    chatJobControls: {},
    voiceProfileSuggestion: null,
    voiceProfileInspection: null,
    voiceProfileEditorOpen: false
  };
}
