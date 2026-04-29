import { StaticSpriteRenderer } from "./renderers/StaticSpriteRenderer.js";
import { SpeechBubble } from "./ui/SpeechBubble.js";
import { ChatInput } from "./ui/ChatInput.js";
import { DragHandler } from "./ui/DragHandler.js";
import { StickerOverlay } from "./ui/StickerOverlay.js";

import { ContextMenu } from "./ui/ContextMenu.js";
import { BackendClient } from "./services/BackendClient.js";
import { SessionManager } from "./services/SessionManager.js";
import { TaskWatcher } from "./services/TaskWatcher.js";
import { PresentationController } from "./services/PresentationController.js";
import { VoiceRecorder, VoiceRecorderError } from "./services/VoiceRecorder.js";
import { ActivityRuntime } from "./services/ActivityRuntime.js";
import { PetLifeController } from "./services/PetLifeController.js";
import { HealthCheckService } from "./services/HealthCheckService.js";
import { DEFAULT_EMOTION, resolveEmotion } from "./services/EmotionMapper.js";

const CAPABILITIES = ["speech_segments", "tool_actions", "tts", "desktop_context", "file_drop", "audio_playback"];
const CLIENT_MODE = "desktop_pet";
const LOCAL_CLICK_LINES = [
  "我在哦。",
  "主人，怎么啦？",
  "嘿嘿，我就在这里。",
  "要和我说点什么吗？",
];
const PET_STATES = {
  IDLE: "idle",
  THINKING: "thinking",
  SPEAKING: "speaking",
};
const DEFAULT_PET_SCALE = 1;

const spriteContainer = document.getElementById("sprite-container");
const speechBubbleEl = document.getElementById("speech-bubble");
const voicePlayerEl = document.getElementById("voice-player");
const activityAudioPlayerEl = document.getElementById("activity-audio-player");
const stickerOverlayEl = document.getElementById("sticker-overlay");
const chatInputContainer = document.getElementById("chat-input-container");
const chatInputEl = document.getElementById("chat-input");
const voiceRecordButtonEl = document.getElementById("voice-record-button");
const settingsPromptEl = document.getElementById("settings-prompt");
const settingsPromptTitleEl = document.getElementById("settings-prompt-title");
const settingsPromptInputEl = document.getElementById("settings-prompt-input");
const settingsPromptCancelEl = document.getElementById("settings-prompt-cancel");
const settingsPromptSaveEl = document.getElementById("settings-prompt-save");

const contextMenuRootEl = document.getElementById("context-menu-root");

let sending = false;
let currentEmotion = DEFAULT_EMOTION;
let petState = PET_STATES.IDLE;
let rendererReady = false;
let settingsPromptResolve = null;
let resourceManifest = null;
let voiceInputEnabled = true;
let currentOpacity = 1;
let currentPetScale = DEFAULT_PET_SCALE;
let desktopContextEnabled = true;
let clipboardContextEnabled = false;
let voiceShortcutHeld = false;
let petLife = null;
let healthReport = null;
let healthCheckRunning = false;

const sessionManager = new SessionManager();
let identity = null;
const client = new BackendClient(sessionManager.getBackendUrl());
const healthCheck = new HealthCheckService({
  backendClient: client,
  getIdentity: () => identity,
  getManifest: () => resourceManifest,
  getOutfit: () => sessionManager.getOutfit(),
  getVoiceEnabled: () => presentation?.isVoiceEnabled?.() !== false,
  getVoiceInputEnabled: () => voiceInputEnabled,
});

const renderer = new StaticSpriteRenderer();
const bubble = new SpeechBubble(speechBubbleEl);
const stickerOverlay = new StickerOverlay(stickerOverlayEl);
const chatInput = new ChatInput(chatInputContainer, chatInputEl, {
  onSend: (text) => void sendMessage(text),
});
const voiceRecorder = new VoiceRecorder({
  onStateChange: (state) => updateVoiceRecordButton(state),
});
const presentation = new PresentationController({
  speechBubble: bubble,
  stickerOverlay,
  spriteRenderer: renderer,
  voicePlayerEl,
  getSpriteContext: () => ({
    outfit: sessionManager.getOutfit(),
    backendUrl: sessionManager.getBackendUrl(),
  }),
  onPetStateChange: (state) => setPetState(state),
  onEmotionChange: (emotion) => {
    currentEmotion = emotion || DEFAULT_EMOTION;
    publishDebugState();
  },
});
petLife = new PetLifeController({
  getAvailableEmotions: () => getAvailableEmotionIds(),
  getCurrentEmotion: () => currentEmotion,
  onEmotion: (emotion) => {
    presentation.dispatch({
      type: "change_emotion",
      source: "system",
      emotion: normalizeEmotionForCurrentOutfit(emotion),
    });
  },
});
const activityRuntime = new ActivityRuntime({
  audioEl: activityAudioPlayerEl,
  backendClient: client,
  getIdentity: () => identity,
  onNotice: (text) => showActivityNotice(text),
  onStatusChange: (activity) => {
    const status = String(activity?.status || "").toLowerCase();
    petLife?.setMusicActive(status === "running");
    publishCurrentActivityState(activity);
  },
});
// Workspace panel is now a standalone window — see workspace-window.js / workspace.js

