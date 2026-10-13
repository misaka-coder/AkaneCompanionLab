// Scope-owned reads and saves. The view only reflects acknowledged persisted state.
export function createMemorySettingsController({ onChange = () => {}, invoke, desktop = false } = {}) {
  let source = null, scope = {}, key = "", sequence = 0;
  let state = { phase: "idle", data: null, error: "", notice: "", restartAvailable: false, scopeKey: "" };
  const publish = patch => { state = { ...state, ...patch }; onChange(); };
  const current = (version, target) => version === sequence && source === target;
  const native = (target, action) => invoke("memory_backend_restart", {
    request: { action, backendUrl: target.backendUrl, botId: target.botId }
  });
  const stale = () => ({ ok: false, status: "stale", reason: "memory_settings_scope_changed" });
  const errorReason = result => String(result?.reason || result?.status || "memory_settings_request_failed");
  const controller = {
    snapshot: () => state,
    bind(next, nextScope = {}) {
      const nextKey = JSON.stringify([next?.backendUrl, next?.botId, nextScope.characterPackId]);
      if (next === source && nextKey === key) return false;
      source = next; scope = { ...nextScope }; key = nextKey; sequence++;
      publish({ phase: source?.readMemorySettings ? "idle" : "unavailable", data: null, error: "", notice: "",
        restartAvailable: false, scopeKey: key });
      if (source?.readMemorySettings) void controller.refresh();
      return true;
    },
    async refresh() {
      if (!source?.readMemorySettings || ["saving", "restarting"].includes(state.phase)) return { ok: false, status: "busy" };
      const version = ++sequence, target = source, requestedScope = { ...scope };
      publish({ phase: "loading", error: "" });
      const results = await Promise.allSettled([target.readMemorySettings(requestedScope),
        desktop ? native(target, "detect") : Promise.resolve({ ok: false })]);
      if (!current(version, target)) return stale();
      let data = results[0].status === "fulfilled" ? results[0].value : { ok: false, reason: "memory_settings_read_failed" };
      if (data?.ok && (data.botId !== target.botId || (requestedScope.characterPackId
          && data.characterPackId !== requestedScope.characterPackId))) {
        data = { ok: false, reason: "memory_settings_scope_mismatch" };
      }
      if (data?.ok && (!Array.isArray(data.settings) || !Array.isArray(data.groups))) {
        data = { ok: false, reason: "memory_settings_bad_response" };
      }
      const restart = results[1].status === "fulfilled" ? results[1].value : { ok: false };
      publish({ phase: data?.ok ? "ready" : "failed", data: data?.ok ? data : state.data,
        restartAvailable: restart?.ok === true, error: data?.ok ? "" : errorReason(data) });
      return data;
    },
    async run(action, payload = {}) {
      if (action === "memory.refresh") return controller.refresh();
      if (!source || state.phase !== "ready" || !state.data) return { ok: false, status: "not_ready" };
      if (action === "memory.restart" && !state.restartAvailable) return { ok: false, reason: "managed_launcher_unavailable" };
      const version = ++sequence, target = source, requestedScope = { ...scope };
      const restarting = action === "memory.restart";
      publish({ phase: restarting ? "restarting" : "saving", error: "", notice: "" });
      let result;
      try {
        if (restarting) result = await native(target, "restart");
        else if (action.startsWith("memory.setting.")) {
          const setting = action.slice("memory.setting.".length);
          if (!state.data.settings.some(row => row.key === setting) || typeof payload.value !== "boolean") throw Error("invalid_memory_setting");
          result = await target.updateSetting(setting, payload.value);
          if (current(version, target) && result?.ok) {
            publish({ data: { ...state.data, settings: state.data.settings.map(row => row.key === setting
              ? { ...row, current: result.current, desired: result.desired, pendingRestart: result.pendingRestart } : row) } });
          }
        } else if (action.startsWith("memory.group.")) {
          const group = action.slice("memory.group.".length);
          result = await target.saveMemoryReceiver(group, payload.value, requestedScope);
          if (current(version, target) && result?.ok) {
            publish({ data: { ...state.data, groups: state.data.groups.map(row => row.groupId === group
              ? { ...row, enabled: result.enabled } : row) } });
          }
        } else throw Error("invalid_memory_action");
      } catch (error) { result = { ok: false, reason: String(error?.message || "memory_settings_request_failed") }; }
      if (!current(version, target)) return stale();
      publish({ phase: "ready", error: result?.ok ? "" : errorReason(result),
        notice: result?.ok ? restarting ? "后端已重启。" : "已保存。" : "" });
      if (restarting && result?.ok) {
        const refreshVersion = sequence + 1;
        const read = await controller.refresh();
        if (!current(refreshVersion, target)) return stale();
        if (!read?.ok || read.settings.some(row => row.pendingRestart)) {
          result = { ok: false, status: "execution_unknown", reason: "settings_apply_not_confirmed" };
          publish({ error: result.reason, notice: "后端已重启，设置生效状态尚未确认。" });
        } else publish({ notice: "后端已重启，设置已生效。" });
      }
      return result;
    },
    stop() { source = null; key = ""; sequence++; }
  };
  return controller;
}
