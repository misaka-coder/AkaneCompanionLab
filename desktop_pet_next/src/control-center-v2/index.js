import { createControlCenterBridge } from "./bridge.js";
import { renderControlCenterShell } from "./components/shell.js";
import { createInitialControlCenterState, createControlCenterStore } from "./store.js";
import { normalizeActionPresentation } from "./view-model.js";
import {
  applyModelProvider,
  createModelServiceDraft,
  modelServicePayload,
  MODEL_SERVICE_ACTIONS
} from "./model-service.js";
import {
  loadPresentationPreferences,
  normalizePresentationPreferences,
  presentationCssVariables,
  resetPresentationFrame,
  resolveThemeMode,
  savePresentationPreferences,
  updatePresentationFrame
} from "./presentation-preferences.js";
import "./styles.css";

const root = document.querySelector("#app");
const store = createControlCenterStore(createInitialControlCenterState({ activePage: initialPageFromLocation() }));
const bridge = createControlCenterBridge();
let chatDraft = "";
let chatScrollTop = 0;
let chatWasAtBottom = true;
let lastChatMessageId = "";
let voicePreviewDraft = "";
let wakeWordDraft = "";
let livePresentationPreferences = null;
let framingDrag = null;
let renderedPage = "";
const pageScrollTop = new Map();
const systemThemeQuery = window.matchMedia?.("(prefers-color-scheme: light)") || null;

store.subscribe((state) => render(state));
bridge.subscribe((viewModel) => {
  store.patch((state) => {
    const packId = String(viewModel?.character?.packId || "default").trim() || "default";
    const packChanged = state.presentationPackId !== packId;
    const firstModelRead = !state.modelDraft && viewModel?.model?.available;
    if (packChanged) livePresentationPreferences = null;
    return {
      ...state,
      phase: "ready",
      error: "",
      viewModel,
      refreshedAt: Date.now(),
      presentationPackId: packId,
      presentationPreferences: packChanged
        ? loadPresentationPreferences(packId)
        : state.presentationPreferences,
      modelDraft: firstModelRead ? createModelServiceDraft(viewModel.model) : state.modelDraft
    };
  });
});

root.addEventListener("click", (event) => {
  const approvalButton = event.target.closest("button[data-approval-mode]");
  if (approvalButton && !approvalButton.disabled) {
    void runAction("abilities.approvalPolicy.save", { defaultMode: approvalButton.dataset.approvalMode });
    return;
  }
  const modelToggle = event.target.closest("button[data-model-toggle]");
  if (modelToggle && !modelToggle.disabled) {
    const draft = readModelServiceForm(store.getState().modelDraft);
    const field = modelToggle.dataset.modelToggle;
    store.patch({ modelDraft: { ...draft, [field]: !Boolean(draft[field]) } });
    return;
  }
  const modelActionButton = event.target.closest("button[data-action^='model.']");
  if (modelActionButton && !modelActionButton.disabled) {
    const actionId = modelActionButton.dataset.action;
    void runModelAction(actionId);
    return;
  }
  const themeButton = event.target.closest("button[data-theme-mode]");
  if (themeButton) {
    updatePresentationPreferences({
      ...store.getState().presentationPreferences,
      themeMode: themeButton.dataset.themeMode
    });
    return;
  }
  const accentButton = event.target.closest("button[data-accent-preset]");
  if (accentButton) {
    updatePresentationPreferences({
      ...store.getState().presentationPreferences,
      accentPreset: accentButton.dataset.accentPreset
    });
    return;
  }
  const fontButton = event.target.closest("button[data-font-preset]");
  if (fontButton) {
    updatePresentationPreferences({
      ...store.getState().presentationPreferences,
      fontPreset: fontButton.dataset.fontPreset
    });
    return;
  }
  const presentationToggle = event.target.closest("button[data-presentation-toggle]");
  if (presentationToggle) {
    const field = presentationToggle.dataset.presentationToggle;
    if (field === "reducedMotion") {
      const preferences = store.getState().presentationPreferences;
      updatePresentationPreferences({ ...preferences, reducedMotion: !preferences.reducedMotion });
    }
    return;
  }
  const targetButton = event.target.closest("[data-framing-target]");
  if (targetButton) {
    store.patch({ framingTarget: targetButton.dataset.framingTarget || "portrait" });
    return;
  }
  const resetButton = event.target.closest("[data-framing-reset]");
  if (resetButton) {
    const state = store.getState();
    updatePresentationPreferences(resetPresentationFrame(state.presentationPreferences, state.framingTarget));
    return;
  }
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
  const actionId = actionButton.dataset.action;
  if (actionId === "voice.previewPlay") {
    const previewInput = root.querySelector("[data-voice-preview-input]");
    const text = String(previewInput?.value || voicePreviewDraft).trim();
    void runAction(actionId, text ? { text } : {});
    return;
  }
  const value = actionValueFromButton(actionButton);
  void runAction(actionId, value === undefined ? {} : { value });
});