const contextMenu = new ContextMenu(contextMenuRootEl, {
  getSettings: () => ({
    backendUrl: sessionManager.getBackendUrl(),
    outfit: sessionManager.getOutfit(),
    opacity: currentOpacity,
    petScale: currentPetScale,
    voiceEnabled: presentation.isVoiceEnabled(),
    voiceInputEnabled,
    desktopContextEnabled,
    clipboardContextEnabled,
  }),
  onAction: (action, value) => {
    void handleContextMenuAction(action, value);
  },
});

let dragHandler = null;
let taskWatcher = null;
let dragDepth = 0;

spriteContainer.addEventListener("contextmenu", (event) => {
  if (!renderer.isOpaqueAtPoint(event.clientX, event.clientY)) return;
  event.preventDefault();
  window.akaneAPI?.openContextMenu?.({
    screenX: event.screenX,
    screenY: event.screenY,
    settings: {
      backendUrl: sessionManager.getBackendUrl(),
      outfit: sessionManager.getOutfit(),
      opacity: currentOpacity,
      petScale: currentPetScale,
      voiceEnabled: presentation.isVoiceEnabled(),
      voiceInputEnabled,
      desktopContextEnabled,
      clipboardContextEnabled,
    },
  });
});

function showThinking() {
  presentation.showThinking();
}

function setPetState(state) {
  petState = state;
  document.body.dataset.petState = state;
  petLife?.setPetState(state);
  publishDebugState();
  if (state === PET_STATES.IDLE) {
    window.setTimeout(() => {
      taskWatcher?.flush();
    }, 0);
  }
}

async function loadPetSettings() {
  if (!window.akaneAPI?.getSettings) return {};
  try {
    return await window.akaneAPI.getSettings();
  } catch {
    return {};
  }
}

function applyPetSettings(settings, { reloadSprite = false } = {}) {
  if (!settings || typeof settings !== "object") return;

  const previousBackendUrl = sessionManager.getBackendUrl();
  const previousOutfit = sessionManager.getOutfit();

  if (settings.backendUrl) sessionManager.setBackendUrl(settings.backendUrl);
  if (settings.outfit) sessionManager.setOutfit(settings.outfit);
  if (typeof settings.voiceEnabled === "boolean") presentation.setVoiceEnabled(settings.voiceEnabled);
  if (typeof settings.voiceInputEnabled === "boolean") {
    voiceInputEnabled = settings.voiceInputEnabled;
    voiceRecorder.setEnabled(voiceInputEnabled);
    updateVoiceRecordButton(voiceRecorder.getState());
  }
  if (typeof settings.desktopContextEnabled === "boolean") {
    desktopContextEnabled = settings.desktopContextEnabled;
  }
  if (typeof settings.clipboardContextEnabled === "boolean") {
    clipboardContextEnabled = settings.clipboardContextEnabled;
  }
  if (typeof settings.opacity === "number") {
    currentOpacity = Math.max(0.3, Math.min(1, settings.opacity));
  }
  if (typeof settings.petScale === "number") {
    currentPetScale = normalizePetScale(settings.petScale);
    document.documentElement.style.setProperty("--pet-scale", currentPetScale.toFixed(2));
    chatInput.refreshLayout?.();
  }
  client.setBaseUrl(sessionManager.getBackendUrl());

  const spriteSourceChanged =
    previousBackendUrl !== sessionManager.getBackendUrl() ||
    previousOutfit !== sessionManager.getOutfit();

  if (rendererReady && (reloadSprite || spriteSourceChanged)) {
    void refreshResourceManifest({ force: true }).finally(() => {
      presentation.dispatch({ type: "change_emotion", emotion: currentEmotion, reload: true });
      publishDebugState();
    });
  }
  publishDebugState();
}

