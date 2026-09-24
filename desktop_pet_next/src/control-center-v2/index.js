import { createControlCenterBridge } from "./bridge.js";
import { renderControlCenterShell } from "./components/shell.js";
import { createInitialControlCenterState, createControlCenterStore } from "./store.js";
import { normalizeActionPresentation } from "./view-model.js";
import { prependedHistoryScrollTop } from "./chat-history.js";
import { createChatAutoPreviews } from "./chat-auto-previews.js";
import { chatTimeline } from "./chat-timeline.js";
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
const chatAutoPreviews = createChatAutoPreviews({ root, open: item => bridge.previewChatImage(item) });
bridge.onFileDrop(paths => {
  if (store.getState().activePage === "chat") void runAction("chat.attach", { paths });
});
let chatDraft = "";
let chatScrollTop = 0;
let chatWasAtBottom = true;
let lastChatMessageId = "";
let renderedChatSessionId = "";
let renderedChatScope = "";
let chatPrependAnchor = null;
let voicePreviewDraft = "";
let wakeWordDraft = "";
let voiceProfilePreviewAudio = null;
let livePresentationPreferences = null;
let framingDrag = null;
let renderedPage = "";
let renderedPerceptionScope = "";
let renderedModelScope = "";
let deferredRenderState = null;
let setupExpanded = null;
let setupCoreComplete = null;
const pageScrollTop = new Map();
const openCapabilityPanels = new Set();
const capabilityFormDrafts = new Map();
const systemThemeQuery = window.matchMedia?.("(prefers-color-scheme: light)") || null;

store.subscribe((state) => render(state));
bridge.subscribe((viewModel) => {
  store.patch((state) => {
    const packId = String(viewModel?.character?.packId || "default").trim() || "default";
    const packChanged = state.presentationPackId !== packId;
    const firstModelRead = !state.modelDraft && viewModel?.model?.available;
    const previousChatSessionId = String(state.viewModel?.chat?.sessionId || "");
    const nextChatSessionId = String(viewModel?.chat?.sessionId || "");
    const previousChatScope = [previousChatSessionId, state.viewModel?.bots?.activeId || "", state.viewModel?.character?.packId || ""].join("|");
    const nextChatScope = [nextChatSessionId, viewModel?.bots?.activeId || "", viewModel?.character?.packId || ""].join("|");
    const chatSessionChanged = Boolean(previousChatSessionId && nextChatSessionId && previousChatSessionId !== nextChatSessionId);
    const chatScopeChanged = Boolean(previousChatScope && nextChatScope && previousChatScope !== nextChatScope);
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
      modelDraft: firstModelRead ? createModelServiceDraft(viewModel.model) : state.modelDraft,
      chatHistory: chatSessionChanged
        ? { phase: "idle", error: "", sessionId: nextChatSessionId }
        : state.chatHistory,
      chatJobControls: chatScopeChanged ? {} : state.chatJobControls
    };
  });
});