root.addEventListener("change", (event) => {
  const botSelect = event.target.closest("[data-bound-bot-select]");
  if (botSelect && !botSelect.disabled) {
    void runAction("settings.selectBot", { value: botSelect.value, botId: botSelect.value });
  }
});

root.addEventListener("input", (event) => {
  if (event.target.matches("[data-chat-input]")) chatDraft = event.target.value;
  if (event.target.matches("[data-voice-preview-input]")) voicePreviewDraft = event.target.value;
  if (event.target.matches("[data-wake-word-input]")) wakeWordDraft = event.target.value;
  if (event.target.matches("[data-voice-range]")) {
    const field = event.target.dataset.voiceRange;
    const value = Number(event.target.value);
    const label = root.querySelector(`[data-voice-range-value="${field}"]`);
    if (label) label.textContent = field === "volume" ? `${Math.round(value)}%` : String(value);
  }
  if (event.target.matches("[data-presentation-range]")) {
    const field = event.target.dataset.presentationRange;
    const value = Number(event.target.value);
    previewPresentationPreferences({ [field]: value });
    const label = root.querySelector(`[data-presentation-value="${field}"]`);
    if (label) label.textContent = `${Math.round(value)}${event.target.dataset.presentationUnit || ""}`;
  }
  if (event.target.matches("[data-framing-scale]")) {
    const scale = Number(event.target.value) / 100;
    previewPresentationFrame({ scale });
    const label = root.querySelector("[data-framing-scale-label]");
    if (label) label.textContent = `${Math.round(scale * 100)}%`;
  }
});

root.addEventListener("change", (event) => {
  if (event.target.matches('[data-model-field="providerId"]')) {
    const state = store.getState();
    const draft = readModelServiceForm(state.modelDraft);
    store.patch({
      modelDraft: applyModelProvider(state.viewModel?.model, draft, event.target.value),
      modelModels: []
    });
    return;
  }
  if (event.target.matches('[data-voice-range="volume"]')) {
    void runAction("voice.setVolume", { value: Number(event.target.value) / 100 });
  }
  if (event.target.matches("[data-presentation-range]") && livePresentationPreferences) {
    updatePresentationPreferences(livePresentationPreferences);
  }
  if (event.target.matches("[data-framing-scale]") && livePresentationPreferences) {
    updatePresentationPreferences(livePresentationPreferences);
  }
});

root.addEventListener("keydown", (event) => {
  const framingStage = event.target.closest?.("[data-framing-stage]");
  if (framingStage && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
    event.preventDefault();
    const state = store.getState();
    const frame = state.presentationPreferences.frames?.[state.framingTarget] || { x: 50, y: 50 };
    const step = event.shiftKey ? 5 : 2;
    previewPresentationFrame({
      x: frame.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0),
      y: frame.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0)
    });
    updatePresentationPreferences(livePresentationPreferences);
    return;
  }
  if (!event.target.matches("[data-chat-input]") || event.isComposing) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.target.closest("form")?.requestSubmit();
  }
});