async function refreshResourceManifest({ force = false } = {}) {
  if (!identity) return resourceManifest;
  if (resourceManifest && !force) return resourceManifest;

  try {
    const manifest = await client.fetchManifest({
      profileUserId: identity.profileUserId,
      sessionId: identity.sessionId,
    });
    if (manifest && typeof manifest === "object") {
      resourceManifest = manifest;
      renderer.setManifest(resourceManifest);
      currentEmotion = normalizeEmotionForCurrentOutfit(currentEmotion);
      logKnownEmotions(resourceManifest, sessionManager.getOutfit());
      publishDebugState();
    }
  } catch (error) {
    console.warn("[AkanePet] resource manifest load failed:", error);
  }
  return resourceManifest;
}

function logKnownEmotions(manifest, outfit) {
  const outfitEntry = findManifestOutfit(manifest, outfit);
  const emotionIds = listManifestEmotions(outfitEntry).map((item) => item.id);
  if (emotionIds.length > 0) {
    console.info(`[AkanePet] 表情资源已加载: ${outfitEntry.id} -> ${emotionIds.join(", ")}`);
  }
}

function findManifestOutfit(manifest, outfit) {
  const outfits = Array.isArray(manifest?.characters?.outfits) ? manifest.characters.outfits : [];
  const target = String(outfit || manifest?.defaults?.outfit || "").trim();
  if (!target) return outfits[0] || null;
  const normalized = target.toLowerCase();
  return (
    outfits.find((item) => {
      if (!item || typeof item !== "object") return false;
      const values = [item.id, item.name, ...(Array.isArray(item.aliases) ? item.aliases : [])]
        .map((value) => String(value || "").trim())
        .filter(Boolean);
      return values.some((value) => value === target || value.toLowerCase() === normalized);
    }) ||
    outfits.find((item) => String(item?.id || "") === String(manifest?.defaults?.outfit || "")) ||
    outfits[0] ||
    null
  );
}

function buildCurrentVisual() {
  const outfitEntry = findManifestOutfit(resourceManifest, sessionManager.getOutfit());
  const emotions = listManifestEmotions(outfitEntry);
  const normalizedEmotion = normalizeEmotionForCurrentOutfit(currentEmotion);
  currentEmotion = normalizedEmotion;

  return {
    emotion: normalizedEmotion,
    character: {
      outfit: String(outfitEntry?.id || sessionManager.getOutfit() || "").trim(),
      available_emotions: emotions.map((item) => ({
        id: item.id,
        name: item.name,
        aliases: item.aliases,
      })),
    },
    scene: {},
    available_emotions: emotions.map((item) => item.id),
  };
}

function getAvailableEmotionIds() {
  const outfitEntry = findManifestOutfit(resourceManifest, sessionManager.getOutfit());
  return listManifestEmotions(outfitEntry).map((item) => item.id);
}

function normalizeEmotionForCurrentOutfit(emotion) {
  const outfitEntry = findManifestOutfit(resourceManifest, sessionManager.getOutfit());
  if (!outfitEntry) {
    return resolveEmotion(emotion || DEFAULT_EMOTION);
  }

  const requested = findManifestEmotion(outfitEntry, emotion);
  if (requested) return String(requested.id || DEFAULT_EMOTION).trim() || DEFAULT_EMOTION;

  const fallback =
    findManifestEmotion(outfitEntry, resourceManifest?.defaults?.emotion) ||
    findManifestEmotion(outfitEntry, DEFAULT_EMOTION) ||
    listManifestEmotions(outfitEntry)[0];
  return String(fallback?.id || DEFAULT_EMOTION).trim() || DEFAULT_EMOTION;
}

function findManifestEmotion(outfitEntry, emotion) {
  const emotions = listManifestEmotions(outfitEntry);
  const direct = findManifestEntry(emotions, emotion);
  if (direct) return direct;
  return findManifestEntry(emotions, resolveEmotion(emotion));
}

function listManifestEmotions(outfitEntry) {
  return (Array.isArray(outfitEntry?.emotions) ? outfitEntry.emotions : [])
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      ...item,
      id: String(item.id || "").trim(),
      name: String(item.name || item.id || "").trim(),
      aliases: (Array.isArray(item.aliases) ? item.aliases : [])
        .map((alias) => String(alias || "").trim())
        .filter(Boolean),
    }))
    .filter((item) => item.id);
}

function findManifestEntry(items, value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const key = normalizeLookupKey(raw);
  return (
    items.find((item) => {
      const values = [item.id, item.name, ...(Array.isArray(item.aliases) ? item.aliases : [])]
        .map((option) => String(option || "").trim())
        .filter(Boolean);
      return values.some((option) => option === raw || normalizeLookupKey(option) === key);
    }) || null
  );
}

function normalizeLookupKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

