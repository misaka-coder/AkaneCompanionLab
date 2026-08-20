import { createControlCenterBridge } from "./bridge.js";
import { renderControlCenterShell } from "./components/shell.js";
import { createInitialControlCenterState, createControlCenterStore } from "./store.js";
import { normalizeActionPresentation } from "./view-model.js";
import "./styles.css";

const root = document.querySelector("#app");
const store = createControlCenterStore(createInitialControlCenterState());
const bridge = createControlCenterBridge();
let chatDraft = "";
let chatScrollTop = 0;
let chatWasAtBottom = true;
let lastChatMessageId = "";

store.subscribe((state) => render(state));
bridge.subscribe((viewModel) => {
  store.patch({ phase: "ready", error: "", viewModel, refreshedAt: Date.now() });
});

root.addEventListener("click", (event) => {
  const jumpButton = event.target.closest("[data-chat-jump-latest]");
  if (jumpButton) {
    const viewport = root.querySelector("[data-chat-viewport]");
    if (viewport) viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    jumpButton.hidden = true;
    chatWasAtBottom = true;
    return;
  }
  const pageButton = event.target.closest("[data-page]");
  if (pageButton) {
    store.patch({ activePage: pageButton.dataset.page || "overview" });
    return;
  }
  const refreshButton = event.target.closest("[data-refresh]");
  if (refreshButton) {
    void refresh();
    return;
  }
  const actionButton = event.target.closest("[data-action]");
  if (!actionButton || actionButton.disabled) return;
  const value = String(actionButton.dataset.actionValue || "").trim();
  void runAction(actionButton.dataset.action, value ? { value } : {});
});

root.addEventListener("input", (event) => {
  if (event.target.matches("[data-chat-input]")) chatDraft = event.target.value;
});

root.addEventListener("keydown", (event) => {
  if (!event.target.matches("[data-chat-input]") || event.isComposing) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.target.closest("form")?.requestSubmit();
  }
});

root.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-chat-form]");
  if (!form) return;
  event.preventDefault();
  const input = form.querySelector("[data-chat-input]");
  const text = String(input?.value || chatDraft).trim();
  if (!text) return;
  chatDraft = text;
  void runAction("chat.send", { text }).then((result) => {
    if (result?.ok) chatDraft = "";
    const latestInput = root.querySelector("[data-chat-input]");
    if (latestInput) {
      latestInput.value = chatDraft;
      if (!result?.ok) latestInput.focus();
    }
  });
});

root.addEventListener("scroll", (event) => {
  if (!event.target.matches?.("[data-chat-viewport]")) return;
  const viewport = event.target;
  chatScrollTop = viewport.scrollTop;
  chatWasAtBottom = isNearBottom(viewport);
  const jumpButton = root.querySelector("[data-chat-jump-latest]");
  if (jumpButton && chatWasAtBottom) jumpButton.hidden = true;
}, true);

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

async function runAction(actionId, payload = {}) {
  const current = store.getState().actionStates[actionId];
  if (current?.phase === "pressed" || current?.phase === "pending") return null;
  updateAction(actionId, { phase: "pressed", label: "已按下", detail: "" });
  await new Promise((resolve) => requestAnimationFrame(resolve));
  updateAction(actionId, { phase: "pending", label: "处理中", detail: "" });
  let result;
  try {
    result = await bridge.runAction(actionId, payload);
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
  return result;
}

function render(state) {
  const currentViewport = root.querySelector("[data-chat-viewport]");
  if (currentViewport) {
    chatScrollTop = currentViewport.scrollTop;
    chatWasAtBottom = isNearBottom(currentViewport);
  }
  const currentInput = root.querySelector("[data-chat-input]");
  if (currentInput) chatDraft = currentInput.value;

  renderControlCenterShell(root, state);

  const nextInput = root.querySelector("[data-chat-input]");
  if (nextInput) nextInput.value = chatDraft;
  const nextViewport = root.querySelector("[data-chat-viewport]");
  if (!nextViewport) return;
  const messages = state.viewModel?.chat?.messages || [];
  const nextLastId = messages.at(-1)?.id || "";
  const hasNewMessage = Boolean(lastChatMessageId && nextLastId && nextLastId !== lastChatMessageId);
  if (!lastChatMessageId || chatWasAtBottom) {
    nextViewport.scrollTop = nextViewport.scrollHeight;
    chatWasAtBottom = true;
  } else {
    nextViewport.scrollTop = chatScrollTop;
    const jumpButton = root.querySelector("[data-chat-jump-latest]");
    if (jumpButton) jumpButton.hidden = !hasNewMessage;
  }
  lastChatMessageId = nextLastId;
}

function isNearBottom(viewport) {
  return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 72;
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