root.addEventListener("submit", (event) => {
  const wakeWordForm = event.target.closest("[data-wake-word-form]");
  if (wakeWordForm) {
    event.preventDefault();
    const input = wakeWordForm.querySelector("[data-wake-word-input]");
    const value = String(input?.value || wakeWordDraft).trim();
    if (!value) return;
    wakeWordDraft = value;
    void runAction("voice.setWakeWord", { value }).then((result) => {
      if (result?.ok) wakeWordDraft = "";
    });
    return;
  }
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

root.addEventListener("pointerdown", (event) => {
  const stage = event.target.closest("[data-framing-stage]");
  if (!stage || event.button !== 0) return;
  event.preventDefault();
  framingDrag = { pointerId: event.pointerId };
  stage.setPointerCapture?.(event.pointerId);
  updateFramingPositionFromPointer(event, stage);
});

root.addEventListener("pointermove", (event) => {
  if (!framingDrag || framingDrag.pointerId !== event.pointerId) return;
  const stage = root.querySelector("[data-framing-stage]");
  if (stage) updateFramingPositionFromPointer(event, stage);
});

root.addEventListener("pointerup", (event) => {
  if (!framingDrag || framingDrag.pointerId !== event.pointerId) return;
  framingDrag = null;
  if (livePresentationPreferences) updatePresentationPreferences(livePresentationPreferences);
});

root.addEventListener("pointercancel", (event) => {
  if (!framingDrag || framingDrag.pointerId !== event.pointerId) return;
  framingDrag = null;
  livePresentationPreferences = null;
  applyPresentationPreferences(store.getState().presentationPreferences);
});

systemThemeQuery?.addEventListener?.("change", () => applyPresentationPreferences(
  livePresentationPreferences || store.getState().presentationPreferences
));

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

async function runModelAction(actionId) {
  const state = store.getState();
  const draft = readModelServiceForm(state.modelDraft);
  store.patch({ modelDraft: draft });
  const result = await runAction(actionId, modelServicePayload(draft));
  if (actionId === MODEL_SERVICE_ACTIONS.models && result?.ok) {
    const models = Array.isArray(result.models) ? result.models.map((item) => String(item || "").trim()).filter(Boolean) : [];
    const nextDraft = { ...draft };
    if (!nextDraft.chatModel && models.length) nextDraft.chatModel = models[0];
    store.patch({ modelDraft: nextDraft, modelModels: models });
  } else if (actionId === MODEL_SERVICE_ACTIONS.save && result?.ok) {
    store.patch({
      modelDraft: createModelServiceDraft(store.getState().viewModel?.model || draft),
      modelModels: []
    });
  }
  return result;
}

function render(state) {
  const currentPageViewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
  if (currentPageViewport && renderedPage) pageScrollTop.set(renderedPage, currentPageViewport.scrollTop);
  const currentViewport = root.querySelector("[data-chat-viewport]");
  if (currentViewport) {
    chatScrollTop = currentViewport.scrollTop;
    chatWasAtBottom = isNearBottom(currentViewport);
  }
  const currentInput = root.querySelector("[data-chat-input]");
  if (currentInput) chatDraft = currentInput.value;
  const currentVoicePreview = root.querySelector("[data-voice-preview-input]");
  if (currentVoicePreview) voicePreviewDraft = currentVoicePreview.value;
  const currentWakeWord = root.querySelector("[data-wake-word-input]");
  if (currentWakeWord && currentWakeWord.value !== state.viewModel?.voice?.wakeWord) wakeWordDraft = currentWakeWord.value;

  const modelDraft = state.activePage === "model" && root.querySelector("[data-model-form]")
    ? readModelServiceForm(state.modelDraft)
    : state.modelDraft;

  renderControlCenterShell(root, modelDraft === state.modelDraft ? state : { ...state, modelDraft });
  const nextPageViewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
  if (nextPageViewport) {
    const desiredScrollTop = pageScrollTop.get(state.activePage) || 0;
    const maximumScrollTop = Math.max(0, nextPageViewport.scrollHeight - nextPageViewport.clientHeight);
    nextPageViewport.scrollTop = Math.min(desiredScrollTop, maximumScrollTop);
  }
  renderedPage = state.activePage;
  applyPresentationPreferences(livePresentationPreferences || state.presentationPreferences);

  const nextInput = root.querySelector("[data-chat-input]");
  if (nextInput) nextInput.value = chatDraft;
  const nextVoicePreview = root.querySelector("[data-voice-preview-input]");
  if (nextVoicePreview) nextVoicePreview.value = voicePreviewDraft;
  const nextWakeWord = root.querySelector("[data-wake-word-input]");
  if (nextWakeWord && wakeWordDraft) nextWakeWord.value = wakeWordDraft;
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

function previewPresentationFrame(patch) {
  const state = store.getState();
  livePresentationPreferences = updatePresentationFrame(
    livePresentationPreferences || state.presentationPreferences,
    state.framingTarget,
    patch
  );
  applyPresentationPreferences(livePresentationPreferences);
}

function previewPresentationPreferences(patch) {
  const state = store.getState();
  livePresentationPreferences = normalizePresentationPreferences({
    ...(livePresentationPreferences || state.presentationPreferences),
    ...patch
  });
  applyPresentationPreferences(livePresentationPreferences);
}

function updatePresentationPreferences(preferences) {
  const state = store.getState();
  const normalized = normalizePresentationPreferences(preferences);
  livePresentationPreferences = normalized;
  const saved = savePresentationPreferences(state.presentationPackId || "default", normalized);
  const notice = saved
    ? { phase: "confirmed", label: "外观设置已保存", detail: "仅应用于这台设备" }
    : { phase: "failed", label: "外观设置未保存", detail: "本地存储当前不可用" };
  store.patch({ presentationPreferences: normalized, presentationNotice: notice });
  window.setTimeout(() => {
    if (store.getState().presentationNotice === notice) store.patch({ presentationNotice: null });
  }, saved ? 1600 : 5000);
  livePresentationPreferences = null;
}

function applyPresentationPreferences(preferences) {
  const resolvedTheme = resolveThemeMode(preferences?.themeMode, Boolean(systemThemeQuery?.matches));
  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.dataset.themeMode = preferences?.themeMode || "system";
  document.documentElement.dataset.accent = preferences?.accentPreset || "violet";
  document.documentElement.dataset.font = preferences?.fontPreset || "system";
  document.documentElement.dataset.reducedMotion = preferences?.reducedMotion ? "true" : "false";
  const variables = presentationCssVariables(preferences);
  for (const [name, value] of Object.entries(variables)) {
    document.documentElement.style.setProperty(name, value);
  }
}

function updateFramingPositionFromPointer(event, stage) {
  const rect = stage.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const target = stage.dataset.target;
  const yMin = target === "portrait" ? 60 : 0;
  const yMax = target === "portrait" ? 140 : 100;
  previewPresentationFrame({
    x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
    y: yMin + Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) * (yMax - yMin)
  });
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

function actionValueFromButton(button) {
  if (!Object.hasOwn(button.dataset, "actionValue")) return undefined;
  const value = String(button.dataset.actionValue || "").trim();
  if (button.dataset.actionValueType === "boolean") return value === "true";
  if (button.dataset.actionValueType === "number") return Number(value);
  return value;
}

function readModelServiceForm(fallback = {}) {
  const read = (field) => root.querySelector(`[data-model-field="${field}"]`);
  return {
    ...createModelServiceDraft(fallback),
    ...fallback,
    providerId: String(read("providerId")?.value ?? fallback.providerId ?? "openai_compatible"),
    protocol: String(store.getState().viewModel?.model?.providers?.find((item) => item.id === String(read("providerId")?.value ?? fallback.providerId))?.protocol || fallback.protocol || "openai"),
    baseUrl: String(read("baseUrl")?.value ?? fallback.baseUrl ?? "").trim(),
    apiKey: String(read("apiKey")?.value ?? fallback.apiKey ?? "").trim(),
    chatModel: String(read("chatModel")?.value ?? fallback.chatModel ?? "").trim(),
    visionModel: String(read("visionModel")?.value ?? fallback.visionModel ?? "").trim(),
    visionBaseUrl: String(read("visionBaseUrl")?.value ?? fallback.visionBaseUrl ?? "").trim(),
    visionApiKey: String(read("visionApiKey")?.value ?? fallback.visionApiKey ?? "").trim(),
    visionApiProtocol: String(read("visionApiProtocol")?.value ?? fallback.visionApiProtocol ?? "openai"),
    timeoutSeconds: Number(read("timeoutSeconds")?.value ?? fallback.timeoutSeconds ?? 120)
  };
}

function initialPageFromLocation() {
  const page = new URLSearchParams(window.location.search).get("page") || "overview";
  return {
    character: "appearance",
    advanced: "system",
    diagnostics: "system",
    model: "model"
  }[page] || (["overview", "chat", "appearance", "voice", "abilities", "system", "model"].includes(page) ? page : "overview");
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