function setupMainProcessEvents() {
  if (!window.akaneAPI) return;

  window.akaneAPI.onSettingsChanged?.((settings) => {
    applyPetSettings(settings);
  });

  window.akaneAPI.onSettingsPrompt?.((payload) => {
    void promptAndSaveSetting(payload);
  });

  window.akaneAPI.onReloadSprite?.(() => {
    void reloadSprite();
  });

  window.akaneAPI.onVoiceShortcutToggle?.(() => {
    void toggleVoiceRecording();
  });

  window.akaneAPI.onWorkspacePanelToggle?.(() => {
    window.akaneAPI.toggleWorkspacePanel?.(identity || {});
  });

  window.akaneAPI.onDebugPanelToggle?.(() => {
    window.akaneAPI.toggleDebugPanel?.();
    window.setTimeout(() => publishDebugState(), 120);
  });

  window.akaneAPI.onSettingsPanelToggle?.(() => {
    window.akaneAPI.toggleSettingsPanel?.();
  });

  window.akaneAPI.onWorkspaceActivityStateRequest?.(() => {
    publishCurrentActivityState();
  });

  window.akaneAPI.onWorkspaceActivityAction?.((action) => {
    void handleWorkspaceActivityAction(action);
  });

  window.akaneAPI.onDebugStateRequest?.(() => {
    publishDebugState();
  });

  window.akaneAPI.onDebugAction?.((action) => {
    void handleDebugAction(action);
  });

  window.akaneAPI.onMenuAction?.((action, value) => {
    void handleContextMenuAction(action, value);
  });
}

function publishCurrentActivityState(activity = null) {
  const payload = activity || activityRuntime.getCurrentActivity();
  window.akaneAPI?.publishWorkspaceActivityState?.(payload);
  publishDebugState();
}

function buildDebugState() {
  const outfitEntry = findManifestOutfit(resourceManifest, sessionManager.getOutfit());
  const lastResolution = renderer.getLastResolution?.() || null;
  return {
    profileUserId: identity?.profileUserId || "master",
    sessionId: identity?.sessionId || "",
    backendUrl: sessionManager.getBackendUrl(),
    outfit: String(outfitEntry?.id || sessionManager.getOutfit() || "").trim(),
    petState,
    lifeMotion: document.body.dataset.lifeMotion || "idle",
    currentEmotion,
    resolvedEmotion: lastResolution?.id || currentEmotion,
    spriteSource: lastResolution?.source || "",
    voiceEnabled: presentation.isVoiceEnabled(),
    voiceInputEnabled,
    petScale: currentPetScale,
    desktopContextEnabled,
    clipboardContextEnabled,
    currentActivity: activityRuntime.getCurrentActivity(),
    healthReport,
    healthCheckRunning,
    availableEmotions: listManifestEmotions(outfitEntry).map((item) => ({
      id: item.id,
      name: item.name || item.id,
      aliases: item.aliases || [],
    })),
    updatedAt: Date.now(),
  };
}

function publishDebugState() {
  window.akaneAPI?.publishDebugState?.(buildDebugState());
}

async function handleWorkspaceActivityAction(action) {
  const name = String(action?.action || action || "").trim().toLowerCase();
  if (!["play", "pause", "resume", "stop"].includes(name)) return;
  await activityRuntime.applyAction({
    action: name,
    target: "current",
  });
  publishCurrentActivityState();
}

async function handleDebugAction(action) {
  const type = String(action?.type || action?.action || "").trim().toLowerCase();
  if (type === "preview-emotion") {
    const emotion = String(action?.emotion || "").trim();
    if (!emotion) return;
    presentation.dispatch({
      type: "change_emotion",
      source: "system",
      emotion: normalizeEmotionForCurrentOutfit(emotion),
    });
    publishDebugState();
    return;
  }

  if (type === "test-bubble") {
    showLocalNotice("状态预览器：气泡测试正常。");
    return;
  }

  if (type === "test-tts") {
    const text = "状态预览器：语音播放测试。";
    showLocalNotice(text);
    presentation.dispatch({
      type: "play_tts",
      source: "formal",
      text,
      force: true,
    });
    return;
  }

  if (type === "refresh") {
    publishDebugState();
    return;
  }

  if (type === "run-health-check") {
    await runHealthCheck({ announce: true });
  }
}

async function runHealthCheck({ announce = false } = {}) {
  if (healthCheckRunning) return healthReport;
  healthCheckRunning = true;
  publishDebugState();
  try {
    healthReport = await healthCheck.run();
    if (announce && healthReport?.status && healthReport.status !== "ok") {
      petLife?.showMoment("confused");
      showLocalNotice(healthReport.summary || "启动自检发现需要确认的项目。");
    }
    publishDebugState();
    return healthReport;
  } catch (error) {
    healthReport = {
      status: "error",
      summary: "启动自检执行失败。",
      items: [
        {
          id: "health_check",
          label: "启动自检",
          status: "error",
          message: String(error?.message || error || "自检失败"),
        },
      ],
      checkedAt: Date.now(),
    };
    if (announce) {
      petLife?.showMoment("confused");
      showLocalNotice("启动自检执行失败，可以打开状态预览器看看。");
    }
    publishDebugState();
    return healthReport;
  } finally {
    healthCheckRunning = false;
    publishDebugState();
  }
}

