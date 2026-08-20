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

export function createInitialControlCenterState() {
  return {
    phase: "connecting",
    refreshedAt: 0,
    error: "",
    viewModel: null,
    actionStates: {}
  };
}
