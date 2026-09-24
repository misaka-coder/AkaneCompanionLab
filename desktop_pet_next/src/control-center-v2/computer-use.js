// Device controls stay local: they do not depend on the model or backend queue.
export function createComputerUseController({ invoke, available, onChange, schedule = setTimeout, cancel = clearTimeout }) {
  let state = { available, input_enabled: false, status: available ? "connecting" : "unavailable", error: "", busy: false };
  let revision = 0;
  let timer = null;
  let disposed = false;
  async function readStatus() {
    const [desktop, chrome] = await Promise.all([invoke("get_computer_use_status"), invoke("get_personal_browser_status")]);
    if (desktop?.ok !== true) throw new Error(desktop?.reason || "local_executor_unavailable");
    if (chrome?.ok !== true) throw new Error(chrome?.reason || "local_executor_unavailable");
    return { ...desktop, preference_error: desktop.preference_error || chrome.preference_error || "", chrome_enabled: chrome?.enabled === true, chrome_connected: chrome?.connected === true, chrome_status: chrome?.status || "unavailable" };
  }
  function publish(patch) { if (!disposed) { state = { ...state, ...patch }; onChange?.(); } }
  async function run(action, payload = {}) {
    if (!available) return { ok: false, reason: "desktop_required" };
    if (state.busy && action !== "stop") return { ok: false, reason: "operation_in_progress" };
    const ticket = ++revision;
    publish({ busy: true, error: "" });
    const commands = { refresh: "get_computer_use_status", enable: "configure_computer_use",
      disable: "configure_computer_use", stop: "stop_computer_use", resume: "resume_computer_use",
      chrome_enable: "configure_personal_browser", chrome_disable: "configure_personal_browser",
      refresh_targets: "get_computer_use_targets", set_target: "configure_computer_use_target" };
    try {
      if (!commands[action]) throw new Error("unknown_computer_use_action");
      const parameters = ["enable", "disable"].includes(action) ? { inputEnabled: action === "enable" }
        : action.startsWith("chrome_") ? { enabled: action === "chrome_enable" }
        : action === "set_target" ? { targetId: String(payload.targetId || "") } : {};
      const result = await invoke(commands[action], parameters);
      if (result?.ok !== true) throw new Error(result?.reason || "desktop_control_failed");
      const fresh = await readStatus();
      if (ticket === revision) publish({ ...fresh, ...(Array.isArray(result.targets) ? { targets: result.targets } : {}), busy: false, error: "" });
      return result;
    } catch (error) {
      // A setting may have changed live even if saving it failed. Refresh the
      // executor's facts before displaying the failed-persistence explanation.
      let fresh = {};
      try { fresh = await readStatus(); } catch { /* keep the original failure */ }
      if (ticket === revision) publish({ ...fresh, busy: false, error: String(error?.message || error) });
      return { ok: false, reason: String(error?.message || error) };
    }
  }
  async function poll() {
    if (disposed || !available) return;
    const ticket = revision;
    if (!state.busy) {
      try {
        const fresh = await readStatus();
        if (ticket === revision && fresh?.ok === true) {
          const nextState = { ...state, ...fresh, error: "" };
          if (JSON.stringify(nextState) !== JSON.stringify(state)) {
            publish({ ...fresh, error: "" });
          }
        }
      } catch (error) {
        if (ticket === revision) {
          const nextPatch = {
            status: "unavailable",
            input_enabled: null,
            chrome_enabled: null,
            workflow: null,
            window_title: "",
            error: String(error?.message || error)
          };
          const nextState = { ...state, ...nextPatch };
          if (JSON.stringify(nextState) !== JSON.stringify(state)) {
            publish(nextPatch);
          }
        }
      }
    }
    const interval = (state.workflow || state.status === "running" || state.status === "stopping") ? 2000 : 10000;
    if (!disposed) timer = schedule(poll, interval);
  }
  return { snapshot: () => ({ ...state }), run, start: () => { void poll(); },
    stop: () => { disposed = true; revision += 1; if (timer !== null) cancel(timer); } };
}