async function promptAndSaveSetting(payload) {
  const key = String(payload?.key || "");
  if (!["backendUrl", "outfit"].includes(key)) return;

  const title = String(payload?.title || key);
  const currentValue = String(payload?.value || "");
  const nextValue = await openSettingsPrompt(title, currentValue);
  if (nextValue === null) return;

  const trimmed = nextValue.trim();
  if (!trimmed) return;

  const settings = await window.akaneAPI.setSettings({ [key]: trimmed });
  applyPetSettings(settings, { reloadSprite: true });
}

function openSettingsPrompt(title, value) {
  closeSettingsPrompt(null);

  settingsPromptTitleEl.textContent = title;
  settingsPromptInputEl.value = value;
  settingsPromptEl.classList.add("visible");
  settingsPromptEl.setAttribute("aria-hidden", "false");
  window.setTimeout(() => {
    settingsPromptInputEl.focus();
    settingsPromptInputEl.select();
  }, 0);

  return new Promise((resolve) => {
    settingsPromptResolve = resolve;
  });
}

function closeSettingsPrompt(result) {
  if (!settingsPromptEl) return;

  settingsPromptEl.classList.remove("visible");
  settingsPromptEl.setAttribute("aria-hidden", "true");

  if (settingsPromptResolve) {
    const resolve = settingsPromptResolve;
    settingsPromptResolve = null;
    resolve(result);
  }
}

function isSettingsPromptVisible() {
  return settingsPromptEl?.classList.contains("visible") || false;
}

settingsPromptCancelEl.addEventListener("click", () => {
  closeSettingsPrompt(null);
});

settingsPromptSaveEl.addEventListener("click", () => {
  closeSettingsPrompt(settingsPromptInputEl.value);
});

settingsPromptInputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    closeSettingsPrompt(settingsPromptInputEl.value);
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeSettingsPrompt(null);
  }
});

voiceRecordButtonEl.addEventListener("click", () => {
  void toggleVoiceRecording();
});

document.addEventListener("keydown", (event) => {
  if (!isVoiceShortcut(event) || event.repeat) return;
  event.preventDefault();
  voiceShortcutHeld = true;
  if (!voiceRecorder.isRecording()) {
    void startVoiceRecording();
  }
});

document.addEventListener("keyup", (event) => {
  if (!isVoiceShortcut(event) || !voiceShortcutHeld) return;
  event.preventDefault();
  voiceShortcutHeld = false;
  if (voiceRecorder.isRecording()) {
    void stopVoiceRecording();
  }
});

function isVoiceShortcut(event) {
  return event.ctrlKey && event.shiftKey && !event.altKey && event.code === "Space";
}

async function toggleVoiceRecording() {
  if (voiceRecorder.isRecording()) {
    await stopVoiceRecording();
  } else {
    await startVoiceRecording();
  }
}

async function startVoiceRecording() {
  if (!voiceInputEnabled) {
    petLife?.showMoment("confused");
    showLocalNotice("语音输入现在是关闭的，可以在托盘菜单里打开。");
    return;
  }
  if (sending) {
    petLife?.showMoment("confused");
    showLocalNotice("我正在回复这轮消息，等一下再听你说。");
    return;
  }

  try {
    await voiceRecorder.start();
    petLife?.setListeningActive(true);
    showLocalNotice("正在听……");
  } catch (error) {
    petLife?.setListeningActive(false);
    showVoiceError(error);
  }
}

async function stopVoiceRecording() {
  let audioBlob = null;
  try {
    audioBlob = await voiceRecorder.stop();
  } catch (error) {
    petLife?.setListeningActive(false);
    showVoiceError(error);
    return;
  }
  petLife?.setListeningActive(false);
  if (!audioBlob) return;

  voiceRecorder.setProcessing(true);
  petLife?.showMoment("thinking", { durationMs: 1400 });
  showLocalNotice("我在识别语音……");
  try {
    const result = await client.transcribeAudio({
      audioBlob,
      filename: voiceRecorder.getFilename(),
      language: "zh",
    });
    if (!result?.ok || !String(result.text || "").trim()) {
      showVoiceError(result?.message || result?.error || "没听清，可以再说一次。");
      return;
    }
    chatInput.setText(String(result.text || "").trim());
    petLife?.showMoment("success");
    showLocalNotice("我听写好了，主人确认一下再发送。");
  } catch (error) {
    showVoiceError(error);
  } finally {
    voiceRecorder.setProcessing(false);
  }
}

