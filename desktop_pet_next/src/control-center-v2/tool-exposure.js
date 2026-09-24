// Preference requests are bound to a backend/profile/session/character scope.
// A late read/save can never publish into a newly selected scope.
export function createToolExposureController({ onChange = () => {} } = {}) {
  let source = null;
  let scope = {};
  let key = "";
  let sequence = 0;
  let state = { phase: "idle", data: null, error: "", notice: "" };
  const publish = patch => { state = { ...state, ...patch }; onChange(state); };
  const current = (version, requestedSource) => version === sequence && requestedSource === source;
  return {
    snapshot: () => state,
    bind(nextSource, nextScope = {}) {
      const nextKey = JSON.stringify([nextSource?.backendUrl, nextSource?.botId, nextSource?.profileUserId,
        nextScope.sessionId, nextScope.characterPackId]);
      if (nextKey === key && nextSource === source) return false;
      source = nextSource; scope = { ...nextScope }; key = nextKey; sequence += 1;
      publish({ phase: source ? "idle" : "unavailable", data: null, error: "", notice: "" });
      if (source) void this.refresh();
      return true;
    },
    async refresh() {
      if (!source?.readToolExposure || state.phase === "saving") return;
      const version = ++sequence, requestedSource = source, requestedScope = { ...scope };
      publish({ phase: "loading", error: "" });
      try {
        const data = await requestedSource.readToolExposure(requestedScope);
        if (!current(version, requestedSource)) return;
        publish({ phase: data?.ok ? "ready" : "failed", data,
          error: data?.ok ? "" : String(data?.reason || data?.status || "tool_exposure_read_failed") });
      } catch (error) {
        if (current(version, requestedSource)) publish({ phase: "failed", error: String(error?.message || error) });
      }
    },
    async save(change = {}) {
      if (!source?.saveToolExposure || state.phase !== "ready" || !state.data?.preferences) return { ok: false, status: "not_ready" };
      const version = ++sequence, requestedSource = source, requestedScope = { ...scope };
      const preferences = state.data.preferences;
      let toolModes = { ...preferences.toolModes };
      if (change.id) toolModes[change.id] = change.mode;
      if (change.source) for (const row of state.data.tools || []) {
        if (row.source === change.source) toolModes[row.id] = change.mode;
      }
      const payload = { ...preferences, toolModes,
        ...(typeof change.searchEnabled === "boolean" ? { searchEnabled: change.searchEnabled } : {}),
        ...(change.defaultMode ? { defaultMode: change.defaultMode } : {}) };
      publish({ phase: "saving", error: "", notice: "" });
      try {
        const result = await requestedSource.saveToolExposure(payload, requestedScope);
        if (!current(version, requestedSource)) return { ok: false, status: "scope_changed" };
        if (!result?.ok) {
          publish({ phase: "failed", error: String(result?.reason || result?.status || "tool_exposure_save_failed") });
          return result;
        }
        publish({ phase: result.state?.ok ? "ready" : "failed", data: result.state || null,
          notice: "目标偏好已保存。", error: result.state?.ok ? "" : "偏好已保存，当前暴露状态读取失败，请刷新。" });
        return result;
      } catch (error) {
        if (current(version, requestedSource)) publish({ phase: "failed", error: String(error?.message || error) });
        return { ok: false, status: "request_failed" };
      }
    },
    stop() { source = null; sequence += 1; }
  };
}