root.addEventListener("click", (event) => {
  const computerUseButton = event.target.closest("button[data-computer-use]");
  if (computerUseButton && !computerUseButton.disabled) {
    const targetId = root.querySelector("[data-computer-use-target]")?.value || "";
    void bridge.runAction(`computerUse.${computerUseButton.dataset.computerUse}`, { targetId });
    return;
  }
  const exposureButton = event.target.closest("button[data-exposure-mode], button[data-exposure-search], button[data-exposure-refresh]");
  if (exposureButton) {
    if (exposureButton.disabled) return;
    if (exposureButton.hasAttribute("data-exposure-refresh")) {
      void bridge.runAction("abilities.toolExposure.refresh");
    } else {
      const data = exposureButton.dataset;
      void bridge.runAction("abilities.toolExposure.save", {
        id: data.exposureId, source: data.exposureSource,
        mode: data.exposureMode,
        ...(data.exposureDefault ? { defaultMode: data.exposureMode } : {}),
        ...(data.exposureSearch !== undefined ? { searchEnabled: data.exposureSearch === "true" } : {})
      });
    }
    return;
  }
  const previewButton = event.target.closest("[data-chat-image-preview]");
  if (previewButton) {
    if (!previewButton.disabled) void bridge.previewChatImage({ handle: previewButton.dataset.previewHandle,
      itemType: previewButton.dataset.previewType }, previewButton.dataset.chatImagePreview);
    return;
  }
  const jobControlButton = event.target.closest("[data-chat-job-control]");
  if (jobControlButton && !jobControlButton.disabled) {
    void runAction("chat.jobControl", {
      jobId: jobControlButton.dataset.jobId,
      action: jobControlButton.dataset.chatJobControl
    });
    return;
  }
  const fileButton = event.target.closest("[data-chat-file-action]");
  if (fileButton) {
    const card = fileButton.closest("[data-file-handle]");
    if (!fileButton.disabled && card) void runAction("chat.fileAction", { action: fileButton.dataset.chatFileAction,
      handle: card.dataset.fileHandle, itemType: card.dataset.fileType, title: card.dataset.fileTitle, format: card.dataset.fileFormat });
    return;
  }
  const attachmentButton = event.target.closest("[data-chat-attachment-remove], [data-chat-attachment-play]");
  if (attachmentButton) {
    const remove = attachmentButton.hasAttribute("data-chat-attachment-remove");
    void runAction(remove ? "chat.removeAttachment" : "chat.playAttachment", {
      attachmentId: remove ? attachmentButton.dataset.chatAttachmentRemove : attachmentButton.dataset.chatAttachmentPlay,
    });
    return;
  }
  const olderMessagesButton = event.target.closest("button[data-chat-load-older]");
  if (olderMessagesButton && !olderMessagesButton.disabled) {
    void loadOlderChatHistory();
    return;
  }
  const voiceProfileEditButton = event.target.closest("button[data-voice-profile-edit]");
  if (voiceProfileEditButton && !voiceProfileEditButton.disabled) {
    editVoiceProfile(voiceProfileEditButton.dataset.providerId, voiceProfileEditButton.dataset.voiceProfileEdit);
    return;
  }
  const voiceProfileActionButton = event.target.closest("button[data-voice-profile-action]");
  if (voiceProfileActionButton && !voiceProfileActionButton.disabled) {
    const operation = voiceProfileActionButton.dataset.voiceProfileAction;
    const payload = {
      providerId: voiceProfileActionButton.dataset.providerId || "",
      voiceProfileId: voiceProfileActionButton.dataset.voiceProfileId || "",
      emotion: voiceProfileActionButton.dataset.emotion || "",
      characterPackId: store.getState().viewModel?.character?.packId || ""
    };
    if (operation === "test") void runVoiceProfileTest(payload);
    if (operation === "bind") void runAction("abilities.provider.voiceProfile.assignToCurrentCharacter", payload);
    if (operation === "clear") void runAction("abilities.provider.voiceProfile.clearCurrentCharacter", payload);
    return;
  }
  const marketStageButton = event.target.closest("button[data-plugin-market-stage]");
  if (marketStageButton && !marketStageButton.disabled) {
    const entry = store.getState().viewModel?.abilities?.plugins?.market?.entries?.find(
      (item) => item.pluginId === marketStageButton.dataset.pluginMarketStage
    );
    if (entry) void runAction("abilities.plugin.stageMarket", { pluginId: entry.pluginId, digest: entry.digest });
    return;
  }
  const pluginInstallButton = event.target.closest("button[data-plugin-stage-install]");
  if (pluginInstallButton && !pluginInstallButton.disabled) {
    const stageId = String(pluginInstallButton.dataset.pluginStageInstall || "").trim();
    const stage = store.getState().viewModel?.abilities?.plugins?.stages?.find((item) => item.stageId === stageId && item.ok);
    if (!stage) return;
    void runAction("abilities.plugin.install", {
      stageId,
      approvedPermissions: [...stage.permissions]
    });
    return;
  }
  const pluginPathPicker = event.target.closest("button[data-plugin-path-picker]");
  if (pluginPathPicker && !pluginPathPicker.disabled) {
    const actionId = String(pluginPathPicker.dataset.pluginPathPicker || "").trim();
    void runAction(actionId).then((result) => {
      const path = String(result?.path || "").trim();
      const input = root.querySelector('[data-capability-form="plugin-stage"] [name="pluginPath"]');
      if (result?.ok && path && input) {
        input.value = path;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      }
    });
    return;
  }
  const pluginDiscardButton = event.target.closest("button[data-plugin-stage-discard]");
  if (pluginDiscardButton && !pluginDiscardButton.disabled) {
    const stageId = String(pluginDiscardButton.dataset.pluginStageDiscard || "").trim();
    if (stageId) void runAction("abilities.plugin.discardStage", { stageId });
    return;
  }
  const pluginUninstallButton = event.target.closest("button[data-plugin-uninstall]");
  if (pluginUninstallButton && !pluginUninstallButton.disabled) {
    const pluginId = String(pluginUninstallButton.dataset.pluginUninstall || "").trim();
    if (pluginId) void runAction("abilities.plugin.uninstall", { pluginId });
    return;
  }
  const approvalRequestButton = event.target.closest("button[data-approval-request]");
  if (approvalRequestButton && !approvalRequestButton.disabled) {
    void runAction("abilities.approvalRequest.decide", {
      requestId: approvalRequestButton.dataset.approvalRequest,
      decision: approvalRequestButton.dataset.decision
    });
    return;
  }
  const approvalButton = event.target.closest("button[data-approval-mode]");
  if (approvalButton && !approvalButton.disabled) {
    void runAction("abilities.approvalPolicy.save", {
      familyId: approvalButton.dataset.approvalFamily,
      mode: approvalButton.dataset.approvalMode
    });
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
  if (actionButton.matches('button[type="submit"]') && actionButton.closest("form")) return;
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
  const perceptionChoice = event.target.closest("[data-perception-choice]");
  if (perceptionChoice && !perceptionChoice.disabled) {
    if (!perceptionChoice.reportValidity()) return;
    const value = perceptionChoice.dataset.valueType === "number" ? Number(perceptionChoice.value) : perceptionChoice.value;
    perceptionChoice.blur();
    void runAction(perceptionChoice.dataset.perceptionChoice, { value });
    return;
  }
  const botSelect = event.target.closest("[data-bound-bot-select]");
  if (botSelect && !botSelect.disabled) {
    void runAction("settings.selectBot", { value: botSelect.value, botId: botSelect.value });
  }
});

root.addEventListener("focusout", (event) => {
  if (event.target.matches("[data-perception-choice]")) {
    queueMicrotask(() => render(store.getState()));
  }
});

root.addEventListener("input", (event) => {
  if (event.target.matches("[data-chat-input]")) chatDraft = event.target.value;
  if (event.target.matches("[data-voice-preview-input]")) voicePreviewDraft = event.target.value;
  if (event.target.matches("[data-wake-word-input]")) wakeWordDraft = event.target.value;
  if (event.target.matches("[data-model-field]")) {
    const field = event.target.dataset.modelField;
    const currentDraft = store.getState().modelDraft;
    if (field && currentDraft) {
      currentDraft[field] = event.target.dataset.modelValueType === "number"
        ? (event.target.value === "" ? "" : Number(event.target.value))
        : event.target.value;
    }
  }
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
  if (event.target.matches("[data-model-field]")) {
    const field = event.target.dataset.modelField;
    const currentDraft = store.getState().modelDraft;
    if (field && currentDraft) {
      currentDraft[field] = event.target.dataset.modelValueType === "number"
        ? (event.target.value === "" ? "" : Number(event.target.value))
        : event.target.value;
    }
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

root.addEventListener("focusout", () => {
  window.setTimeout(() => {
    const active = document.activeElement;
    const stillInteracting = active && (
      active.matches("select, input[list], [data-model-field], [data-perception-choice]") ||
      active.matches("input:not([type='button']):not([type='submit']):not([type='checkbox']):not([type='radio']), textarea")
    );
    if (!stillInteracting && deferredRenderState) {
      const next = deferredRenderState;
      deferredRenderState = null;
      render(next);
    }
  }, 60);
});

root.addEventListener("submit", (event) => {
  const capabilityForm = event.target.closest("[data-capability-form]");
  if (capabilityForm) {
    event.preventDefault();
    const actionId = event.submitter?.dataset.action;
    if (!actionId || event.submitter?.disabled) return;
    const payload = capabilityFormPayload(capabilityForm, actionId);
    if (!payload) return;
    void runCapabilityFormAction(actionId, payload);
    return;
  }
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
  if (!text && !(store.getState().viewModel?.chat?.pendingAttachments || []).length) return;
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

root.addEventListener("paste", event => {
  if (!event.target.closest("[data-chat-input]")) return;
  const files = Array.from(event.clipboardData?.files || []);
  if (files.length) { event.preventDefault(); void runAction("chat.attach", { browserFiles: files }); }
});
root.addEventListener("error", event => {
  if (event.target.matches?.("img[data-preview-handle]")) void bridge.previewChatImage({
    handle: event.target.dataset.previewHandle, itemType: event.target.dataset.previewType }, "failed");
}, true);
root.addEventListener("dragover", event => {
  if (event.target.closest(".chat-workspace")) event.preventDefault();
});
root.addEventListener("drop", event => {
  if (!event.target.closest(".chat-workspace")) return;
  event.preventDefault();
  const files = Array.from(event.dataTransfer?.files || []);
  if (files.length) void runAction("chat.attach", { browserFiles: files });
});

function capabilityFormPayload(form, actionId) {
  const data = new FormData(form);
  const enabled = data.get("enabled") === "on";
  if (form.dataset.capabilityForm === "provider") {
    return {
      providerId: form.dataset.providerId || "",
      enabled,
      endpoint: String(data.get("endpoint") || "").trim()
    };
  }
  if (form.dataset.capabilityForm === "mcp") {
    const command = String(data.get("command") || "").trim();
    if (actionId === "abilities.mcp.config.save" && !command) {
      const commandInput = form.elements.namedItem("command");
      commandInput?.setCustomValidity("替换 MCP 配置前，请填写完整启动命令。");
      commandInput?.reportValidity();
      commandInput?.addEventListener("input", () => commandInput.setCustomValidity(""), { once: true });
      return null;
    }
    return {
      serverId: form.dataset.serverId || "",
      enabled,
      command,
      displayName: String(data.get("displayName") || "").trim(),
      cwd: String(data.get("cwd") || "").trim(),
      args: String(data.get("args") || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
    };
  }
  if (form.dataset.capabilityForm === "workflow") {
    return {
      workflowId: form.dataset.workflowId || "",
      enabled,
      workflowPath: String(data.get("workflowPath") || "").trim(),
      inputImageSlot: String(data.get("inputImageSlot") || "").trim(),
      outputImageSlot: String(data.get("outputImageSlot") || "").trim()
    };
  }
  if (form.dataset.capabilityForm === "plugin-connection") {
    const values = {};
    const clearFields = new Set(Array.from(form.querySelectorAll("[data-connection-clear]:checked"))
      .map((element) => String(element.dataset.connectionClear || "").trim()).filter(Boolean));
    for (const field of form.querySelectorAll("[data-connection-field]")) {
      const key = String(field.dataset.connectionField || "").trim();
      if (!key || clearFields.has(key)) continue;
      const type = String(field.dataset.connectionType || "string");
      if (type === "boolean") {
        values[key] = Boolean(field.checked);
        continue;
      }
      const raw = String(field.value || "").trim();
      if (!raw) continue;
      if (type === "number" || type === "integer") {
        const number = Number(raw);
        if (!Number.isFinite(number)) {
          field.setCustomValidity("请输入有效数字。");
          field.reportValidity();
          field.addEventListener("input", () => field.setCustomValidity(""), { once: true });
          return null;
        }
        values[key] = type === "integer" ? Math.trunc(number) : number;
      } else {
        values[key] = raw;
      }
    }
    return {
      pluginId: form.dataset.pluginId || "",
      connectionName: form.dataset.connectionName || "",
      expectedRevision: Number(form.dataset.connectionRevision || 0),
      enabled,
      values,
      clearFields: [...clearFields]
    };
  }
  if (form.dataset.capabilityForm === "plugin-stage") {
    const path = String(data.get("pluginPath") || "").trim();
    if (!path) {
      const input = form.elements.namedItem("pluginPath");
      input?.setCustomValidity("请填写当前宿主可以读取的插件路径。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (actionId === "abilities.plugin.stageWheel" && !path.toLowerCase().endsWith(".whl")) {
      const input = form.elements.namedItem("pluginPath");
      input?.setCustomValidity("wheel 文件路径需要以 .whl 结尾。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    return { path };
  }
  if (form.dataset.capabilityForm === "voice-profile") {
    const providerId = form.dataset.providerId || "";
    if (actionId === "abilities.provider.voiceProfile.inspectFolder") {
      const folderPath = String(data.get("folderPath") || "").trim();
      if (!folderPath) {
        const input = form.elements.namedItem("folderPath");
        input?.setCustomValidity("请先填写 GPT-SoVITS 模型目录。");
        input?.reportValidity();
        input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
        return null;
      }
      return { providerId, folderPath };
    }
    const emotionVoiceMap = collectEmotionVoiceMap(form, data);
    if (emotionVoiceMap === null) return null;
    return {
      providerId,
      voiceProfileId: String(data.get("voiceProfileId") || "").trim(),
      displayName: String(data.get("displayName") || "").trim(),
      voiceProfileEnabled: data.get("voiceProfileEnabled") === "on",
      textLang: String(data.get("textLang") || "").trim(),
      promptLang: String(data.get("promptLang") || "").trim(),
      mediaType: String(data.get("mediaType") || "").trim(),
      refAudioPath: String(data.get("refAudioPath") || "").trim(),
      promptText: String(data.get("promptText") || "").trim(),
      parallelInfer: String(data.get("parallelInfer") || "").trim(),
      splitBucket: String(data.get("splitBucket") || "").trim(),
      batchSize: String(data.get("batchSize") || "").trim(),
      topK: String(data.get("topK") || "").trim(),
      topP: String(data.get("topP") || "").trim(),
      temperature: String(data.get("temperature") || "").trim(),
      speedFactor: String(data.get("speedFactor") || "").trim(),
      fragmentInterval: String(data.get("fragmentInterval") || "").trim(),
      textSplitMethod: String(data.get("textSplitMethod") || "").trim(),
      emotionVoiceMap
    };
  }
  return null;
}

function collectEmotionVoiceMap(form, data) {
  const result = {};
  for (const row of form.querySelectorAll("[data-emotion-sample-row]")) {
    const index = String(row.dataset.emotionSampleRow || "").trim();
    if (!index || data.get(`emotionRemove.${index}`) === "on") continue;
    const emotionId = String(data.get(`emotionId.${index}`) || "").trim();
    const aliases = String(data.get(`emotionAliases.${index}`) || "").split(/[,，;；\s]+/).map((item) => item.trim()).filter(Boolean);
    const refAudioPath = String(data.get(`emotionRefAudioPath.${index}`) || "").trim();
    const promptText = String(data.get(`emotionPromptText.${index}`) || "").trim();
    const configured = row.dataset.emotionConfigured === "true";
    if (!emotionId && !aliases.length && !refAudioPath && !promptText) continue;
    if (!emotionId) {
      const input = row.querySelector('[name^="emotionId."]');
      input?.setCustomValidity("请填写情绪标识。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (!configured && Boolean(refAudioPath) !== Boolean(promptText)) {
      const fieldName = refAudioPath ? "emotionPromptText" : "emotionRefAudioPath";
      const input = row.querySelector(`[name^="${fieldName}."]`);
      input?.setCustomValidity("参考音频和对应文本必须成对填写。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    if (Object.hasOwn(result, emotionId)) {
      const input = row.querySelector('[name^="emotionId."]');
      input?.setCustomValidity("情绪标识不能重复；请改用匹配别名。");
      input?.reportValidity();
      input?.addEventListener("input", () => input.setCustomValidity(""), { once: true });
      return null;
    }
    result[emotionId] = {
      aliases,
      ...(refAudioPath ? { refAudioPath } : {}),
      ...(promptText ? { promptText } : {})
    };
  }
  return result;
}

async function runCapabilityFormAction(actionId, payload) {
  const result = await runAction(actionId, payload);
  if (actionId === "abilities.plugin.connection.save" && result?.ok) {
    capabilityFormDrafts.delete(`plugin-connection:${payload.pluginId}:${payload.connectionName}`);
  }
  if (actionId === "abilities.provider.voiceProfile.inspectFolder" && result?.ok && result.suggestedProfile) {
    capabilityFormDrafts.delete("voice-profile:editor");
    openCapabilityPanels.add("voice-profile:editor");
    store.patch({
      voiceProfileSuggestion: {
        ...result.suggestedProfile,
        providerId: payload.providerId,
        folderPath: payload.folderPath
      },
      voiceProfileInspection: {
        detected: result.detected || {},
        warnings: Array.isArray(result.warnings) ? result.warnings : []
      },
      voiceProfileEditorOpen: true
    });
  }
  return result;
}

function editVoiceProfile(providerId, voiceProfileId) {
  const state = store.getState();
  const provider = state.viewModel?.abilities?.providers?.find((item) => item.id === providerId);
  const profile = provider?.voiceProfiles?.find((item) => item.voiceProfileId === voiceProfileId);
  if (!profile) return;
  capabilityFormDrafts.delete("voice-profile:editor");
  openCapabilityPanels.add("voice-profile:editor");
  store.patch({
    voiceProfileSuggestion: {
      providerId,
      voiceProfileId: profile.voiceProfileId,
      displayName: profile.name,
      enabled: profile.enabled,
      textLang: profile.textLang,
      promptLang: profile.promptLang,
      mediaType: profile.mediaType,
      parallelInfer: profile.parallelInfer,
      splitBucket: profile.splitBucket,
      batchSize: profile.batchSize,
      topK: profile.topK,
      topP: profile.topP,
      temperature: profile.temperature,
      speedFactor: profile.speedFactor,
      fragmentInterval: profile.fragmentInterval,
      textSplitMethod: profile.textSplitMethod,
      emotionSamples: profile.emotionSamples
    },
    voiceProfileInspection: null,
    voiceProfileEditorOpen: true
  });
  requestAnimationFrame(() => root.querySelector(".voice-profile-editor")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
}

async function runVoiceProfileTest(payload) {
  const text = String(root.querySelector("[data-voice-preview-input]")?.value || voicePreviewDraft || "你好呀，今天也请多关照。").trim();
  const actionId = "abilities.provider.ttsTest";
  const result = await runAction(actionId, { ...payload, text });
  if (!result?.ok || !result.audioBase64) return result;
  try {
    await playVoiceProfileAudio(result.audioBase64, result.mediaType);
    updateAction(actionId, { phase: "confirmed", label: "正在试听", detail: "服务已返回真实音频" });
  } catch (error) {
    updateAction(actionId, { phase: "failed", label: "音频未能播放", detail: friendlyError(error) });
  }
  return result;
}

async function playVoiceProfileAudio(base64, mediaType) {
  voiceProfilePreviewAudio?.pause?.();
  if (voiceProfilePreviewAudio?.dataset?.objectUrl) URL.revokeObjectURL(voiceProfilePreviewAudio.dataset.objectUrl);
  const binary = atob(String(base64));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const mime = String(mediaType || "audio/wav").includes("/") ? String(mediaType) : `audio/${String(mediaType || "wav")}`;
  const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const audio = new Audio(objectUrl);
  audio.dataset.objectUrl = objectUrl;
  voiceProfilePreviewAudio = audio;
  audio.addEventListener("ended", () => {
    URL.revokeObjectURL(objectUrl);
    if (voiceProfilePreviewAudio === audio) voiceProfilePreviewAudio = null;
  }, { once: true });
  try {
    await audio.play();
  } catch (error) {
    URL.revokeObjectURL(objectUrl);
    if (voiceProfilePreviewAudio === audio) voiceProfilePreviewAudio = null;
    throw error;
  }
}

root.addEventListener("scroll", (event) => {
  if (event.target.matches?.(".chat-attachments")) chatAutoPreviews.refresh();
  if (!event.target.matches?.("[data-chat-viewport]")) return;
  const viewport = event.target;
  chatScrollTop = viewport.scrollTop;
  chatWasAtBottom = isNearBottom(viewport);
  const jumpButton = root.querySelector("[data-chat-jump-latest]");
  if (jumpButton && chatWasAtBottom) jumpButton.hidden = true;
  if (viewport.scrollTop <= 72) void loadOlderChatHistory(viewport);
  chatAutoPreviews.refresh();
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
  // Keep the explicitly disconnected snapshot: local QQ recovery must work while the backend is down.
  store.patch({ phase: "failed", error: friendlyError(error) });
});

window.addEventListener("beforeunload", () => {
  chatAutoPreviews.dispose();
  bridge.stop();
  voiceProfilePreviewAudio?.pause?.();
  if (voiceProfilePreviewAudio?.dataset?.objectUrl) URL.revokeObjectURL(voiceProfilePreviewAudio.dataset.objectUrl);
}, { once: true });

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
  const actionMeta = actionId === "chat.jobControl"
    ? { jobId: String(payload.jobId || payload.job_id || "").trim() }
    : {};
  updateAction(actionId, { phase: "pressed", label: "已按下", detail: "", ...actionMeta });
  await new Promise((resolve) => requestAnimationFrame(resolve));
  updateAction(actionId, { phase: "pending", label: "处理中", detail: "", ...actionMeta });
  let result;
  try {
    result = await bridge.runAction(actionId, payload);
  } catch (error) {
    result = { ok: false, status: "failed", reason: friendlyError(error) };
  }
  if (actionId === "chat.jobControl") rememberChatJobControl(payload, result);
  const presentation = { ...normalizeActionPresentation(result), ...actionMeta };
  updateAction(actionId, presentation);
  window.setTimeout(() => {
    const latest = store.getState().actionStates[actionId];
    if (latest === presentation || latest?.phase === presentation.phase) {
      updateAction(actionId, null);
    }
  }, presentation.phase === "confirmed" ? 1800 : 5000);
  return result;
}

async function loadOlderChatHistory(viewport = root.querySelector("[data-chat-viewport]")) {
  const state = store.getState();
  const chat = state.viewModel?.chat;
  if (!chat?.history?.hasMore || state.chatHistory?.phase === "loading") return null;
  const beforeSeq = Number(chat.history.nextBeforeSeq);
  if (!Number.isFinite(beforeSeq) || beforeSeq <= 0) return null;
  const sessionId = String(chat.sessionId || "");
  const firstMessageId = String(chat.messages?.[0]?.id || "");
  chatPrependAnchor = viewport
    ? {
        sessionId,
        firstMessageId,
        scrollHeight: viewport.scrollHeight,
        scrollTop: viewport.scrollTop
      }
    : null;
  store.patch({ chatHistory: { phase: "loading", error: "", sessionId } });
  let result;
  try {
    result = await bridge.loadOlderChatMessages({ beforeSeq, limit: 60 });
  } catch (error) {
    result = { ok: false, status: "failed", reason: friendlyError(error) };
  }
  const latestSessionId = String(store.getState().viewModel?.chat?.sessionId || "");
  if (latestSessionId !== sessionId || result?.status === "stale") {
    chatPrependAnchor = null;
    return result;
  }
  if (result?.ok) {
    store.patch({ chatHistory: { phase: "idle", error: "", sessionId } });
  } else {
    chatPrependAnchor = null;
    store.patch({
      chatHistory: {
        phase: "failed",
        error: result?.status === "no-more-history" ? "已经没有更早消息" : "更早消息暂时没有加载出来",
        sessionId
      }
    });
  }
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

function perceptionRenderScope(state) {
  const vm = state.viewModel;
  return JSON.stringify([state.activePage, vm?.shell?.instanceLabel, vm?.bots?.activeId,
    vm?.character?.packId, vm?.chat?.sessionId, vm?.perception?.available, vm?.shell?.connected]);
}

function modelRenderScope(state) {
  const model = state.viewModel?.model;
  return JSON.stringify([
    state.activePage,
    model?.connected,
    model?.available,
    model?.configured,
    model?.providerId,
    model?.chatModel,
    state.modelDraft?.providerId,
    state.modelDraft?.useForVision,
    state.modelModels || [],
    state.actionStates?.[MODEL_SERVICE_ACTIONS.models],
    state.actionStates?.[MODEL_SERVICE_ACTIONS.test],
    state.actionStates?.[MODEL_SERVICE_ACTIONS.save]
  ]);
}

function render(state) {
  const pageChanged = renderedPage !== state.activePage;
  const activeElement = document.activeElement;
  const isInteractingWithForm = activeElement && (
    activeElement.matches("select, input[list], [data-model-field], [data-perception-choice]") ||
    activeElement.matches("input:not([type='button']):not([type='submit']):not([type='checkbox']):not([type='radio']), textarea")
  );

  // If the user is actively interacting with form controls or dropdowns on the current page,
  // do not let a background live snapshot destroy DOM nodes, close dropdown menus, or drop focus/IME.
  if (!pageChanged && isInteractingWithForm) {
    const isChatInput = activeElement.matches("[data-chat-input]");
    const messages = state.viewModel?.chat?.messages || [];
    const nextLastId = chatTimeline(state.viewModel?.chat).at(-1)?.id || "";
    const hasNewMessage = Boolean(lastChatMessageId && nextLastId && nextLastId !== lastChatMessageId);
    if (!isChatInput || !hasNewMessage) {
      deferredRenderState = state;
      return;
    }
  }

  // On the model page, do not re-render if nothing relevant to the model page has changed.
  if (!pageChanged && state.activePage === "model") {
    const nextModelScope = modelRenderScope(state);
    if (nextModelScope === renderedModelScope) {
      return;
    }
  }

  const nextPerceptionScope = perceptionRenderScope(state);
  // A live snapshot must not destroy a native select while its menu is open.
  // Changes of scope/availability still replace the old controls immediately.
  if (!pageChanged && nextPerceptionScope === renderedPerceptionScope && document.activeElement?.matches("[data-perception-choice]")) {
    deferredRenderState = state;
    return;
  }
  renderedPerceptionScope = nextPerceptionScope;
  const modelFieldFocus = captureFocusedModelField();
  captureCapabilityUiState();
  const currentSetupCenter = root.querySelector(".setup-center");
  if (currentSetupCenter) setupExpanded = currentSetupCenter.open;
  const nextSetupCoreComplete = Boolean(state.viewModel?.setup?.coreComplete);
  if (setupCoreComplete === false && nextSetupCoreComplete) setupExpanded = false;
  setupCoreComplete = state.viewModel?.setup ? nextSetupCoreComplete : null;
  const currentPageViewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
  if (currentPageViewport && renderedPage) pageScrollTop.set(renderedPage, currentPageViewport.scrollTop);
  const nextChatSessionId = String(state.viewModel?.chat?.sessionId || "");
  const nextChatScope = JSON.stringify([state.viewModel?.shell?.instanceLabel, state.viewModel?.bots?.activeId,
    state.viewModel?.character?.packId, nextChatSessionId]);
  const chatSessionChanged = Boolean(renderedChatScope && nextChatScope !== renderedChatScope);
  const currentViewport = root.querySelector("[data-chat-viewport]");
  if (currentViewport && !chatSessionChanged) {
    chatScrollTop = currentViewport.scrollTop;
    chatWasAtBottom = isNearBottom(currentViewport);
  }
  if (chatSessionChanged) {
    chatScrollTop = 0;
    chatWasAtBottom = true;
    lastChatMessageId = "";
    chatPrependAnchor = null;
    chatDraft = "";
  }
  const currentInput = root.querySelector("[data-chat-input]");
  const inputSelection = !chatSessionChanged && currentInput && document.activeElement === currentInput
    ? { start: currentInput.selectionStart, end: currentInput.selectionEnd } : null;
  if (currentInput && !chatSessionChanged) chatDraft = currentInput.value;
  const currentVoicePreview = root.querySelector("[data-voice-preview-input]");
  if (currentVoicePreview) voicePreviewDraft = currentVoicePreview.value;
  const currentWakeWord = root.querySelector("[data-wake-word-input]");
  if (currentWakeWord && currentWakeWord.value !== state.viewModel?.voice?.wakeWord) wakeWordDraft = currentWakeWord.value;

  const modelDraft = state.activePage === "model" && root.querySelector("[data-model-form]")
    ? readModelServiceForm(state.modelDraft)
    : state.modelDraft;

  const renderState = modelDraft === state.modelDraft ? state : { ...state, modelDraft };
  renderControlCenterShell(root, { ...renderState, setupExpanded });
  restoreCapabilityUiState();
  const nextPageViewport = root.querySelector(".ccv2-scroll:not(.is-chat)");
  if (nextPageViewport) {
    const desiredScrollTop = pageScrollTop.get(state.activePage) || 0;
    const maximumScrollTop = Math.max(0, nextPageViewport.scrollHeight - nextPageViewport.clientHeight);
    nextPageViewport.scrollTop = Math.min(desiredScrollTop, maximumScrollTop);
  }
  renderedPage = state.activePage;
  renderedModelScope = modelRenderScope(renderState);
  applyPresentationPreferences(livePresentationPreferences || state.presentationPreferences);

  const nextInput = root.querySelector("[data-chat-input]");
  if (nextInput) {
    nextInput.value = chatDraft;
    if (inputSelection && !nextInput.disabled) {
      nextInput.focus({ preventScroll: true });
      nextInput.setSelectionRange(inputSelection.start, inputSelection.end);
    }
  }
  const nextVoicePreview = root.querySelector("[data-voice-preview-input]");
  if (nextVoicePreview) nextVoicePreview.value = voicePreviewDraft;
  const nextWakeWord = root.querySelector("[data-wake-word-input]");
  if (nextWakeWord && wakeWordDraft) nextWakeWord.value = wakeWordDraft;
  restoreFocusedModelField(modelFieldFocus);
  const nextViewport = root.querySelector("[data-chat-viewport]");
  if (!nextViewport) return;
  const messages = state.viewModel?.chat?.messages || [];
  const nextFirstId = String(messages[0]?.id || "");
  const nextLastId = chatTimeline(state.viewModel?.chat).at(-1)?.id || "";
  const hasNewMessage = Boolean(lastChatMessageId && nextLastId && nextLastId !== lastChatMessageId);
  const prependedHistory = Boolean(
    chatPrependAnchor &&
    chatPrependAnchor.sessionId === nextChatSessionId &&
    chatPrependAnchor.firstMessageId &&
    nextFirstId &&
    chatPrependAnchor.firstMessageId !== nextFirstId
  );
  if (prependedHistory) {
    nextViewport.scrollTop = prependedHistoryScrollTop(chatPrependAnchor, nextViewport.scrollHeight);
    chatScrollTop = nextViewport.scrollTop;
    chatWasAtBottom = isNearBottom(nextViewport);
    chatPrependAnchor = null;
  } else if (!lastChatMessageId || chatWasAtBottom) {
    nextViewport.scrollTop = nextViewport.scrollHeight;
    chatWasAtBottom = true;
  } else {
    nextViewport.scrollTop = chatScrollTop;
    const jumpButton = root.querySelector("[data-chat-jump-latest]");
    if (jumpButton) jumpButton.hidden = !hasNewMessage;
  }
  lastChatMessageId = nextLastId;
  renderedChatSessionId = nextChatSessionId;
  renderedChatScope = nextChatScope;
  chatAutoPreviews.refresh();
}

window.addEventListener("resize", () => chatAutoPreviews.refresh());

function captureCapabilityUiState() {
  for (const details of root.querySelectorAll("details[data-capability-key]")) {
    const key = details.dataset.capabilityKey;
    if (!key) continue;
    if (details.open) openCapabilityPanels.add(key);
    else openCapabilityPanels.delete(key);
    const form = details.querySelector("[data-capability-form]");
    if (!form || form.closest("[data-capability-key]") !== details) continue;
    capabilityFormDrafts.set(key, Array.from(form.elements)
      .filter((element) => element.name)
      .map((element) => ({ name: element.name, value: element.value, checked: Boolean(element.checked), type: element.type })));
  }
}

function restoreCapabilityUiState() {
  for (const details of root.querySelectorAll("details[data-capability-key]")) {
    const key = details.dataset.capabilityKey;
    if (!key) continue;
    details.open = openCapabilityPanels.has(key);
    const draft = capabilityFormDrafts.get(key);
    if (!draft) continue;
    const form = details.querySelector("[data-capability-form]");
    if (!form || form.closest("[data-capability-key]") !== details) continue;
    for (const field of draft) {
      const element = form?.elements.namedItem(field.name);
      if (!element) continue;
      if (field.type === "checkbox") element.checked = field.checked;
      else element.value = field.value;
    }
  }
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

function rememberChatJobControl(payload, result) {
  const jobId = String(payload?.jobId || payload?.job_id || result?.jobId || result?.job_id || "").trim();
  if (!jobId) return;
  store.patch((state) => {
    const previous = state.chatJobControls?.[jobId] || {};
    const next = {
      ...previous,
      jobId,
      lastAction: String(payload?.action || result?.controlAction || "").trim(),
      ...(result?.ok ? {
        jobStatus: String(result?.jobStatus || result?.job_status || result?.status || previous.jobStatus || "").trim(),
        controlState: String(result?.controlState || result?.control_state || result?.status || previous.controlState || "").trim(),
        error: ""
      } : {
        error: String(result?.reason || result?.error || result?.status || "任务控制没有完成").trim()
      })
    };
    return { ...state, chatJobControls: { ...(state.chatJobControls || {}), [jobId]: next } };
  });
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

function captureFocusedModelField() {
  const active = document.activeElement;
  if (!active?.matches?.("[data-model-field]")) return null;
  return {
    field: active.dataset.modelField || "",
    start: typeof active.selectionStart === "number" ? active.selectionStart : null,
    end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
    direction: active.selectionDirection || "none"
  };
}

function restoreFocusedModelField(snapshot) {
  if (!snapshot?.field) return;
  const next = [...root.querySelectorAll("[data-model-field]")]
    .find((element) => element.dataset.modelField === snapshot.field);
  if (!next || next.disabled) return;
  next.focus({ preventScroll: true });
  if (snapshot.start === null || typeof next.setSelectionRange !== "function") return;
  const length = String(next.value || "").length;
  const start = Math.min(snapshot.start, length);
  const end = Math.min(snapshot.end ?? start, length);
  next.setSelectionRange(start, end, snapshot.direction);
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