function updateVoiceRecordButton(state) {
  if (!voiceRecordButtonEl) return;
  voiceRecordButtonEl.classList.toggle("recording", state === "recording");
  voiceRecordButtonEl.classList.toggle("processing", state === "processing");
  voiceRecordButtonEl.disabled = state === "disabled" || state === "processing";
  if (state === "recording") {
    voiceRecordButtonEl.textContent = "停";
    voiceRecordButtonEl.title = "停止录音";
  } else if (state === "processing") {
    voiceRecordButtonEl.textContent = "…";
    voiceRecordButtonEl.title = "正在识别语音";
  } else {
    voiceRecordButtonEl.textContent = "麦";
    voiceRecordButtonEl.title = voiceInputEnabled ? "语音输入 Ctrl+Shift+Space" : "语音输入已关闭";
  }
}

function showLocalNotice(text, { state = "idle" } = {}) {
  const message = String(text || "").trim();
  if (!message) return;
  presentation.dispatch({
    type: "show_bubble",
    source: "system",
    text: message,
    key: `system_notice:${message}`,
    queue: false,
    state,
  });
}

function showActivityNotice(text) {
  const message = String(text || "").trim();
  if (!message) return;
  if (/失败|没|没有|不是|不可|拦截|超时|错误/.test(message)) {
    petLife?.showMoment("confused");
  } else if (/正在|我在/.test(message)) {
    petLife?.showMoment("thinking", { durationMs: 1400 });
  } else if (/已|好啦|完成|放在手边/.test(message)) {
    petLife?.showMoment("success");
  }
  showLocalNotice(message);
}

function showVoiceError(error) {
  petLife?.showMoment("confused");
  const code = error instanceof VoiceRecorderError ? error.code : "";
  const message = String(error?.message || error || "").trim();
  const fallback = code === "too_short" ? "录音太短啦，我没听清。" : "语音输入失败了。";
  showLocalNotice(message || fallback);
}

function setupAudioDropZone() {
  document.addEventListener("dragenter", (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    document.body.classList.add("drag-audio-over");
  });

  document.addEventListener("dragover", (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  });

  document.addEventListener("dragleave", (event) => {
    if (!hasDraggedFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) document.body.classList.remove("drag-audio-over");
  });

  document.addEventListener("drop", (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    document.body.classList.remove("drag-audio-over");
    void handleAudioDrop(event);
  });
}

async function handleAudioDrop(event) {
  const files = Array.from(event.dataTransfer?.files || []);
  const audioFile = files.find((file) => activityRuntime.canAcceptFile(file));
  if (!audioFile) {
    petLife?.showMoment("confused");
    showLocalNotice("拖进来的文件不是可播放音频。");
    return;
  }
  try {
    await activityRuntime.handleDroppedAudio(audioFile);
    window.akaneAPI.notifyWorkspaceChanged?.();
    publishCurrentActivityState();
  } catch (error) {
    console.warn("[AkanePet] audio drop failed:", error);
    petLife?.showMoment("confused");
    showLocalNotice(String(error?.message || "音频拖拽处理失败。"));
  }
}

function hasDraggedFiles(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes("Files");
}

async function reloadSprite() {
  if (!rendererReady) return;
  await refreshResourceManifest({ force: true });
  presentation.dispatch({ type: "change_emotion", emotion: currentEmotion, reload: true });
}

function showLocalInteraction() {
  if (sending || petState !== PET_STATES.IDLE || chatInput.isVisible()) return;

  const index = Math.floor(Math.random() * LOCAL_CLICK_LINES.length);
  const text = LOCAL_CLICK_LINES[index];
  presentation.dispatch({ type: "local_reaction", text });
  petLife?.reactToClick();
}

function canShowPassiveReminder() {
  return (
    !sending &&
    petState === PET_STATES.IDLE &&
    presentation.canAcceptPassive() &&
    !chatInput.isVisible() &&
    !isSettingsPromptVisible()
  );
}

function showTaskReminder(text, item = null) {
  if (!text || !canShowPassiveReminder()) return;
  const state = String(item?.handoff?.state || item?.status || "").trim().toLowerCase();
  petLife?.showMoment(state === "blocked" ? "confused" : "success");
  presentation.dispatch({ type: "task_notice", text });
}

