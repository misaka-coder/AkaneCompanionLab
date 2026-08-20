import { createControlCenterBridge } from "./bridge.js";
import { renderControlCenterShell } from "./components/shell.js";
import { createInitialControlCenterState, createControlCenterStore } from "./store.js";
import { normalizeActionPresentation } from "./view-model.js";
import "./styles.css";

const root = document.querySelector("#app");
const store = createControlCenterStore(createInitialControlCenterState());
const bridge = createControlCenterBridge();

store.subscribe((state) => renderControlCenterShell(root, state));
bridge.subscribe((viewModel) => {
  store.patch({ phase: "ready", error: "", viewModel, refreshedAt: Date.now() });
});

root.addEventListener("click", (event) => {
  const refreshButton = event.target.closest("[data-refresh]");
  if (refreshButton) {
    void refresh();
    return;
  }
  const actionButton = event.target.closest("[data-action]");
  if (!actionButton || actionButton.disabled) return;
  void runAction(actionButton.dataset.action);
});

void bridge.start().catch((error) => {
  store.patch({ phase: "failed", error: friendlyError(error), viewModel: null });
});

window.addEventListener("beforeunload", () => bridge.stop(), { once: true });

async function refresh() {
  if (store.getState().phase === "refreshing") return;
  store.patch({ phase: "refreshing", error: "" });
  try {
    const viewModel = await bridge.refresh();
    store.patch({ phase: "ready", viewModel, refreshedAt: Date.now() });
  } catch (error) {
    store.patch((state) => ({
      ...state,
      phase: state.viewModel ? "ready" : "failed",
      error: friendlyError(error)
    }));
  }
}

async function runAction(actionId) {
  const current = store.getState().actionStates[actionId];
  if (current?.phase === "pressed" || current?.phase === "pending") return;
  updateAction(actionId, { phase: "pressed", label: "已按下", detail: "" });
  await new Promise((resolve) => requestAnimationFrame(resolve));
  updateAction(actionId, { phase: "pending", label: "处理中", detail: "" });
  let result;
  try {
    result = await bridge.runAction(actionId);
  } catch (error) {
    result = { ok: false, status: "failed", reason: friendlyError(error) };
  }
  const presentation = normalizeActionPresentation(result);
  updateAction(actionId, presentation);
  window.setTimeout(() => {
    const latest = store.getState().actionStates[actionId];
    if (latest === presentation || latest?.phase === presentation.phase) {
      updateAction(actionId, null);
    }
  }, presentation.phase === "confirmed" ? 1800 : 5000);
}

function updateAction(actionId, value) {
  store.patch((state) => {
    const actionStates = { ...state.actionStates };
    if (value) actionStates[actionId] = value;
    else delete actionStates[actionId];
    return { ...state, actionStates };
  });
}

function friendlyError(error) {
  const message = error instanceof Error ? error.message : String(error || "unknown");
  if (message.startsWith("tauri_control_center_bootstrap_failed:")) {
    return `桌宠运行时初始化失败：${message.slice("tauri_control_center_bootstrap_failed:".length)}`;
  }
  return {
    "instance-health-unavailable": "后端健康检查未通过，请确认桌宠和本地服务已经启动。",
    "snapshot_unavailable": "没有读取到控制中心快照，请稍后重试。",
    "all-backend-endpoints-failed": "后端已连接，但控制中心数据暂时不可用。"
  }[message] || message;
}