// ── Stream processing ──
async function processStream(gen) {
  let finalPayload = null;
  let streamErrored = false;
  let streamErrorMessage = "";
  let partialSpeech = "";
  let renderedTerminalFallback = false;
  let authoritativeRendered = false;

  function applyEmotion(payload) {
    const emotion = String(payload?.emotion || "").trim();
    if (emotion) {
      presentation.dispatch({
        type: "change_emotion",
        source: "formal",
        emotion: normalizeEmotionForCurrentOutfit(emotion),
      });
    }
  }

  function renderAuthoritativePayload(payload) {
    const shown = presentation.dispatch({
      type: "show_bubble",
      source: "formal",
      payload,
      stopTts: true,
    });
    if (shown) {
      authoritativeRendered = true;
      presentation.dispatch({ type: "play_tts", source: "formal", payload });
    }
    return shown;
  }

  function renderTerminalFallback(payload) {
    if (authoritativeRendered || renderedTerminalFallback) return false;

    renderedTerminalFallback = true;
    const shown = presentation.dispatch({
      type: "show_bubble",
      source: "formal",
      payload,
      stopTts: true,
    });
    if (shown) {
      presentation.dispatch({ type: "play_tts", source: "formal", payload });
    }
    return shown;
  }

  function applyActivity(payload) {
    if (!payload?.activity) return;
    void activityRuntime.applyAction(payload.activity).finally(() => {
      publishCurrentActivityState();
    });
  }

  for await (const event of gen) {
    const type = String(event?.type || "").trim().toLowerCase();

    if (type === "stream_start") {
      // no-op
    } else if (type === "turn_start") {
      showThinking();
    } else if (type === "ui") {
      applyEmotion(event);
    } else if (type === "sticker_ready") {
      presentation.dispatch({ type: "show_sticker", source: "formal", sticker: event?.sticker || event?.payload?.sticker });
    } else if (type === "generated_file_ready") {
      const generatedFile = event?.generated_file || event?.payload?.generated_file || event?.file || null;
      activityRuntime.registerGeneratedFile(generatedFile);
    } else if (type === "speech_chunk") {
      const text = String(event?.text || "");
      if (text) {
        // Keep partial text only for failure fallback; final payload decides the real presentation.
        partialSpeech += text;
      }
    } else if (type === "final" || type === "final_ui") {
      const payload = event?.payload || null;
      if (payload) {
        applyEmotion(payload);
        renderAuthoritativePayload(payload);
        applyActivity(payload);
        finalPayload = payload;
      }
    } else if (type === "npc_turn") {
      const speech = String(event?.speech || "").trim();
      if (speech && !authoritativeRendered) {
        const shown = presentation.dispatch({
          type: "show_bubble",
          source: "formal",
          text: speech,
          stopTts: true,
        });
        if (shown) authoritativeRendered = true;
      }
    } else if (type === "stream_error" || type === "error") {
      streamErrored = true;
      streamErrorMessage = String(event?.message || "Stream error");
      const partial = event?.partial;
      if (partial) applyEmotion(partial);
      const fallbackSpeech = String(partial?.speech || partialSpeech || "").trim();
      if (fallbackSpeech) {
        renderTerminalFallback({ ...partial, speech: fallbackSpeech });
      }
    } else if (type === "stream_end") {
      const partial = event?.partial;
      if (partial) applyEmotion(partial);
      const fallbackSpeech = String(partial?.speech || partialSpeech || "").trim();
      if (fallbackSpeech) {
        renderTerminalFallback({ ...partial, speech: fallbackSpeech });
      }
    }
  }

  if (!finalPayload && streamErrored && !authoritativeRendered && !renderedTerminalFallback) {
    presentation.dispatch({
      type: "show_bubble",
      source: "formal",
      text: streamErrorMessage || "未收到完整回应",
      stopTts: true,
    });
  }
}

// ── Send message ──
async function collectDesktopContextForTurn() {
  if (!desktopContextEnabled || !window.akaneAPI?.getDesktopContext) return null;

  try {
    const context = await window.akaneAPI.getDesktopContext({
      includeClipboard: clipboardContextEnabled,
    });
    if (!context?.ok) return null;
    return context;
  } catch (error) {
    console.warn("[AkanePet] desktop context collection failed:", error);
    return null;
  }
}

async function sendMessage(text) {
  if (sending) return;
  const trimmed = String(text || "").trim();
  if (!trimmed) return;

  sending = true;
  showThinking();

  try {
    const currentActivity = activityRuntime.interruptForUserMessage();
    publishCurrentActivityState(currentActivity);
    const desktopContext = await collectDesktopContextForTurn();
    const gen = client.sendMessage({
      profileUserId: identity.profileUserId,
      sessionId: identity.sessionId,
      message: trimmed,
      clientMode: CLIENT_MODE,
      capabilities: CAPABILITIES,
      currentVisual: buildCurrentVisual(),
      desktopContext,
      currentActivity,
    });
    await processStream(gen);
  } catch (error) {
    const message = String(error?.message || "请求失败");
    presentation.dispatch({
      type: "show_bubble",
      source: "formal",
      text: message,
      stopTts: true,
    });
  } finally {
    sending = false;
    if (petState === PET_STATES.THINKING) {
      setPetState(PET_STATES.IDLE);
    }
  }
}

// ── Context menu actions ──
async function handleContextMenuAction(action, value) {
  switch (action) {
    case "toggle-window":
      window.akaneAPI?.minimizeWindow?.();
      break;
    case "reload-sprite":
      await reloadSprite();
      break;
    case "workspace-panel":
      window.akaneAPI.toggleWorkspacePanel?.(identity || {});
      break;
    case "debug-panel":
      window.akaneAPI.toggleDebugPanel?.();
      window.setTimeout(() => publishDebugState(), 120);
      break;
    case "settings-panel":
      window.akaneAPI.toggleSettingsPanel?.();
      break;
    case "prompt-backend":
      await promptAndSaveSetting({ key: "backendUrl", title: "后端地址", value: sessionManager.getBackendUrl() });
      break;
    case "prompt-outfit":
      await promptAndSaveSetting({ key: "outfit", title: "服装名", value: sessionManager.getOutfit() });
      break;
    case "set-opacity":
      await window.akaneAPI?.setSettings?.({ opacity: parseFloat(value) || 1 });
      break;
    case "set-pet-scale":
      await window.akaneAPI?.setSettings?.({ petScale: normalizePetScale(value) });
      break;
    case "toggle-voice":
      await window.akaneAPI?.setSettings?.({ voiceEnabled: !presentation.isVoiceEnabled() });
      break;
    case "toggle-voice-input":
      await window.akaneAPI?.setSettings?.({ voiceInputEnabled: !voiceInputEnabled });
      break;
    case "toggle-desktop-ctx":
      await window.akaneAPI?.setSettings?.({ desktopContextEnabled: !desktopContextEnabled });
      break;
    case "toggle-clipboard-ctx":
      await window.akaneAPI?.setSettings?.({ clipboardContextEnabled: !clipboardContextEnabled });
      break;
    case "quit":
      window.akaneAPI?.closeWindow?.();
      break;
  }
}

function normalizePetScale(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_PET_SCALE;
  return Math.max(0.75, Math.min(1.45, numeric));
}

// ── Init ──
async function init() {
  let backendConnectionFailed = false;
  const settings = await loadPetSettings();
  identity = sessionManager.init(settings);
  applyPetSettings(settings);
  setupMainProcessEvents();
  setupAudioDropZone();
  setPetState(PET_STATES.IDLE);
  await refreshResourceManifest({ force: true });

  await renderer.init(spriteContainer);
  rendererReady = true;
  presentation.dispatch({ type: "change_emotion", emotion: DEFAULT_EMOTION });

  // Drag/click on the sprite container; transparent edges are minimal in practice
  dragHandler = new DragHandler(spriteContainer, {
    canStart: (event) => renderer.isOpaqueAtPoint(event.clientX, event.clientY),
    onClick: () => {
      showLocalInteraction();
    },
    onDoubleClick: () => {
      chatInput.show();
    },
    onDragStart: () => {
      presentation.hideBubble();
    },
    onDragMove: (dx, dy) => {
      if (window.akaneAPI) {
        window.akaneAPI.moveWindow(dx, dy);
      }
    },
  });

  try {
    const bundle = await client.ensureSession({
      profileUserId: identity.profileUserId,
      sessionId: identity.sessionId,
      displayTitle: "桌宠对话",
    });

    const latestPayload = bundle?.latest_final_json;
    if (latestPayload && typeof latestPayload === "object") {
      const emotion = String(latestPayload.emotion || "").trim();
      if (emotion) {
        presentation.dispatch({ type: "change_emotion", emotion: normalizeEmotionForCurrentOutfit(emotion) });
      }
      presentation.dispatch({
        type: "show_bubble",
        source: "formal",
        payload: latestPayload,
        stopTts: false,
      });
    }
  } catch (error) {
    backendConnectionFailed = true;
    presentation.dispatch({
      type: "show_bubble",
      source: "system",
      text: "Akane 后端未连接\n请先启动 backend 服务",
      stopTts: false,
    });
  }

  taskWatcher = new TaskWatcher({
    getBackendUrl: () => sessionManager.getBackendUrl(),
    getIdentity: () => identity,
    canNotify: () => canShowPassiveReminder(),
    onNotify: (message, item) => {
      showTaskReminder(message, item);
    },
  });
  taskWatcher.start();
  void runHealthCheck({ announce: !backendConnectionFailed });
}

init();
