import { createAudioController } from "./modules/audio.js";
import { createAvatarController } from "./modules/avatar.js?v=20260418_emotion_motion1";
import { createIdentityHelpers } from "./modules/identity.js";
import { createVoiceLipSyncController } from "./modules/lip-sync.js?v=20260418_lipsync2";
import { createUiShellHelpers } from "./modules/ui-shell.js";

const appEl = document.getElementById("app");
const bgmPlayerEl = document.getElementById("bgm-player");
const voicePlayerEl = document.getElementById("voice-player");
const dialogueTextEl = document.getElementById("dialogue-text");
const dialogueCodeEl = document.getElementById("dialogue-code");
const emotionPillEl = document.getElementById("emotion-pill");
const statusPillEl = document.getElementById("status-pill");
const scenePillEl = document.getElementById("scene-pill");
const bgmPillEl = document.getElementById("bgm-pill");
const modePillEl = document.getElementById("mode-pill");
const subtitleEl = document.getElementById("scene-subtitle");
const speakerTagEl = document.getElementById("speaker-tag");
const dialogueTurnsEl = document.getElementById("dialogue-turns");
const choicesHeadingEl = document.getElementById("choices-heading");
const choicesRowEl = document.getElementById("choices-row");
const historyListEl = document.getElementById("history-list");
const debugOutputEl = document.getElementById("debug-output");
const spriteEl = document.getElementById("akane-sprite");
const live2dCanvasEl = document.getElementById("akane-live2d-canvas");
const inputEl = document.getElementById("message-input");
const sendButtonEl = document.getElementById("send-button");
const newSessionButtonEl = document.getElementById("new-session");
const renameSessionButtonEl = document.getElementById("rename-session");
const copyIdentityButtonEl = document.getElementById("copy-identity");
const importIdentityButtonEl = document.getElementById("import-identity");
const sessionListEl = document.getElementById("session-list");
const voiceToggleEl = document.getElementById("voice-toggle");
const voiceToggleLabelEl = document.getElementById("voice-toggle-label");
const historyToggleEl = document.getElementById("history-toggle");
const historyCloseEl = document.getElementById("history-close");
const instantToggleEl = document.getElementById("instant-toggle");
const instantToggleLabelEl = document.getElementById("instant-toggle-label");
const avatarToggleEl = document.getElementById("avatar-toggle");
const avatarToggleLabelEl = document.getElementById("avatar-toggle-label");
const avatarSizeRangeEl = document.getElementById("avatar-size-range");
const avatarSizeLabelEl = document.getElementById("avatar-size-label");
const timeChipEl = document.getElementById("time-chip");
const chatFormEl = document.getElementById("chat-form");
const composerDockEl = document.getElementById("composer-dock");
const composerToggleEl = document.getElementById("composer-toggle");
const settingsToggleEl = document.getElementById("settings-toggle");
const settingsPanelEl = document.getElementById("settings-panel");
const settingsBackdropEl = document.getElementById("settings-backdrop");
const settingsCloseEl = document.getElementById("settings-close");
const settingsStatusCopyEl = document.getElementById("settings-status-copy");
const modelServiceSettingsEl = document.getElementById("model-service-settings");
const modelServiceStatusEl = document.getElementById("model-service-status");
const modelServiceStatusDetailEl = document.getElementById("model-service-status-detail");
const modelServiceProviderEl = document.getElementById("model-service-provider");
const modelServiceBaseUrlEl = document.getElementById("model-service-base-url");
const modelServiceApiKeyEl = document.getElementById("model-service-api-key");
const modelServiceChatModelEl = document.getElementById("model-service-chat-model");
const modelServiceModelListEl = document.getElementById("model-service-model-list");
const modelServiceUseVisionEl = document.getElementById("model-service-use-vision");
const modelServiceModelsEl = document.getElementById("model-service-models");
const modelServiceTestEl = document.getElementById("model-service-test");
const modelServiceSaveEl = document.getElementById("model-service-save");
const modelServiceMessageEl = document.getElementById("model-service-message");
const giftHandToggleEl = document.getElementById("gift-hand-toggle");
const giftHandPanelEl = document.getElementById("gift-hand-panel");
const giftHandCloseEl = document.getElementById("gift-hand-close");
const giftHandListEl = document.getElementById("gift-hand-list");
const giftHandSummaryEl = document.getElementById("gift-hand-summary");
const giftHandOverflowEl = document.getElementById("gift-hand-overflow");
const giftHandOpenLibraryEl = document.getElementById("gift-hand-open-library");
const giftLibraryEl = document.getElementById("gift-library");
const giftDropOverlayEl = document.getElementById("gift-drop-overlay");
const artifactContainersEl = document.getElementById("artifact-containers");
const artifactContainerDetailEl = document.getElementById("artifact-container-detail");

const bgFrames = [
  document.querySelector(".bg-frame-a"),
  document.querySelector(".bg-frame-b"),
];

const DEFAULT_GREETING = "今晚想和 Akane 聊点什么？";
const THINKING_TEXT = "……";
const FALLBACK_REPLY = "刚才没有收到完整回应，请再说一次。";
const ERROR_REPLY = "刚才没有成功回应，请再试一次。";
const TIMEOUT_REPLY = "这次等得有点久，前端先超时了，请再试一次。";
const DEFAULT_SPEAKER = "角色";
const USER_SPEAKER = "你";
const BASE_BGM_VOLUME = 0.34;
const DUCKED_BGM_VOLUME = 0.12;
const THINK_REQUEST_TIMEOUT_MS = 5 * 60 * 1000;
const MIN_TTS_SEGMENT_CHARS = 8;
const REMINDER_POLL_INTERVAL_MS = 15_000;
const SESSION_SYNC_INITIAL_DELAY_MS = 280;
const SESSION_SYNC_RETRY_DELAY_MS = 420;
const IDENTITY_STORAGE_KEY = "gal_shell.identity.v1";
const IDENTITY_BACKUP_PREFIX = "akane-id:";
const OWNER_PROFILE_USER_ID = "master";
const WEB_SESSION_ID_PREFIX = "web";
const BROWSER_PROFILE_ID_PREFIX = "browser";
const LEGACY_VISUAL_STATE_STORAGE_KEY = "gal_shell.visual_state.v1";
const VISUAL_STATE_STORAGE_KEY_PREFIX = "gal_shell.visual_state.v1.";
const VOICE_ENABLED_STORAGE_KEY = "gal_shell.voice_enabled.v1";
const AVATAR_MODE_STORAGE_KEY = "gal_shell.avatar_mode.v1";
const AVATAR_SIZE_STORAGE_KEY = "gal_shell.avatar_size.v1";
const DEFAULT_AVATAR_SCALE = 1.2;
const GIFT_AUDIO_EXTENSIONS = [".mp3", ".ogg", ".wav", ".m4a", ".flac"];
const GIFT_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];
const GIFT_METADATA_REFRESH_DELAY_MS = 1200;
const GIFT_METADATA_REFRESH_MAX_ATTEMPTS = 8;
const ARTIFACT_CONTAINER_ORDER = ["music_box", "album"];
const FALLBACK_BACKGROUND_PATH = "/assets/scenes/街道/黄昏街道.png";
const FALLBACK_SPRITE_PATH = "/assets/characters/猫娘/正常.png";

const STATUS_LABELS = {
  idle: "状态 待机",
  thinking: "状态 思考中",
  final: "状态 已回应",
  error: "状态 异常",
};

const state = {
  manifest: null,
  manifestIndex: null,
  currentVisual: null,
  currentMode: "gal",
  webIdentity: {
    mode: "owner",
    ownerProfileUserId: OWNER_PROFILE_USER_ID,
  },
  identity: {
    profileUserId: "",
    sessionId: "",
  },
  sessions: [],
  history: [],
  activeBgIndex: 0,
  currentBackgroundPath: "",
  currentSpritePath: "",
  currentTrackPath: "",
  sending: false,
  typingToken: 0,
  instantText: false,
  avatarMode: "static",
  avatarModeLoading: false,
  avatarScale: DEFAULT_AVATAR_SCALE,
  audioUnlocked: false,
  voiceEnabled: true,
  streamingTtsEnabled: true,
  currentVoiceUrl: "",
  voiceAbortController: null,
  voicePlaybackToken: 0,
  streamSpeechText: "",
  streamTypewriterQueue: [],
  streamTypewriterTask: null,
  ttsTextBuffer: "",
  ttsRequestQueue: [],
  ttsRequestActive: false,
  voiceQueueItems: new Map(),
  voiceQueueToken: 0,
  voiceNextSequence: 0,
  voiceNextPlaySequence: 0,
  voicePlaybackActive: false,
  voiceStreamComplete: false,
  voiceRequestControllers: [],
  reminderPollInFlight: false,
  pendingReminderNotifications: [],
  flushingReminderNotifications: false,
  gifts: [],
  giftInventory: {
    items: [],
    totalCount: 0,
    overflowCount: 0,
  },
  giftHandOpen: false,
  giftActionInFlight: "",
  dragDepth: 0,
  giftMetadataRefreshToken: 0,
  artifactContainers: [],
  selectedArtifactContainerType: "music_box",
  selectedArtifactContainerKey: "",
  artifactContainerDetail: null,
  modelService: null,
  modelServiceBusy: false,
};

const identityHelpers = createIdentityHelpers({
  state,
  DEFAULT_SPEAKER,
  USER_SPEAKER,
  IDENTITY_STORAGE_KEY,
  IDENTITY_BACKUP_PREFIX,
  OWNER_PROFILE_USER_ID,
  WEB_SESSION_ID_PREFIX,
  BROWSER_PROFILE_ID_PREFIX,
  LEGACY_VISUAL_STATE_STORAGE_KEY,
  VISUAL_STATE_STORAGE_KEY_PREFIX,
  VOICE_ENABLED_STORAGE_KEY,
  formatModeLabel,
});

const {
  getLocalStorage,
  createUuid,
  normalizePersistedIdentity,
  loadPersistedIdentity,
  persistIdentity,
  ensureIdentity,
  getCurrentProfileUserId,
  getCurrentSessionId,
  encodeIdentityBackup,
  decodeIdentityBackup,
  normalizeSessionRecord,
  getCurrentSessionRecord,
  mapRoleToSpeaker,
  buildHistoryEntriesFromMessages,
  createVisualStateStorageKey,
  rotateSessionIdentity,
  loadVoiceEnabledPreference,
  persistVoiceEnabledPreference,
  normalizePersistedVisual,
  loadPersistedShellState,
  persistShellState,
  clearPersistedShellState,
} = identityHelpers;

const audioController = createAudioController({
  state,
  bgmPlayerEl,
  bgmPillEl,
  voicePlayerEl,
  debugOutputEl,
  wait,
  BASE_BGM_VOLUME,
  DUCKED_BGM_VOLUME,
  MIN_TTS_SEGMENT_CHARS,
});

const {
  fadeAudioTo,
  playBgmWithFade,
  updateBgm,
  revokeVoiceUrl,
  revokeQueuedVoiceUrls,
  forgetVoiceController,
  duckBgmForVoice,
  restoreBgmAfterVoice,
  cleanupVoicePlayer,
  stopVoicePlayback,
  countSpeakableChars,
  findTtsSplitIndex,
  enqueueSpeechSegment,
  processTtsRequestQueue,
  flushBufferedSpeechSegments,
  appendSpeechForTts,
  enqueueDirectSpeech,
  pumpVoiceQueue,
  markVoiceStreamComplete,
  speakText,
  finalizeStreamAudioIfIdle,
} = audioController;

const uiShellHelpers = createUiShellHelpers({
  state,
  appEl,
  historyListEl,
  dialogueTurnsEl,
  choicesHeadingEl,
  choicesRowEl,
  sessionListEl,
  dialogueCodeEl,
  instantToggleEl,
  instantToggleLabelEl,
  voiceToggleEl,
  voiceToggleLabelEl,
  inputEl,
  sendButtonEl,
  newSessionButtonEl,
  renameSessionButtonEl,
  copyIdentityButtonEl,
  importIdentityButtonEl,
  composerDockEl,
  composerToggleEl,
  settingsToggleEl,
  settingsPanelEl,
  settingsBackdropEl,
  settingsStatusCopyEl,
  clearChildren,
  persistVoiceEnabledPreference,
  stopVoicePlayback,
  getCurrentSessionId,
  onSwitchSession: (sessionId) => switchToSession(sessionId),
  onChooseMessage: (message) => sendMessage(message),
  DEFAULT_SPEAKER,
});

const {
  setHistoryOpen,
  setSettingsOpen,
  updateSettingsStatusCopy,
  setInstantText,
  setVoiceEnabled,
  renderSessionList,
  renderHistory,
  renderDialogueTurns,
  renderChoices,
  normalizeDialogueTurns,
  setDialogueCodeSnippet,
  setSendingState,
  setComposerExpanded,
} = uiShellHelpers;

const avatarController = createAvatarController({
  appEl,
  canvasEl: live2dCanvasEl,
  debugOutputEl,
  wait,
});

const voiceLipSyncController = createVoiceLipSyncController({
  voicePlayerEl,
  avatarController,
});
let avatarVoiceEndMotionTimer = 0;

function syncViewportHeight() {
  const viewport = window.visualViewport;
  const height = viewport ? viewport.height : window.innerHeight;
  document.documentElement.style.setProperty("--app-height", `${Math.round(height)}px`);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clearChildren(element) {
  while (element.firstChild) {
    element.removeChild(element.firstChild);
  }
}

function buildDebugPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return payload;
  }

  return {
    final: {
      emotion: payload.emotion || "",
      persona: payload.persona || { active: "" },
      character: payload.character || {},
      scene: payload.scene || {},
      client_mode: payload.client_mode || "",
      client: payload.client || {},
      tool_call: payload.tool_call || null,
      speech: payload.speech || "",
      speech_segments: Array.isArray(payload.speech_segments) ? payload.speech_segments : [],
    },
    retrieval: payload._debug || null,
  };
}

function normalizeSpeechSegmentsForDisplay(payload) {
  if (!Array.isArray(payload?.speech_segments)) {
    return [];
  }
  return payload.speech_segments.map((segment) => String(segment || "").trim()).filter(Boolean).slice(0, 3);
}

function detectLocalTimeOfDay(date = new Date()) {
  const hour = date.getHours();
  if (hour >= 6 && hour < 11) return "morning";
  if (hour >= 11 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 20) return "evening";
  return "night";
}

function inferTimeTag(background) {
  const sample = `${background?.id || ""} ${background?.name || ""}`.toLowerCase();
  if (/night|midnight|深夜|夜晚|夜里|夜/.test(sample)) return "night";
  if (/evening|dusk|sunset|黄昏|傍晚|晚/.test(sample)) return "evening";
  if (/afternoon|noon|午后|下午|白|昼/.test(sample)) return "afternoon";
  if (/morning|sunrise|dawn|清晨|早晨|早上|晨/.test(sample)) return "morning";
  return detectLocalTimeOfDay();
}

function updateClock() {
  const now = new Date();
  timeChipEl.textContent = now.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatModeLabel(mode) {
  const resolved = String(mode || "gal").toLowerCase();
  if (resolved === "pet") return "pet";
  return "gal";
}

function normalizeWebIdentityMode(mode) {
  const resolved = String(mode || "owner").trim().toLowerCase();
  return ["owner", "browser", "invite"].includes(resolved) ? resolved : "owner";
}

function setStatus(status) {
  const resolved = String(status || "idle");
  statusPillEl.textContent = STATUS_LABELS[resolved] || `状态 ${resolved}`;
}

function setMode(mode) {
  state.currentMode = formatModeLabel(mode);
  modePillEl.textContent = state.currentMode;
}

function normalizeAvatarMode(mode) {
  return String(mode || "").trim().toLowerCase() === "live2d" ? "live2d" : "static";
}

function loadAvatarModePreference() {
  const queryMode = new URLSearchParams(window.location.search).get("avatar");
  if (queryMode) {
    return normalizeAvatarMode(queryMode);
  }

  try {
    const stored = getLocalStorage()?.getItem(AVATAR_MODE_STORAGE_KEY);
    return normalizeAvatarMode(stored);
  } catch {
    return "static";
  }
}

function persistAvatarModePreference(mode) {
  try {
    getLocalStorage()?.setItem(AVATAR_MODE_STORAGE_KEY, normalizeAvatarMode(mode));
  } catch {
  }
}

function normalizeAvatarScale(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return DEFAULT_AVATAR_SCALE;
  }
  return Math.max(0.8, Math.min(1.8, numeric));
}

function loadAvatarScalePreference() {
  const queryScale = new URLSearchParams(window.location.search).get("avatar_size");
  if (queryScale) {
    const numeric = Number(queryScale);
    return normalizeAvatarScale(numeric > 10 ? numeric / 100 : numeric);
  }

  try {
    const stored = getLocalStorage()?.getItem(AVATAR_SIZE_STORAGE_KEY);
    if (!stored) {
      return DEFAULT_AVATAR_SCALE;
    }
    return normalizeAvatarScale(stored);
  } catch {
    return DEFAULT_AVATAR_SCALE;
  }
}

function persistAvatarScalePreference(scale) {
  try {
    getLocalStorage()?.setItem(AVATAR_SIZE_STORAGE_KEY, String(normalizeAvatarScale(scale)));
  } catch {
  }
}

function setAvatarScale(scale, options = {}) {
  const normalized = normalizeAvatarScale(scale);
  state.avatarScale = normalized;
  appEl.style.setProperty("--avatar-scale", normalized.toFixed(2));
  if (avatarSizeRangeEl) {
    avatarSizeRangeEl.value = String(Math.round(normalized * 100));
  }
  if (avatarSizeLabelEl) {
    avatarSizeLabelEl.textContent = `${Math.round(normalized * 100)}%`;
  }
  if (options.persist !== false) {
    persistAvatarScalePreference(normalized);
  }
}

function updateAvatarToggle() {
  if (!avatarToggleEl || !avatarToggleLabelEl) {
    return;
  }
  const isLive2d = state.avatarMode === "live2d";
  avatarToggleEl.setAttribute("aria-pressed", String(isLive2d));
  avatarToggleEl.disabled = state.avatarModeLoading;
  avatarToggleLabelEl.textContent = state.avatarModeLoading
    ? "形象 加载中"
    : `形象 ${isLive2d ? "Live2D" : "静态"}`;
}

async function setAvatarMode(mode, options = {}) {
  const nextMode = normalizeAvatarMode(mode);
  const shouldPersist = options.persist !== false;
  state.avatarMode = nextMode;
  state.avatarModeLoading = nextMode === "live2d";
  if (nextMode === "static") {
    cancelAvatarVoiceEndMotion();
    voiceLipSyncController.stop();
  }
  updateAvatarToggle();
  if (shouldPersist) {
    persistAvatarModePreference(nextMode);
  }

  try {
    await avatarController.setMode(nextMode);
    if (nextMode === "live2d" && state.currentVisual?.emotion) {
      await avatarController.showEmotion(state.currentVisual.emotion);
    }
    if (nextMode === "live2d" && !voicePlayerEl.paused && !voicePlayerEl.ended) {
      void voiceLipSyncController.start();
    }
  } catch (error) {
    state.avatarMode = "static";
    if (shouldPersist) {
      persistAvatarModePreference("static");
    }
    console.warn("avatar mode fallback to static", error);
  } finally {
    state.avatarModeLoading = false;
    updateAvatarToggle();
  }
}

function bindAvatarVoiceSync() {
  const start = () => {
    cancelAvatarVoiceEndMotion();
    void voiceLipSyncController.start();
  };
  const stop = (event) => {
    voiceLipSyncController.stop();
    if (event?.type === "ended") {
      scheduleAvatarVoiceEndMotion();
    }
  };

  voicePlayerEl.addEventListener("play", start);
  voicePlayerEl.addEventListener("playing", start);
  voicePlayerEl.addEventListener("pause", stop);
  voicePlayerEl.addEventListener("ended", stop);
  voicePlayerEl.addEventListener("error", stop);
  voicePlayerEl.addEventListener("abort", stop);
  voicePlayerEl.addEventListener("emptied", stop);
}

function cancelAvatarVoiceEndMotion() {
  if (!avatarVoiceEndMotionTimer) {
    return;
  }
  window.clearTimeout(avatarVoiceEndMotionTimer);
  avatarVoiceEndMotionTimer = 0;
}

function scheduleAvatarVoiceEndMotion() {
  cancelAvatarVoiceEndMotion();
  avatarVoiceEndMotionTimer = window.setTimeout(() => {
    avatarVoiceEndMotionTimer = 0;
    if (state.avatarMode !== "live2d") {
      return;
    }
    if (state.voicePlaybackActive || state.voiceQueueItems.size > 0 || !state.voiceStreamComplete) {
      return;
    }
    void avatarController.playEmotionEndMotion();
  }, 160);
}

function scheduleLive2dPreload() {
  const preload = () => {
    void avatarController.preload();
  };
  window.setTimeout(preload, 350);
}

async function copyIdentityBackup() {
  const payload = {
    profileUserId: getCurrentProfileUserId(),
    sessionId: getCurrentSessionId(),
  };
  const token = encodeIdentityBackup(payload);

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(token);
      debugOutputEl.textContent = "已复制身份。";
      return;
    }
  } catch {
  }

  window.prompt("复制这段身份备份", token);
  debugOutputEl.textContent = "已打开身份备份内容。";
}

async function importIdentityBackup() {
  const raw = window.prompt("粘贴之前复制的身份备份");
  if (raw == null) {
    return;
  }

  const parsed = decodeIdentityBackup(raw);
  if (!parsed) {
    debugOutputEl.textContent = "导入失败：这段身份备份无效。";
    return;
  }

  state.identity = parsed;
  persistIdentity();
  setSettingsOpen(false);
  await loadCurrentSessionBundle({ debugMessage: "已导入身份。" });
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = `HTTP ${response.status}`;
    throw new Error(message);
  }
  return response.json();
}

async function readResponseErrorMessage(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    const detail = String(payload?.detail || payload?.message || "").trim();
    return detail || fallback;
  } catch {
    return fallback;
  }
}

function toUserFacingRequestError(error) {
  const message = String(error?.message || "").trim();
  if (!message || /^HTTP \d+$/.test(message)) {
    return ERROR_REPLY;
  }
  return message;
}

function setGiftDropOverlayVisible(flag) {
  if (!giftDropOverlayEl) return;
  giftDropOverlayEl.setAttribute("aria-hidden", String(!flag));
}

function setGiftHandOpen(flag) {
  const shouldOpen =
    Boolean(flag) && Number(state.giftInventory?.totalCount || 0) > 0 && Boolean(giftHandPanelEl && giftHandToggleEl);
  state.giftHandOpen = shouldOpen;
  if (giftHandToggleEl) {
    giftHandToggleEl.setAttribute("aria-expanded", String(shouldOpen));
  }
  if (giftHandPanelEl) {
    giftHandPanelEl.setAttribute("aria-hidden", String(!shouldOpen));
  }
}

function giftStatusLabel(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "internalized") return "已吃掉";
  if (normalized === "kept" || normalized === "saved") return "已留下";
  if (normalized === "rejected") return "已放下";
  return "待处理";
}

function giftActionLabel(asset, action) {
  const normalizedType = String(asset?.asset_type || "").trim().toLowerCase();
  const normalizedAction = String(action || "").trim().toLowerCase();
  if (normalizedType === "image") {
    if (normalizedAction === "keep") return "收进相册";
    if (normalizedAction === "internalize") return "变成场景";
    if (normalizedAction === "observe") return "只看看";
    if (normalizedAction === "remove") return "放下";
    if (normalizedAction === "purge") return "删掉";
  }
  if (normalizedType === "audio") {
    if (normalizedAction === "keep") return "留下";
    if (normalizedAction === "internalize") return "吃掉";
    if (normalizedAction === "remove") return "放下";
    if (normalizedAction === "purge") return "删掉";
  }
  if (normalizedAction === "remove") return "放下";
  if (normalizedAction === "purge") return "删掉";
  if (normalizedAction === "keep") return "留下";
  if (normalizedAction === "internalize") return "吃掉";
  return normalizedAction;
}

function showTransientAssistantLine(text) {
  const speech = String(text || "").trim();
  if (!speech) {
    return;
  }
  setStatus("final");
  speakerTagEl.textContent = "Akane";
  dialogueTextEl.classList.remove("is-pending");
  replaceDialogueTextImmediately(speech);
  setDialogueCodeSnippet("");
  renderChoices([]);
}

function getGiftInventoryLabel() {
  const totalCount = Number(state.giftInventory?.totalCount || 0);
  return totalCount > 0 ? `手边 (${totalCount})` : "手边";
}

function renderGiftHandPanel() {
  if (!giftHandToggleEl || !giftHandPanelEl || !giftHandListEl) return;

  const inventory = state.giftInventory || { items: [], totalCount: 0, overflowCount: 0 };
  const items = Array.isArray(inventory.items) ? inventory.items : [];
  const totalCount = Number(inventory.totalCount || 0);
  const overflowCount = Number(inventory.overflowCount || 0);

  giftHandToggleEl.textContent = getGiftInventoryLabel();
  giftHandToggleEl.hidden = totalCount <= 0;
  if (giftHandSummaryEl) {
    giftHandSummaryEl.textContent =
      totalCount > 0
        ? `最近递到她手边的礼物会先停在这里。现在手边还有 ${totalCount} 件没整理。`
        : "最近递到她手边的礼物会先停在这里。";
  }

  clearChildren(giftHandListEl);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "目前没有待处理的礼物。";
    giftHandListEl.appendChild(empty);
    setGiftHandOpen(false);
  } else {
    for (const item of items) {
      const card = document.createElement("article");
      card.className = "gift-hand-entry";

      const header = document.createElement("div");
      header.className = "gift-hand-entry__header";

      const name = document.createElement("strong");
      name.className = "gift-hand-entry__name";
      name.textContent = String(item.display_name || item.summary || "未命名礼物").trim() || "未命名礼物";

      const status = document.createElement("span");
      status.className = "gift-entry__status";
      status.textContent = giftStatusLabel(item.status);

      header.appendChild(name);
      header.appendChild(status);
      card.appendChild(header);

      const summary = document.createElement("p");
      summary.className = "gift-hand-entry__summary";
      summary.textContent = String(item.summary || "她还没决定怎么处理这份礼物。").trim();
      card.appendChild(summary);

      const actions = document.createElement("div");
      actions.className = "gift-hand-entry__actions";
      const actionBusy = state.giftActionInFlight === item.asset_id;
      actions.appendChild(
        createGiftActionButton({
          assetId: item.asset_id,
          action: "keep",
          label: giftActionLabel(item, "keep"),
          className: "gift-hand-entry__button",
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: item.asset_id,
          action: "internalize",
          label: giftActionLabel(item, "internalize"),
          accent: true,
          className: "gift-hand-entry__button",
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: item.asset_id,
          action: "purge",
          label: giftActionLabel(item, "purge"),
          className: "gift-hand-entry__button",
          disabled: actionBusy || state.sending,
        })
      );
      if (String(item?.asset_type || "").trim().toLowerCase() === "image") {
        actions.appendChild(
          createGiftActionButton({
            assetId: item.asset_id,
            action: "observe",
            label: giftActionLabel(item, "observe"),
            className: "gift-hand-entry__button",
            disabled: actionBusy || state.sending,
          })
        );
      }
      card.appendChild(actions);
      giftHandListEl.appendChild(card);
    }
  }

  if (giftHandOverflowEl) {
    giftHandOverflowEl.hidden = overflowCount <= 0;
    giftHandOverflowEl.textContent =
      overflowCount > 0 ? `还有 ${overflowCount} 件较早的礼物已经先放进储物箱里。` : "";
  }
}

function renderGiftLibrary() {
  if (!giftLibraryEl) return;
  clearChildren(giftLibraryEl);

  if (!state.gifts.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "还没有送给 Akane 的礼物。";
    giftLibraryEl.appendChild(empty);
    return;
  }

  for (const asset of state.gifts) {
    const card = document.createElement("article");
    card.className = "gift-entry";

    const header = document.createElement("div");
    header.className = "gift-entry__header";

    const name = document.createElement("strong");
    name.className = "gift-entry__name";
    name.textContent = String(asset.display_name || asset.origin_name || "未命名礼物").trim() || "未命名礼物";

    const status = document.createElement("span");
    status.className = "gift-entry__status";
    if (asset.status === "internalized") {
      status.classList.add("is-internalized");
    }
    status.textContent = giftStatusLabel(asset.status);

    header.appendChild(name);
    header.appendChild(status);

    if (asset.asset_type === "image" && asset.asset_url) {
      const preview = document.createElement("img");
      preview.className = "gift-entry__preview";
      preview.src = asset.asset_url;
      preview.alt = name.textContent || "图片礼物预览";
      preview.loading = "lazy";
      card.appendChild(preview);
    }

    const meta = document.createElement("div");
    meta.className = "gift-entry__meta";
    const originName = String(asset.origin_name || "").trim();
    const fileSize = Number(asset.file_size || 0);
    const payload = asset?.payload && typeof asset.payload === "object" ? asset.payload : {};
    const collectionName = String(payload.collection_name || "").trim();
    const projectionRole = String(payload.projection_role || "").trim();
    const metaParts = [];
    if (originName) {
      metaParts.push(`原文件: ${originName}`);
    }
    if (asset.asset_type === "image" && collectionName) {
      metaParts.push(`归档: ${collectionName}`);
    }
    if (asset.asset_type === "image" && projectionRole) {
      metaParts.push(`用途: ${projectionRole === "scene" ? "场景候选" : "相册收藏"}`);
    }
    if (fileSize > 0) {
      metaParts.push(`大小: ${(fileSize / (1024 * 1024)).toFixed(fileSize >= 1024 * 1024 ? 1 : 2)} MB`);
    }
    meta.textContent = metaParts.join(" · ");

    card.appendChild(header);
    if (meta.textContent) {
      card.appendChild(meta);
    }

    const actions = document.createElement("div");
    actions.className = "gift-entry__actions";
    const actionBusy = state.giftActionInFlight === asset.asset_id;

    if (asset.status === "pending" || asset.status === "offered") {
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "keep",
          label: giftActionLabel(asset, "keep"),
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "internalize",
          label: giftActionLabel(asset, "internalize"),
          accent: true,
          disabled: actionBusy || state.sending,
        })
      );
      if (String(asset?.asset_type || "").trim().toLowerCase() === "image") {
        actions.appendChild(
          createGiftActionButton({
            assetId: asset.asset_id,
            action: "observe",
            label: giftActionLabel(asset, "observe"),
            disabled: actionBusy || state.sending,
          })
        );
      }
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "purge",
          label: giftActionLabel(asset, "purge"),
          disabled: actionBusy || state.sending,
        })
      );
    } else if (asset.status === "kept" || asset.status === "saved") {
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "internalize",
          label: giftActionLabel(asset, "internalize"),
          accent: true,
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "remove",
          label: giftActionLabel(asset, "remove"),
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "purge",
          label: giftActionLabel(asset, "purge"),
          disabled: actionBusy || state.sending,
        })
      );
    } else if (asset.status === "internalized") {
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "remove",
          label: giftActionLabel(asset, "remove"),
          disabled: actionBusy || state.sending,
        })
      );
      actions.appendChild(
        createGiftActionButton({
          assetId: asset.asset_id,
          action: "purge",
          label: giftActionLabel(asset, "purge"),
          disabled: actionBusy || state.sending,
        })
      );
    }

    if (actions.childElementCount > 0) {
      card.appendChild(actions);
    }

    giftLibraryEl.appendChild(card);
  }
}

function normalizeArtifactContainers(containers) {
  const items = Array.isArray(containers) ? containers : [];
  const byType = new Map(
    items
      .map((container) => [String(container?.container_type || "").trim().toLowerCase(), container])
      .filter(([containerType]) => Boolean(containerType))
  );
  return ARTIFACT_CONTAINER_ORDER.map((containerType) => byType.get(containerType)).filter(Boolean);
}

function getArtifactEmptyCopy(containerType) {
  const normalizedType = String(containerType || "").trim().toLowerCase();
  if (normalizedType === "music_box") {
    return "她的曲库里还没有收好的歌。";
  }
  if (normalizedType === "album") {
    return "她的相册里还没有收好的图片。";
  }
  return "这里还没有被她收好的东西。";
}

function formatArtifactCountLabel(totalCount) {
  const normalizedCount = Math.max(0, Number(totalCount || 0));
  return normalizedCount > 0 ? `已收好 ${normalizedCount} 件` : "还没有收好的东西";
}

function buildArtifactMeta(asset) {
  const payload = getGiftPayload(asset);
  const metaParts = [];
  const originName = String(asset?.origin_name || "").trim();
  const collectionName = String(payload.collection_name || "").trim();
  const projectionRole = String(payload.projection_role || "").trim();
  const fileSize = Number(asset?.file_size || 0);
  if (originName) {
    metaParts.push(`原文件: ${originName}`);
  }
  if (String(asset?.asset_type || "").trim().toLowerCase() === "image" && collectionName) {
    metaParts.push(`归档: ${collectionName}`);
  }
  if (String(asset?.asset_type || "").trim().toLowerCase() === "image" && projectionRole) {
    metaParts.push(`用途: ${projectionRole === "scene" ? "场景候选" : "相册收藏"}`);
  }
  if (fileSize > 0) {
    metaParts.push(`大小: ${(fileSize / (1024 * 1024)).toFixed(fileSize >= 1024 * 1024 ? 1 : 2)} MB`);
  }
  return metaParts.join(" · ");
}

function createArtifactEntry(asset) {
  const entry = document.createElement("article");
  entry.className = "gift-entry artifact-entry";

  const assetType = String(asset?.asset_type || "").trim().toLowerCase();
  const displayName = String(asset?.display_name || asset?.origin_name || "未命名收藏").trim() || "未命名收藏";
  const status = String(asset?.status || "").trim().toLowerCase();
  const payload = getGiftPayload(asset);
  const projectionRole = String(payload?.projection_role || "").trim().toLowerCase();
  const currentBgmId = String(state.currentVisual?.scene?.bgm || "").trim();
  const currentBackgroundId = String(state.currentVisual?.scene?.background || "").trim();
  const resourceId = String(asset?.resource_id || "").trim();

  if (assetType === "image" && asset?.asset_url) {
    const preview = document.createElement("img");
    preview.className = "gift-entry__preview";
    preview.src = asset.asset_url;
    preview.alt = displayName || "图片收藏预览";
    preview.loading = "lazy";
    entry.appendChild(preview);
  }

  const header = document.createElement("div");
  header.className = "gift-entry__header";

  const name = document.createElement("strong");
  name.className = "gift-entry__name";
  name.textContent = displayName;

  const badges = document.createElement("div");
  badges.className = "artifact-entry__badges";

  const typeBadge = document.createElement("span");
  typeBadge.className = "artifact-entry__badge";
  typeBadge.textContent = assetType === "audio" ? "曲目" : assetType === "image" ? "相片" : "收藏";
  badges.appendChild(typeBadge);

  const stateBadge = document.createElement("span");
  stateBadge.className = "artifact-entry__badge artifact-entry__badge--state";

  if (assetType === "audio") {
    if (status === "internalized" && resourceId && resourceId === currentBgmId) {
      stateBadge.classList.add("is-current");
      stateBadge.textContent = "正在播放";
    } else if (status === "internalized") {
      stateBadge.classList.add("is-active");
      stateBadge.textContent = "可播放";
    } else {
      stateBadge.textContent = "已留下";
    }
  } else if (assetType === "image") {
    if (projectionRole === "scene" && resourceId && resourceId === currentBackgroundId) {
      stateBadge.classList.add("is-current");
      stateBadge.textContent = "当前场景";
    } else if (projectionRole === "scene") {
      stateBadge.classList.add("is-active");
      stateBadge.textContent = "场景候选";
    } else {
      stateBadge.textContent = "相册收藏";
    }
  } else {
    stateBadge.textContent = status === "internalized" ? "已收进世界" : "已留下";
  }
  badges.appendChild(stateBadge);

  header.appendChild(name);
  header.appendChild(badges);
  entry.appendChild(header);

  const metaText = buildArtifactMeta(asset);
  if (metaText) {
    const meta = document.createElement("div");
    meta.className = "gift-entry__meta";
    meta.textContent = metaText;
    entry.appendChild(meta);
  }

  return entry;
}

function renderArtifactContainers() {
  if (!artifactContainersEl) return;
  clearChildren(artifactContainersEl);

  const containers = normalizeArtifactContainers(state.artifactContainers);
  if (!containers.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "她的曲库和相册还在慢慢长出来。";
    artifactContainersEl.appendChild(empty);
    return;
  }

  for (const container of containers) {
    const containerType = String(container?.container_type || "").trim().toLowerCase();
    const card = document.createElement("button");
    card.type = "button";
    card.className = "artifact-container-card";
    card.setAttribute("aria-pressed", String(containerType === state.selectedArtifactContainerType));
    if (containerType === state.selectedArtifactContainerType) {
      card.classList.add("is-active");
    }

    const top = document.createElement("div");
    top.className = "artifact-container-card__top";

    const name = document.createElement("strong");
    name.className = "artifact-container-card__name";
    name.textContent = String(container?.container_name || containerType || "收藏");

    const count = document.createElement("span");
    count.className = "artifact-container-card__count";
    count.textContent = formatArtifactCountLabel(container?.total_count);

    top.appendChild(name);
    top.appendChild(count);
    card.appendChild(top);

    const description = document.createElement("p");
    description.className = "artifact-container-card__description";
    description.textContent = String(container?.description || "").trim() || getArtifactEmptyCopy(containerType);
    card.appendChild(description);

    const latestItems = Array.isArray(container?.latest_items) ? container.latest_items : [];
    const preview = document.createElement("p");
    preview.className = "artifact-container-card__preview";
    if (latestItems.length) {
      preview.textContent = `最近收好：${latestItems
        .slice(0, 2)
        .map((item) => String(item?.display_name || item?.origin_name || "未命名收藏").trim() || "未命名收藏")
        .join("、")}`;
    } else {
      preview.textContent = getArtifactEmptyCopy(containerType);
    }
    card.appendChild(preview);

    card.addEventListener("click", () => {
      void selectArtifactContainer(containerType);
    });

    artifactContainersEl.appendChild(card);
  }
}

function renderArtifactContainerDetail() {
  if (!artifactContainerDetailEl) return;
  clearChildren(artifactContainerDetailEl);

  const detail = state.artifactContainerDetail;
  if (!detail || !String(detail?.container_type || "").trim()) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = "点开一个容器后，会在这里看到她已经收好的东西。";
    artifactContainerDetailEl.appendChild(empty);
    return;
  }

  const header = document.createElement("div");
  header.className = "artifact-container-detail__header";

  const titleGroup = document.createElement("div");

  const title = document.createElement("strong");
  title.className = "artifact-container-detail__title";
  title.textContent = String(detail?.container_name || detail?.container_type || "收藏");

  const summary = document.createElement("p");
  summary.className = "artifact-container-detail__summary";
  summary.textContent =
    String(detail?.description || "").trim() ||
    `${title.textContent}里现在一共有 ${Number(detail?.total_count || 0)} 件收藏。`;

  titleGroup.appendChild(title);
  titleGroup.appendChild(summary);

  const count = document.createElement("span");
  count.className = "artifact-container-detail__count";
  count.textContent = formatArtifactCountLabel(detail?.total_count);

  header.appendChild(titleGroup);
  header.appendChild(count);
  artifactContainerDetailEl.appendChild(header);

  const collections = Array.isArray(detail?.collections) ? detail.collections : [];
  if (String(detail?.container_type || "").trim().toLowerCase() === "album" && collections.length) {
    const filters = document.createElement("div");
    filters.className = "artifact-collection-filters";

    const allButton = document.createElement("button");
    allButton.type = "button";
    allButton.className = "artifact-collection-filter";
    if (!String(detail?.container_key || "").trim()) {
      allButton.classList.add("is-active");
    }
    allButton.textContent = `全部 (${Number(detail?.total_count || 0)})`;
    allButton.addEventListener("click", () => {
      void selectArtifactContainer("album", { containerKey: "" });
    });
    filters.appendChild(allButton);

    for (const collection of collections) {
      const key = String(collection?.container_key || "").trim();
      if (!key) {
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "artifact-collection-filter";
      if (key === String(detail?.container_key || "").trim()) {
        button.classList.add("is-active");
      }
      button.textContent = `${String(collection?.container_name || key)} (${Number(collection?.total_count || 0)})`;
      button.addEventListener("click", () => {
        void selectArtifactContainer("album", { containerKey: key });
      });
      filters.appendChild(button);
    }

    artifactContainerDetailEl.appendChild(filters);
  }

  const items = Array.isArray(detail?.items) ? detail.items : [];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "panel-empty";
    empty.textContent = getArtifactEmptyCopy(detail?.container_type);
    artifactContainerDetailEl.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "artifact-entry-list";
  for (const asset of items) {
    list.appendChild(createArtifactEntry(asset));
  }
  artifactContainerDetailEl.appendChild(list);
}

async function loadArtifactContainers() {
  try {
    const payload = await requestJson(
      `/artifacts/containers?preview_limit=2&include_empty=true&user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      {
        cache: "no-store",
      }
    );
    state.artifactContainers = normalizeArtifactContainers(payload?.containers);
    renderArtifactContainers();
    return true;
  } catch (error) {
    state.artifactContainers = [];
    state.artifactContainerDetail = null;
    renderArtifactContainers();
    renderArtifactContainerDetail();
    debugOutputEl.textContent = `收藏容器加载失败：${error}`;
    return false;
  }
}

async function loadArtifactContainerDetail(containerType, { containerKey = "", limit = 50 } = {}) {
  const normalizedType = String(containerType || "").trim().toLowerCase();
  if (!normalizedType) {
    state.artifactContainerDetail = null;
    renderArtifactContainerDetail();
    return false;
  }

  try {
    const query = new URLSearchParams({
      container_type: normalizedType,
      limit: String(limit),
      user_id: getCurrentSessionId(),
      real_user_id: getCurrentProfileUserId(),
      t: String(Date.now()),
    });
    if (String(containerKey || "").trim()) {
      query.set("container_key", String(containerKey || "").trim());
    }
    const payload = await requestJson(`/artifacts/container?${query.toString()}`, {
      cache: "no-store",
    });
    state.artifactContainerDetail = payload && typeof payload === "object" ? payload : null;
    renderArtifactContainerDetail();
    return true;
  } catch (error) {
    state.artifactContainerDetail = null;
    renderArtifactContainerDetail();
    debugOutputEl.textContent = `收藏详情加载失败：${error}`;
    return false;
  }
}

async function refreshArtifactViews({ preserveSelection = true } = {}) {
  const previousType = preserveSelection ? String(state.selectedArtifactContainerType || "").trim().toLowerCase() : "";
  const previousKey = preserveSelection ? String(state.selectedArtifactContainerKey || "").trim() : "";
  const overviewLoaded = await loadArtifactContainers();
  if (!overviewLoaded) {
    return false;
  }

  const containers = normalizeArtifactContainers(state.artifactContainers);
  const activeContainer =
    (previousType && containers.find((container) => String(container?.container_type || "").trim().toLowerCase() === previousType)) ||
    containers.find((container) => Number(container?.total_count || 0) > 0) ||
    containers[0] ||
    null;

  state.selectedArtifactContainerType = String(activeContainer?.container_type || "").trim().toLowerCase() || "";
  state.selectedArtifactContainerKey = previousKey;
  renderArtifactContainers();

  if (!state.selectedArtifactContainerType) {
    state.artifactContainerDetail = null;
    renderArtifactContainerDetail();
    return true;
  }

  return loadArtifactContainerDetail(state.selectedArtifactContainerType, {
    containerKey: state.selectedArtifactContainerKey,
  });
}

async function selectArtifactContainer(containerType, { containerKey = "" } = {}) {
  const normalizedType = String(containerType || "").trim().toLowerCase();
  if (!normalizedType) {
    return false;
  }
  state.selectedArtifactContainerType = normalizedType;
  state.selectedArtifactContainerKey = String(containerKey || "").trim();
  renderArtifactContainers();
  return loadArtifactContainerDetail(normalizedType, {
    containerKey: state.selectedArtifactContainerKey,
  });
}

function createGiftActionButton({ assetId, action, label, accent = false, disabled = false, className = "gift-entry__button" }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `${className}${accent ? ` ${className}--accent` : ""}`;
  button.textContent = label;
  button.disabled = disabled;
  button.addEventListener("click", () => {
    if (String(action || "").trim().toLowerCase() === "observe") {
      void observeGiftImage(assetId);
      return;
    }
    void applyGiftAction(assetId, action);
  });
  return button;
}

async function loadGiftInventory() {
  try {
    const payload = await requestJson(
      `/gifts/inventory?scope=pending_recent&limit=3&user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      {
        cache: "no-store",
      }
    );
    state.giftInventory = {
      items: Array.isArray(payload?.items) ? payload.items : [],
      totalCount: Number(payload?.total_count || 0),
      overflowCount: Number(payload?.overflow_count || 0),
    };
    renderGiftHandPanel();
    return true;
  } catch (error) {
    state.giftInventory = { items: [], totalCount: 0, overflowCount: 0 };
    renderGiftHandPanel();
    debugOutputEl.textContent = `手边礼物加载失败：${error}`;
    return false;
  }
}

async function loadGiftLibrary() {
  try {
    const payload = await requestJson(
      `/gifts?user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      {
        cache: "no-store",
      }
    );
    state.gifts = Array.isArray(payload?.items) ? payload.items : [];
    renderGiftLibrary();
    return true;
  } catch (error) {
    state.gifts = [];
    renderGiftLibrary();
    debugOutputEl.textContent = `礼物库加载失败：${error}`;
    return false;
  }
}

async function refreshGiftViews() {
  const [libraryLoaded, inventoryLoaded] = await Promise.all([loadGiftLibrary(), loadGiftInventory()]);
  return libraryLoaded && inventoryLoaded;
}

function getGiftPayload(asset) {
  return asset?.payload && typeof asset.payload === "object" ? asset.payload : {};
}

function needsGiftMetadataRefresh(asset) {
  if (String(asset?.asset_type || "").trim().toLowerCase() !== "image") {
    return false;
  }
  const payload = getGiftPayload(asset);
  const visionSummary = String(payload.vision_summary || "").trim();
  const seedName = String(payload.seed_name || "").trim();
  const seedCollectionKey = String(payload.seed_collection_key || "").trim();
  return !visionSummary || !seedName || !seedCollectionKey;
}

function collectGiftMetadataRefreshAssetIds(targetAssetIds = []) {
  const normalizedTargets = Array.from(
    new Set(
      (Array.isArray(targetAssetIds) ? targetAssetIds : [])
        .map((assetId) => String(assetId || "").trim())
        .filter(Boolean)
    )
  );
  const targetSet = normalizedTargets.length ? new Set(normalizedTargets) : null;
  const pendingAssetIds = [];
  const seenAssetIds = new Set();

  for (const asset of Array.isArray(state.gifts) ? state.gifts : []) {
    const assetId = String(asset?.asset_id || "").trim();
    if (!assetId) {
      continue;
    }
    if (targetSet && !targetSet.has(assetId)) {
      continue;
    }
    seenAssetIds.add(assetId);
    if (needsGiftMetadataRefresh(asset)) {
      pendingAssetIds.push(assetId);
    }
  }
  if (targetSet) {
    for (const assetId of targetSet) {
      if (!seenAssetIds.has(assetId)) {
        pendingAssetIds.push(assetId);
      }
    }
  }
  return pendingAssetIds;
}

function cancelGiftMetadataRefresh() {
  state.giftMetadataRefreshToken += 1;
}

async function runGiftMetadataRefresh({ token, targetAssetIds, profileUserId, sessionId }) {
  let pendingAssetIds = Array.from(targetAssetIds);
  for (let attempt = 0; attempt < GIFT_METADATA_REFRESH_MAX_ATTEMPTS; attempt += 1) {
    await wait(GIFT_METADATA_REFRESH_DELAY_MS);
    if (token !== state.giftMetadataRefreshToken) {
      return;
    }
    if (profileUserId !== getCurrentProfileUserId() || sessionId !== getCurrentSessionId()) {
      return;
    }

    const refreshed = await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    if (token !== state.giftMetadataRefreshToken) {
      return;
    }
    if (!refreshed) {
      continue;
    }
    pendingAssetIds = collectGiftMetadataRefreshAssetIds(pendingAssetIds);
    if (!pendingAssetIds.length) {
      return;
    }
  }
}

function scheduleGiftMetadataRefresh(targetAssetIds = []) {
  const pendingAssetIds = collectGiftMetadataRefreshAssetIds(targetAssetIds);
  if (!pendingAssetIds.length) {
    return;
  }
  const token = state.giftMetadataRefreshToken + 1;
  state.giftMetadataRefreshToken = token;
  void runGiftMetadataRefresh({
    token,
    targetAssetIds: pendingAssetIds,
    profileUserId: getCurrentProfileUserId(),
    sessionId: getCurrentSessionId(),
  });
}

function isSupportedGiftAudioFile(file) {
  if (!file) return false;
  const name = String(file.name || "").trim().toLowerCase();
  return GIFT_AUDIO_EXTENSIONS.some((extension) => name.endsWith(extension));
}

function isSupportedGiftImageFile(file) {
  if (!file) return false;
  const name = String(file.name || "").trim().toLowerCase();
  return GIFT_IMAGE_EXTENSIONS.some((extension) => name.endsWith(extension));
}

function isSupportedGiftFile(file) {
  return isSupportedGiftAudioFile(file) || isSupportedGiftImageFile(file);
}

function eventHasFileDrag(event) {
  const types = Array.from(event?.dataTransfer?.types || []);
  return types.includes("Files");
}

function extractDroppedGiftFile(event) {
  const files = Array.from(event?.dataTransfer?.files || []);
  return files.find((file) => isSupportedGiftFile(file)) || null;
}

async function uploadGiftFile(file) {
  if (!file) {
    return false;
  }

  try {
    const response = await fetch(
      `/gifts/upload?user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      {
        method: "POST",
        cache: "no-store",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Akane-Filename": encodeURIComponent(file.name || "gift-file"),
        },
        body: file,
      }
    );
    if (!response.ok) {
      throw new Error(await readResponseErrorMessage(response));
    }
    const payload = await response.json();
    await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    setSettingsOpen(true);
    setGiftHandOpen(true);
    if (settingsStatusCopyEl) {
      settingsStatusCopyEl.textContent = String(payload?.assistant_line || "礼物已经送到 Akane 手边了。");
    }
    showTransientAssistantLine(payload?.assistant_line || "");
    debugOutputEl.textContent = JSON.stringify(payload?.asset || {}, null, 2);
    if (String(payload?.asset?.asset_type || "").trim().toLowerCase() === "image") {
      scheduleGiftMetadataRefresh([payload?.asset?.asset_id]);
    }
    return true;
  } catch (error) {
    debugOutputEl.textContent = `送礼失败：${error}`;
    return false;
  }
}

async function applyGiftAction(assetId, action) {
  const normalizedAssetId = String(assetId || "").trim();
  const normalizedAction = String(action || "").trim().toLowerCase();
  if (!normalizedAssetId || !normalizedAction || state.giftActionInFlight) {
    return false;
  }

  state.giftActionInFlight = normalizedAssetId;
  renderGiftLibrary();
  renderGiftHandPanel();
  try {
    const response = await fetch(`/gifts/action?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        user_id: getCurrentSessionId(),
        real_user_id: getCurrentProfileUserId(),
        asset_id: normalizedAssetId,
        action: normalizedAction,
      }),
    });
    if (!response.ok) {
      throw new Error(await readResponseErrorMessage(response));
    }
    const payload = await response.json();
    await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    if (payload?.manifest_refresh_required) {
      await fetchManifest(true);
    }
    if (settingsStatusCopyEl) {
      settingsStatusCopyEl.textContent = String(payload?.assistant_line || "Akane 已经处理了这份礼物。");
    }
    showTransientAssistantLine(payload?.assistant_line || "");
    debugOutputEl.textContent = JSON.stringify(payload?.asset || {}, null, 2);
    if (String(payload?.asset?.asset_type || "").trim().toLowerCase() === "image") {
      scheduleGiftMetadataRefresh([payload?.asset?.asset_id]);
    }
    return true;
  } catch (error) {
    debugOutputEl.textContent = `礼物处理失败：${error}`;
    return false;
  } finally {
    state.giftActionInFlight = "";
    renderGiftLibrary();
    renderGiftHandPanel();
  }
}

async function observeGiftImage(assetId) {
  const normalizedAssetId = String(assetId || "").trim();
  if (!normalizedAssetId || state.giftActionInFlight) {
    return false;
  }

  state.giftActionInFlight = normalizedAssetId;
  renderGiftLibrary();
  renderGiftHandPanel();
  try {
    const response = await fetch(`/gifts/observe?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        user_id: getCurrentSessionId(),
        real_user_id: getCurrentProfileUserId(),
        asset_id: normalizedAssetId,
      }),
    });
    if (!response.ok) {
      throw new Error(await readResponseErrorMessage(response));
    }
    const payload = await response.json();
    await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    if (settingsStatusCopyEl) {
      settingsStatusCopyEl.textContent = String(payload?.assistant_line || "Akane 已经看过这张图了。");
    }
    showTransientAssistantLine(payload?.assistant_line || "");
    debugOutputEl.textContent = JSON.stringify(
      {
        type: "gift_observe",
        asset_id: normalizedAssetId,
        observation: payload?.observation || {},
      },
      null,
      2
    );
    return true;
  } catch (error) {
    debugOutputEl.textContent = `图片查看失败：${error}`;
    return false;
  } finally {
    state.giftActionInFlight = "";
    renderGiftLibrary();
    renderGiftHandPanel();
  }
}

async function fetchCurrentSessionBundle({ displayTitle = "" } = {}) {
  return requestJson(`/sessions/ensure?t=${Date.now()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({
      user_id: getCurrentSessionId(),
      real_user_id: getCurrentProfileUserId(),
      display_title: displayTitle,
    }),
  });
}

function isExpectedSessionStillActive(expectedProfileUserId, expectedSessionId) {
  return (
    String(expectedProfileUserId || "").trim() === getCurrentProfileUserId() &&
    String(expectedSessionId || "").trim() === getCurrentSessionId()
  );
}

async function syncCurrentSessionAfterTurn({
  expectedProfileUserId = "",
  expectedSessionId = "",
  initialDelayMs = SESSION_SYNC_INITIAL_DELAY_MS,
  retryDelayMs = SESSION_SYNC_RETRY_DELAY_MS,
  retries = 2,
  silent = true,
} = {}) {
  const normalizedProfileUserId = String(expectedProfileUserId || "").trim();
  const normalizedSessionId = String(expectedSessionId || "").trim();
  if (!normalizedProfileUserId || !normalizedSessionId) {
    return false;
  }

  let lastError = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const waitMs = attempt === 0 ? initialDelayMs : retryDelayMs;
    if (waitMs > 0) {
      await wait(waitMs);
    }

    if (!isExpectedSessionStillActive(normalizedProfileUserId, normalizedSessionId)) {
      return false;
    }

    try {
      const bundle = await fetchCurrentSessionBundle();
      if (!isExpectedSessionStillActive(normalizedProfileUserId, normalizedSessionId)) {
        return false;
      }

      const latestPayload =
        bundle?.latest_final_json && typeof bundle.latest_final_json === "object"
          ? bundle.latest_final_json
          : null;
      if (!latestPayload) {
        continue;
      }

      await applySessionBundle(bundle);
      if (!silent) {
        debugOutputEl.textContent = "已同步当前对话状态。";
      }
      return true;
    } catch (error) {
      lastError = error;
    }
  }

  if (!silent && lastError) {
    debugOutputEl.textContent = `同步当前对话失败：${lastError}`;
  }
  return false;
}

async function renameCurrentSession() {
  const current = getCurrentSessionRecord();
  if (!current) {
    return;
  }
  const currentTitle = current?.displayTitle || "新的对话";
  const nextTitle = window.prompt("给这段对话起个名字", currentTitle);
  if (nextTitle == null) {
    return;
  }

  const normalizedTitle = String(nextTitle || "").trim();
  if (!normalizedTitle || normalizedTitle === currentTitle) {
    return;
  }

  try {
    const payload = await requestJson(`/sessions/rename?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        user_id: getCurrentSessionId(),
        real_user_id: getCurrentProfileUserId(),
        display_title: normalizedTitle,
      }),
    });

    state.sessions = Array.isArray(payload?.sessions)
      ? payload.sessions.map(normalizeSessionRecord).filter(Boolean)
      : state.sessions;
    renderSessionList();
    debugOutputEl.textContent = "已重命名当前对话。";
  } catch (error) {
    debugOutputEl.textContent = `重命名失败：${error}`;
  }
}

function findById(items, id) {
  if (!Array.isArray(items) || !id) return null;
  return items.find((item) => item?.id === id) || null;
}

function normalizeAssetUrl(path) {
  const normalized = String(path || "").trim();
  if (!normalized) return "";
  try {
    return encodeURI(normalized);
  } catch {
    return normalized;
  }
}

function buildManifestIndex(manifest) {
  const scenesByKey = new Map();
  const outfitsById = new Map();

  for (const major of manifest?.scenes?.majors || []) {
    for (const minor of major.minors || []) {
      scenesByKey.set(`${major.id}::${minor.id}`, { major, minor });
    }
  }

  for (const outfit of manifest?.characters?.outfits || []) {
    outfitsById.set(outfit.id, outfit);
  }

  return {
    scenesByKey,
    outfitsById,
    defaults: manifest?.defaults || {},
  };
}

function getDefaultVisual() {
  const defaults = state.manifestIndex?.defaults || {};
  return {
    scene: {
      major: defaults.major || "",
      minor: defaults.minor || "",
      background: defaults.background || "",
      bgm: defaults.bgm || "",
    },
    character: {
      outfit: defaults.outfit || "",
    },
    emotion: defaults.emotion || "",
  };
}

function resolveVisualState(payload) {
  const fallback = getDefaultVisual();
  const merged = {
    scene: {
      ...fallback.scene,
      ...(payload?.scene || {}),
    },
    character: {
      ...fallback.character,
      ...(payload?.character || {}),
    },
    emotion: String(payload?.emotion || fallback.emotion || ""),
  };

  if (!state.manifestIndex) {
    return {
      currentVisual: merged,
      backgroundPath: FALLBACK_BACKGROUND_PATH,
      emotionPath: FALLBACK_SPRITE_PATH,
      sceneLabel: "默认场景",
      backgroundLabel: "黄昏",
      emotionLabel: merged.emotion || "平静",
      bgmLabel: "BGM 未设置",
      bgmEntry: null,
      timeTag: detectLocalTimeOfDay(),
    };
  }

  const defaultScene = state.manifestIndex.scenesByKey.get(`${fallback.scene.major}::${fallback.scene.minor}`);
  const sceneBundle =
    state.manifestIndex.scenesByKey.get(`${merged.scene.major}::${merged.scene.minor}`) ||
    defaultScene ||
    Array.from(state.manifestIndex.scenesByKey.values())[0] ||
    { major: null, minor: null };
  const major = sceneBundle.major;
  const minor = sceneBundle.minor;

  const defaultOutfit = state.manifestIndex.outfitsById.get(fallback.character.outfit);
  const outfit =
    state.manifestIndex.outfitsById.get(merged.character.outfit) ||
    defaultOutfit ||
    Array.from(state.manifestIndex.outfitsById.values())[0] ||
    null;

  const background =
    findById(minor?.backgrounds, merged.scene.background) ||
    findById(minor?.backgrounds, fallback.scene.background) ||
    minor?.backgrounds?.[0] ||
    null;

  const emotion =
    findById(outfit?.emotions, merged.emotion) ||
    findById(outfit?.emotions, fallback.emotion) ||
    outfit?.emotions?.[0] ||
    null;

  const bgmEntry =
    findById(minor?.bgm_tracks, merged.scene.bgm) ||
    findById(minor?.bgm_tracks, background?.id) ||
    findById(minor?.bgm_tracks, fallback.scene.bgm) ||
    minor?.bgm_tracks?.[0] ||
    null;

  return {
    currentVisual: {
      scene: {
        major: major?.id || merged.scene.major || "",
        minor: minor?.id || merged.scene.minor || "",
        background: background?.id || merged.scene.background || "",
        bgm: bgmEntry?.id || merged.scene.bgm || "",
      },
      character: {
        outfit: outfit?.id || merged.character.outfit || "",
      },
      emotion: emotion?.id || merged.emotion || "",
    },
    backgroundPath: background?.path || "",
    emotionPath: emotion?.path || "",
    sceneLabel: major && minor ? `${major.name} / ${minor.name}` : "场景未定",
    backgroundLabel: background?.name || background?.id || "背景未定",
    emotionLabel: emotion?.name || emotion?.id || merged.emotion || "平静",
    bgmLabel: bgmEntry ? `BGM ${bgmEntry.name || bgmEntry.id}` : "BGM 未设置",
    bgmEntry,
    timeTag: inferTimeTag(background),
  };
}

function resetStreamedDialogueText() {
  state.typingToken += 1;
  state.streamSpeechText = "";
  state.streamTypewriterQueue = [];
  state.streamTypewriterTask = null;
  dialogueTextEl.textContent = "";
}

async function drainStreamTypewriter(localToken) {
  while (localToken === state.typingToken && state.streamTypewriterQueue.length) {
    const nextChar = state.streamTypewriterQueue.shift();
    state.streamSpeechText += nextChar;
    dialogueTextEl.textContent = state.streamSpeechText;
    await wait(18);
  }

  if (localToken === state.typingToken) {
    state.streamTypewriterTask = null;
  }
}

function appendStreamedDialogueText(text) {
  const normalized = String(text || "");
  if (!normalized) {
    return;
  }

  if (state.instantText) {
    state.streamSpeechText += normalized;
    dialogueTextEl.textContent = state.streamSpeechText;
    return;
  }

  state.streamTypewriterQueue.push(...normalized);
  if (!state.streamTypewriterTask) {
    const localToken = state.typingToken;
    state.streamTypewriterTask = drainStreamTypewriter(localToken);
  }
}

function replaceDialogueTextImmediately(text) {
  state.typingToken += 1;
  state.streamTypewriterQueue = [];
  state.streamTypewriterTask = null;
  state.streamSpeechText = String(text || "").trim();
  dialogueTextEl.textContent = state.streamSpeechText;
}

async function waitForStreamedTyping() {
  while (state.streamTypewriterTask) {
    await state.streamTypewriterTask;
  }
}

async function applyStreamEmotion(emotion) {
  const normalized = String(emotion || "").trim();
  if (!normalized) {
    return;
  }

  const visualSeed = state.currentVisual
    ? {
        scene: { ...(state.currentVisual.scene || {}) },
        character: { ...(state.currentVisual.character || {}) },
        emotion: state.currentVisual.emotion || "",
      }
    : getDefaultVisual();

  visualSeed.emotion = normalized;
  const resolved = resolveVisualState(visualSeed);
  state.currentVisual = resolved.currentVisual;
  emotionPillEl.textContent = `情绪 ${resolved.emotionLabel}`;
  setSpriteImage(resolved.emotionPath);
  void avatarController.showEmotion(resolved.currentVisual.emotion || normalized);
  persistShellState();
}

function setBackgroundImage(path, timeTag) {
  appEl.dataset.time = timeTag || detectLocalTimeOfDay();
  const assetPath = normalizeAssetUrl(path);
  if (!assetPath || state.currentBackgroundPath === assetPath) {
    return;
  }

  const nextIndex = state.activeBgIndex === 0 ? 1 : 0;
  const nextFrame = bgFrames[nextIndex];
  const currentFrame = bgFrames[state.activeBgIndex];
  nextFrame.style.backgroundImage = `url("${assetPath}")`;
  nextFrame.classList.add("is-visible");
  currentFrame.classList.remove("is-visible");
  state.activeBgIndex = nextIndex;
  state.currentBackgroundPath = assetPath;
}

function setSpriteImage(path) {
  const assetPath = normalizeAssetUrl(path);
  if (!assetPath || state.currentSpritePath === assetPath) {
    if (!assetPath) {
      spriteEl.removeAttribute("src");
      state.currentSpritePath = "";
      spriteEl.classList.remove("is-ready");
    }
    return;
  }

  spriteEl.classList.remove("is-ready");
  spriteEl.src = assetPath;
  state.currentSpritePath = assetPath;
  requestAnimationFrame(() => {
    spriteEl.classList.add("is-ready");
  });
}

async function typewrite(text) {
  state.typingToken += 1;
  const localToken = state.typingToken;
  const normalized = String(text || "").trim();
  if (state.instantText || !normalized) {
    dialogueTextEl.textContent = normalized;
    return;
  }

  dialogueTextEl.textContent = "";
  for (let index = 0; index < normalized.length; index += 1) {
    if (localToken !== state.typingToken) return;
    dialogueTextEl.textContent = normalized.slice(0, index + 1);
    await wait(18);
  }
}

function resolveFocusTurnsForDisplay(turns, speechSegments, fallbackTurn) {
  if (speechSegments.length > 1) {
    const sliced = turns.slice(Math.max(0, turns.length - speechSegments.length));
    if (sliced.length === speechSegments.length) {
      return sliced;
    }
    return speechSegments.map((speech) => ({
      speaker: DEFAULT_SPEAKER,
      speech,
      codeSnippet: "",
    }));
  }
  return [fallbackTurn].filter((turn) => turn && String(turn.speech || "").trim());
}

async function playFocusTurnsForDisplay(turns, options = {}) {
  const normalizedTurns = turns
    .map((turn) => ({
      speaker: String(turn?.speaker || DEFAULT_SPEAKER).trim() || DEFAULT_SPEAKER,
      speech: String(turn?.speech || "").trim(),
      codeSnippet: String(turn?.codeSnippet || turn?.code_snippet || "").trim(),
    }))
    .filter((turn) => turn.speech);
  if (!normalizedTurns.length) {
    return;
  }

  dialogueTextEl.classList.remove("is-pending");
  if (options.skipTypewrite) {
    const lastTurn = normalizedTurns[normalizedTurns.length - 1];
    speakerTagEl.textContent = lastTurn.speaker;
    replaceDialogueTextImmediately(normalizedTurns.map((turn) => turn.speech).join("\n"));
    setDialogueCodeSnippet(lastTurn.codeSnippet);
    return;
  }

  for (const [index, turn] of normalizedTurns.entries()) {
    speakerTagEl.textContent = turn.speaker;
    setDialogueCodeSnippet(index === normalizedTurns.length - 1 ? turn.codeSnippet : "");
    await typewrite(turn.speech);
    if (index < normalizedTurns.length - 1) {
      await wait(Math.min(720, Math.max(420, turn.speech.length * 18)));
    }
  }
}

function pushHistoryEntries(entries) {
  for (const entry of entries) {
    const speaker = String(entry?.speaker || "").trim();
    const content = String(entry?.content || "").trim();
    const codeSnippet = String(entry?.codeSnippet || "").trim();
    if (!speaker || !content) continue;

    const normalized = {
      speaker,
      content,
      codeSnippet,
      kind: entry.kind || "neutral",
    };
    const previous = state.history[state.history.length - 1];
    if (
      previous &&
      previous.speaker === normalized.speaker &&
      previous.content === normalized.content &&
      previous.codeSnippet === normalized.codeSnippet &&
      previous.kind === normalized.kind
    ) {
      continue;
    }

    state.history.push(normalized);
  }

  state.history = state.history.slice(-60);
  renderHistory();
}

async function fetchManifest(force = false) {
  if (!force && state.manifest && state.manifestIndex) {
    return state.manifest;
  }

  try {
    const response = await fetch(
      `/resource-manifest?user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      throw new Error(await readResponseErrorMessage(response));
    }
    const manifest = await response.json();
    state.manifest = manifest;
    state.manifestIndex = buildManifestIndex(manifest);
    return manifest;
  } catch (error) {
    debugOutputEl.textContent = `manifest 加载失败：${error}`;
    return null;
  }
}

async function fetchAppConfig() {
  try {
    const response = await fetch(`/app-config?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.streamingTtsEnabled = payload?.streaming_tts_enabled !== false;
    state.webIdentity = {
      mode: normalizeWebIdentityMode(payload?.web_identity_mode),
      ownerProfileUserId:
        String(payload?.web_owner_profile_user_id || OWNER_PROFILE_USER_ID).trim() ||
        OWNER_PROFILE_USER_ID,
    };
  } catch (error) {
    state.streamingTtsEnabled = true;
    state.webIdentity = {
      mode: "owner",
      ownerProfileUserId: OWNER_PROFILE_USER_ID,
    };
    console.warn("app config load failed", error);
  }
}

async function fetchModelServiceConfig() {
  try {
    const response = await fetch(`/control-center/model-service?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.modelService = payload;
    renderModelServiceConfig(payload);
  } catch (error) {
    modelServiceStatusEl.textContent = "模型配置暂不可用";
    modelServiceStatusDetailEl.textContent = String(error);
  }
}

function renderModelServiceConfig(payload = {}) {
  const providers = Array.isArray(payload.providers) ? payload.providers : [];
  modelServiceProviderEl.replaceChildren(
    ...providers.map((provider) => {
      const option = document.createElement("option");
      option.value = String(provider.id || "");
      option.textContent = String(provider.label || provider.id || "");
      option.selected = option.value === String(payload.providerId || "");
      return option;
    })
  );
  modelServiceBaseUrlEl.value = String(payload.baseUrl || "");
  modelServiceApiKeyEl.value = "";
  modelServiceApiKeyEl.placeholder = payload.hasApiKey
    ? "已保存，留空表示继续使用原密钥"
    : selectedModelProvider()?.apiKeyRequired === false
      ? "本地服务通常无需填写"
      : "粘贴服务商提供的 API Key";
  modelServiceChatModelEl.value = String(payload.chatModel || "");
  modelServiceUseVisionEl.checked = payload.useForVision !== false;
  modelServiceStatusEl.textContent = payload.status === "configured" ? "模型服务已配置" : "尚未配置模型服务";
  modelServiceStatusDetailEl.textContent = payload.status === "configured"
    ? `${selectedModelProvider()?.label || payload.providerId || "自定义服务"} · ${payload.chatModel || "未选择模型"}`
    : "选择服务商并测试连接后即可开始对话";
}

function selectedModelProvider() {
  const providers = Array.isArray(state.modelService?.providers) ? state.modelService.providers : [];
  return providers.find((item) => String(item.id || "") === String(modelServiceProviderEl.value || "")) || null;
}

function readModelServicePayload() {
  const provider = selectedModelProvider();
  return {
    providerId: String(modelServiceProviderEl.value || "openai_compatible"),
    protocol: String(provider?.protocol || "openai"),
    baseUrl: String(modelServiceBaseUrlEl.value || "").trim(),
    apiKey: String(modelServiceApiKeyEl.value || "").trim(),
    chatModel: String(modelServiceChatModelEl.value || "").trim(),
    useForVision: Boolean(modelServiceUseVisionEl.checked),
    timeoutSeconds: 120,
  };
}

async function runModelServiceRequest(action) {
  if (state.modelServiceBusy) return;
  state.modelServiceBusy = true;
  setModelServiceButtonsDisabled(true);
  modelServiceMessageEl.textContent =
    action === "models" ? "正在读取模型列表..." : action === "test" ? "正在测试连接..." : "正在保存并应用...";
  try {
    const path = action === "save" ? "" : `/${action}`;
    const response = await fetch(`/control-center/model-service${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(readModelServicePayload()),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.ok) {
      throw new Error(String(result.reason || result.status || `HTTP ${response.status}`));
    }
    if (action === "models") {
      const models = Array.isArray(result.models) ? result.models : [];
      modelServiceModelListEl.replaceChildren(
        ...models.map((model) => {
          const option = document.createElement("option");
          option.value = String(model);
          return option;
        })
      );
      if (!modelServiceChatModelEl.value && models.length) {
        modelServiceChatModelEl.value = String(models[0]);
      }
      modelServiceMessageEl.textContent = models.length ? `发现 ${models.length} 个模型。` : "服务未返回模型列表，请手动填写模型名。";
    } else if (action === "test") {
      modelServiceMessageEl.textContent = `连接成功，模型返回：${String(result.message || "OK")}`;
    } else {
      state.modelService = result;
      renderModelServiceConfig(result);
      modelServiceMessageEl.textContent = "已保存并立即生效。";
    }
  } catch (error) {
    modelServiceMessageEl.textContent = `操作失败：${String(error?.message || error)}`;
  } finally {
    state.modelServiceBusy = false;
    setModelServiceButtonsDisabled(false);
  }
}

function setModelServiceButtonsDisabled(disabled) {
  for (const button of [modelServiceModelsEl, modelServiceTestEl, modelServiceSaveEl]) {
    if (button) button.disabled = Boolean(disabled);
  }
}

async function applyPayload(payload, options = {}) {
  const resolved = resolveVisualState(payload);
  state.currentVisual = resolved.currentVisual;
  renderArtifactContainers();
  renderArtifactContainerDetail();

  setMode(payload?.mode || "gal");
  setStatus(payload?.status || "final");
  scenePillEl.textContent = resolved.sceneLabel;
  bgmPillEl.textContent = resolved.bgmLabel;
  emotionPillEl.textContent = `情绪 ${resolved.emotionLabel}`;
  subtitleEl.textContent = resolved.sceneLabel;

  setBackgroundImage(resolved.backgroundPath, resolved.timeTag);
  setSpriteImage(resolved.emotionPath);
  void avatarController.showEmotion(resolved.currentVisual.emotion);
  await updateBgm(resolved.bgmEntry, resolved.bgmLabel);

  const turns = normalizeDialogueTurns(payload);
  renderDialogueTurns(turns);
  renderChoices(payload?.choices || []);
  const speechSegments = normalizeSpeechSegmentsForDisplay(payload);
  const aggregateSpeech = String(payload?.speech || speechSegments.join("\n") || FALLBACK_REPLY).trim();

  const focusTurn = turns[turns.length - 1] || {
    speaker: DEFAULT_SPEAKER,
    speech: aggregateSpeech,
    codeSnippet: String(payload?.code_snippet || "").trim(),
  };
  const focusTurns = resolveFocusTurnsForDisplay(turns, speechSegments, focusTurn);
  const focusSpeech = focusTurns.map((turn) => turn.speech).filter(Boolean).join("\n") || aggregateSpeech;
  if (options.playVoice !== false) {
    void speakText(focusSpeech);
  }
  await playFocusTurnsForDisplay(focusTurns, { skipTypewrite: options.skipTypewrite });

  if (options.recordHistory !== false) {
    pushHistoryEntries(
      turns.map((turn) => ({
        speaker: turn.speaker,
        content: turn.speech,
        codeSnippet: turn.codeSnippet,
        kind: turn.speaker === USER_SPEAKER ? "user" : "neutral",
      }))
    );
  }

  persistShellState();
}

async function consumeNdjsonStream(response, onEvent) {
  if (!response.body) {
    throw new Error("浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    while (true) {
      const newlineIndex = buffer.indexOf("\n");
      if (newlineIndex < 0) {
        break;
      }

      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) {
        continue;
      }

      let payload = null;
      try {
        payload = JSON.parse(line);
      } catch {
        continue;
      }
      await onEvent(payload);
    }
  }

  const tail = buffer.trim();
  if (!tail) {
    return;
  }

  const payload = JSON.parse(tail);
  await onEvent(payload);
}

async function handleThinkStreamEvent(event, streamState = null) {
  const eventType = String(event?.type || "").trim().toLowerCase();
  if (!eventType) {
    return null;
  }

  if (eventType === "stream_start") {
    if (streamState) {
      streamState.started = true;
    }
    return null;
  }

  if (eventType === "turn_start") {
    flushBufferedSpeechSegments(true);
    speakerTagEl.textContent = String(event?.speaker || DEFAULT_SPEAKER).trim() || DEFAULT_SPEAKER;
    dialogueTextEl.classList.remove("is-pending");
    resetStreamedDialogueText();
    setDialogueCodeSnippet("");
    return null;
  }

  if (eventType === "ui") {
    await applyStreamEmotion(event?.emotion);
    return null;
  }

  if (eventType === "speech_chunk") {
    const text = String(event?.text || "");
    if (!text) {
      return null;
    }
    if (streamState) {
      streamState.hadPartialSpeech = true;
    }
    dialogueTextEl.classList.remove("is-pending");
    appendStreamedDialogueText(text);
    if (state.streamingTtsEnabled) {
      appendSpeechForTts(text);
    }
    return null;
  }

  if (eventType === "npc_turn") {
    const speaker = String(event?.speaker || "NPC").trim() || "NPC";
    const speech = String(event?.speech || "").trim();
    flushBufferedSpeechSegments(true);
    speakerTagEl.textContent = speaker;
    dialogueTextEl.classList.remove("is-pending");
    replaceDialogueTextImmediately(speech);
    setDialogueCodeSnippet("");
    enqueueDirectSpeech(speech);
    void pumpVoiceQueue();
    return null;
  }

  if (eventType === "final") {
    return event?.payload || null;
  }

  if (eventType === "final_ui") {
    if (state.streamingTtsEnabled) {
      flushBufferedSpeechSegments(true);
      state.voiceStreamComplete = true;
      void pumpVoiceQueue();
      void finalizeStreamAudioIfIdle();
      scheduleAvatarVoiceEndMotion();
    }
    return event?.payload || null;
  }

  if (eventType === "reminder_set") {
    const dueLabel = String(event?.due_label || "").trim();
    const content = String(event?.content || "").trim();
    if (dueLabel && content) {
      debugOutputEl.textContent = `提醒已设置：${dueLabel} -> ${content}`;
    }
    return null;
  }

  if (eventType === "reminder_list") {
    const items = Array.isArray(event?.items) ? event.items : [];
    debugOutputEl.textContent = JSON.stringify({ type: "reminder_list", items }, null, 2);
    return null;
  }

  if (eventType === "reminder_cancelled") {
    const dueLabel = String(event?.due_label || "").trim();
    const content = String(event?.content || "").trim();
    if (content) {
      debugOutputEl.textContent = `提醒已取消：${dueLabel ? `${dueLabel} -> ` : ""}${content}`;
    }
    return null;
  }

  if (eventType === "inventory_snapshot") {
    const items = Array.isArray(event?.items) ? event.items : [];
    const scope = String(event?.scope || "").trim() || "pending_recent";
    debugOutputEl.textContent = JSON.stringify(
      {
        type: "inventory_snapshot",
        scope,
        total_count: Number(event?.total_count || 0),
        overflow_count: Number(event?.overflow_count || 0),
        items,
      },
      null,
      2
    );
    await refreshGiftViews();
    return null;
  }

  if (eventType === "gift_updated") {
    const asset = event?.asset && typeof event.asset === "object" ? event.asset : null;
    const action = String(event?.action || "").trim().toLowerCase();
    await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    if (action === "internalize" || action === "remove" || action === "purge") {
      await fetchManifest(true);
    }
    if (asset) {
      debugOutputEl.textContent = JSON.stringify(
        {
          type: "gift_updated",
          action,
          asset,
        },
        null,
        2
      );
      if (String(asset?.asset_type || "").trim().toLowerCase() === "image") {
        scheduleGiftMetadataRefresh([asset.asset_id]);
      }
    }
    return null;
  }

  if (eventType === "artifact_updated") {
    const asset = event?.asset && typeof event.asset === "object" ? event.asset : null;
    const action = String(event?.action || "").trim().toLowerCase();
    const projectionChanged = Boolean(event?.projection_changed);
    await refreshGiftViews();
    await refreshArtifactViews({ preserveSelection: true });
    if (projectionChanged) {
      await fetchManifest(true);
    }
    if (asset) {
      debugOutputEl.textContent = JSON.stringify(
        {
          type: "artifact_updated",
          action,
          projection_changed: projectionChanged,
          asset,
        },
        null,
        2
      );
    }
    return null;
  }

  if (eventType === "persona_state") {
    const card = event?.card && typeof event.card === "object" ? event.card : {};
    const action = String(event?.action || "").trim();
    const name = String(card?.name || card?.card_id || "").trim();
    debugOutputEl.textContent = `人设卡状态：${action}${name ? ` -> ${name}` : ""}`;
    return null;
  }

  if (eventType === "stream_error" || eventType === "error") {
    const message = String(event?.message || "stream failed");
    const partial = event?.partial && typeof event.partial === "object" ? event.partial : null;
    if (streamState) {
      streamState.streamErrored = true;
      streamState.streamErrorMessage = message;
      if (partial?.speech) {
        streamState.hadPartialSpeech = true;
      }
    }
    if (partial?.emotion && !state.currentVisual?.emotion) {
      await applyStreamEmotion(partial.emotion);
    }
    if (partial?.speech && !state.streamSpeechText) {
      replaceDialogueTextImmediately(partial.speech);
    }
    return null;
  }

  if (eventType === "stream_end") {
    if (streamState) {
      streamState.ended = true;
      streamState.endStatus = String(event?.status || "");
      const partial = event?.partial && typeof event.partial === "object" ? event.partial : null;
      if (partial?.speech) {
        streamState.hadPartialSpeech = true;
      }
    }
    return null;
  }

  return null;
}

async function showReminderNotification(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }

  await fetchManifest();
  setStatus("final");
  debugOutputEl.textContent = JSON.stringify(
    {
      reminder_id: payload.reminder_id || "",
      due_ts: payload.due_ts || 0,
      source: payload.source || "reminder",
    },
    null,
    2
  );
  await applyPayload(payload, { playVoice: true, skipTypewrite: false, recordHistory: true });
}

function enqueueReminderNotifications(notifications) {
  if (!Array.isArray(notifications) || !notifications.length) {
    return;
  }
  for (const item of notifications) {
    if (item && typeof item === "object") {
      state.pendingReminderNotifications.push(item);
    }
  }
  void flushReminderNotifications();
}

async function flushReminderNotifications() {
  if (state.flushingReminderNotifications || state.sending) {
    return;
  }

  state.flushingReminderNotifications = true;
  try {
    while (!state.sending && state.pendingReminderNotifications.length) {
      const payload = state.pendingReminderNotifications.shift();
      await showReminderNotification(payload);
      await wait(120);
    }
  } finally {
    state.flushingReminderNotifications = false;
  }
}

async function pollDueReminders() {
  if (state.reminderPollInFlight) {
    return;
  }

  state.reminderPollInFlight = true;
  try {
    const response = await fetch(
      `/reminders/due?user_id=${encodeURIComponent(getCurrentSessionId())}&real_user_id=${encodeURIComponent(getCurrentProfileUserId())}&t=${Date.now()}`,
      {
        cache: "no-store",
      }
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    const notifications = Array.isArray(payload?.notifications) ? payload.notifications : [];
    if (notifications.length) {
      enqueueReminderNotifications(notifications);
    }
  } catch (error) {
    console.warn("poll reminders failed", error);
  } finally {
    state.reminderPollInFlight = false;
  }
}

function unlockAudio() {
  const wasUnlocked = state.audioUnlocked;
  if (!state.audioUnlocked) {
    state.audioUnlocked = true;
  }
  void playBgmWithFade();
  if (wasUnlocked) {
    return;
  }
}

function requestAudioReplay() {
  if (!state.audioUnlocked) {
    return;
  }
  void playBgmWithFade();
}

async function sendMessage(message) {
  const trimmed = String(message || "").trim();
  if (!trimmed || state.sending) return false;

  unlockAudio();
  await stopVoicePlayback();
  await fetchManifest();
  const expectedProfileUserId = getCurrentProfileUserId();
  const expectedSessionId = getCurrentSessionId();

  const preservedInput = trimmed;
  const streamState = {
    started: false,
    ended: false,
    endStatus: "",
    streamErrored: false,
    streamErrorMessage: "",
    hadPartialSpeech: false,
  };
  let timeoutId = null;
  let finalApplied = false;
  setSendingState(true);
  resetStreamedDialogueText();
  setStatus("thinking");
  dialogueTextEl.classList.add("is-pending");
  dialogueTextEl.textContent = THINKING_TEXT;
  speakerTagEl.textContent = DEFAULT_SPEAKER;
  renderChoices([]);
  pushHistoryEntries([{ speaker: USER_SPEAKER, content: trimmed, kind: "user" }]);

  try {
    const controller = new AbortController();
    // The backend may need multiple LLM calls for one turn, so the client-side
    // timeout should be much looser than a single model call timeout.
    timeoutId = setTimeout(() => controller.abort(), THINK_REQUEST_TIMEOUT_MS);
    const response = await fetch(`/think?t=${Date.now()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      signal: controller.signal,
      body: JSON.stringify({
        user_id: expectedSessionId,
        real_user_id: expectedProfileUserId,
        message: trimmed,
        client_mode: "scene_static",
        client_capabilities: [
          "speech_segments",
          "background",
          "bgm",
          "static_sprite",
          "audio_playback",
          "tts",
          "choices",
          "tool_actions",
        ],
        current_visual: state.currentVisual,
      }),
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    let payload = null;
    await consumeNdjsonStream(response, async (event) => {
      const maybeFinalPayload = await handleThinkStreamEvent(event, streamState);
      if (maybeFinalPayload && typeof maybeFinalPayload === "object") {
        payload = maybeFinalPayload;
        if (!finalApplied) {
          finalApplied = true;
          debugOutputEl.textContent = JSON.stringify(buildDebugPayload(payload), null, 2);
          await applyPayload(payload, {
            playVoice: !state.streamingTtsEnabled || !streamState.hadPartialSpeech,
            skipTypewrite: true,
          });
        }
      }
    });

    await waitForStreamedTyping();
    if (state.streamingTtsEnabled) {
      await markVoiceStreamComplete();
      await finalizeStreamAudioIfIdle();
      scheduleAvatarVoiceEndMotion();
    }

    if (!payload || typeof payload !== "object") {
      throw new Error(streamState.streamErrorMessage || "未收到完整的最终结果");
    }

    if (!finalApplied) {
      debugOutputEl.textContent = JSON.stringify(buildDebugPayload(payload), null, 2);
      await applyPayload(payload, {
        playVoice: !state.streamingTtsEnabled || !streamState.hadPartialSpeech,
        skipTypewrite: true,
      });
    }
    void syncCurrentSessionAfterTurn({
      expectedProfileUserId,
      expectedSessionId,
      silent: true,
    });
    return true;
  } catch (error) {
    const isTimeout = error?.name === "AbortError";
    await stopVoicePlayback();
    inputEl.value = preservedInput;
    const shouldAttemptRecovery = Boolean(streamState.started || streamState.hadPartialSpeech);
    if (shouldAttemptRecovery) {
      const recovered = await syncCurrentSessionAfterTurn({
        expectedProfileUserId,
        expectedSessionId,
        silent: true,
        retries: 3,
      });
      if (recovered) {
        debugOutputEl.textContent = "已自动同步到本轮最新状态。";
        return true;
      }
    }
    debugOutputEl.textContent = isTimeout
      ? `请求超时：前端在 ${Math.round(THINK_REQUEST_TIMEOUT_MS / 1000)} 秒后主动中止了等待。`
      : `请求失败：${error}`;
    setStatus("error");
    if (!streamState.hadPartialSpeech) {
      dialogueTextEl.classList.remove("is-pending");
      speakerTagEl.textContent = DEFAULT_SPEAKER;
      await typewrite(isTimeout ? TIMEOUT_REPLY : toUserFacingRequestError(error));
    } else {
      dialogueTextEl.classList.remove("is-pending");
      if (state.streamingTtsEnabled) {
        await finalizeStreamAudioIfIdle();
        scheduleAvatarVoiceEndMotion();
      }
    }
    return false;
  } finally {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
    }
    setSendingState(false);
    void flushReminderNotifications();
  }
}

async function resetScene() {
  if (state.sending) return;
  setSendingState(true);
  try {
    await stopVoicePlayback();
    await fetch("/reset", { method: "POST" });
    state.history = [];
    state.currentVisual = null;
    state.currentMode = "gal";
    state.currentBackgroundPath = "";
    state.currentSpritePath = "";
    state.currentTrackPath = "";
    state.pendingReminderNotifications = [];
    bgmPlayerEl.pause();
    bgmPlayerEl.removeAttribute("src");
    clearPersistedShellState();
    renderHistory();
    renderChoices([]);
    debugOutputEl.textContent = "记忆已重置。";
    await initializeScene();
  } catch (error) {
    debugOutputEl.textContent = `重置失败：${error}`;
  } finally {
    setSendingState(false);
  }
}

async function renderDefaultSessionScene() {
  await fetchManifest(true);
  const defaults = getDefaultVisual();
  setMode("gal");
  setStatus("idle");
  renderChoices([]);
  await applyPayload(
    {
      ...defaults,
      emotion: defaults.emotion,
      speech: DEFAULT_GREETING,
      dialogue_turns: [{ speaker: DEFAULT_SPEAKER, speech: DEFAULT_GREETING }],
      status: "idle",
      mode: "gal",
    },
    { recordHistory: false, playVoice: false }
  );
}

async function applySessionBundle(bundle) {
  cancelGiftMetadataRefresh();
  await fetchManifest();
  state.sessions = Array.isArray(bundle?.sessions)
    ? bundle.sessions.map(normalizeSessionRecord).filter(Boolean)
    : [];
  renderSessionList();

  const messages = Array.isArray(bundle?.messages) ? bundle.messages : [];
  state.history = buildHistoryEntriesFromMessages(messages).slice(-60);
  renderHistory();

  resetStreamedDialogueText();
  setDialogueCodeSnippet("");
  state.pendingReminderNotifications = [];

  const latestPayload =
    bundle?.latest_final_json && typeof bundle.latest_final_json === "object"
      ? bundle.latest_final_json
      : null;

  if (latestPayload) {
    await applyPayload(latestPayload, {
      playVoice: false,
      skipTypewrite: true,
      recordHistory: false,
    });
  } else {
    await renderDefaultSessionScene();
  }

  updateSettingsStatusCopy();
  await refreshGiftViews();
  await refreshArtifactViews({ preserveSelection: true });
  scheduleGiftMetadataRefresh();
}

async function loadCurrentSessionBundle({ displayTitle = "", debugMessage = "" } = {}) {
  try {
    const bundle = await fetchCurrentSessionBundle({ displayTitle });
    await applySessionBundle(bundle);
    if (debugMessage) {
      debugOutputEl.textContent = debugMessage;
    }
    return true;
  } catch (error) {
    state.sessions = [];
    renderSessionList();
    state.history = [];
    renderHistory();
    state.artifactContainers = [];
    state.artifactContainerDetail = null;
    renderArtifactContainers();
    renderArtifactContainerDetail();
    debugOutputEl.textContent = `对话加载失败：${error}`;
    await renderDefaultSessionScene();
    updateSettingsStatusCopy();
    return false;
  }
}

async function startNewSession() {
  if (state.sending) return;

  await stopVoicePlayback();
  rotateSessionIdentity();
  setSettingsOpen(false);
  await loadCurrentSessionBundle({ debugMessage: "已开始新的对话。" });
}

async function switchToSession(sessionId) {
  const normalizedSessionId = String(sessionId || "").trim();
  if (!normalizedSessionId || normalizedSessionId === getCurrentSessionId() || state.sending) {
    return;
  }

  await stopVoicePlayback();
  ensureIdentity();
  state.identity = {
    ...state.identity,
    sessionId: normalizedSessionId,
  };
  persistIdentity();
  setSettingsOpen(false);
  await loadCurrentSessionBundle({ debugMessage: "已切换到选中的对话。" });
}

async function initializeScene() {
  await loadCurrentSessionBundle();
}

chatFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = inputEl.value;
  const ok = await sendMessage(text);
  if (ok) {
    inputEl.value = "";
    setComposerExpanded(false);
  }
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatFormEl.requestSubmit();
  }
});

inputEl.addEventListener("focus", () => {
  setComposerExpanded(true);
  requestAudioReplay();
});

composerToggleEl?.addEventListener("click", () => {
  setComposerExpanded(true, { focus: true });
  requestAudioReplay();
});

document.querySelectorAll(".quick-action[data-message]").forEach((button) => {
  button.addEventListener("click", () => {
    const text = button.dataset.message || "";
    inputEl.value = text;
    void sendMessage(text).then((ok) => {
      if (ok) {
        inputEl.value = "";
      }
    });
  });
});

newSessionButtonEl?.addEventListener("click", () => {
  void startNewSession();
});

renameSessionButtonEl?.addEventListener("click", () => {
  void renameCurrentSession();
});

copyIdentityButtonEl?.addEventListener("click", () => {
  void copyIdentityBackup();
});

importIdentityButtonEl?.addEventListener("click", () => {
  void importIdentityBackup();
});

voiceToggleEl.addEventListener("click", () => {
  setVoiceEnabled(!state.voiceEnabled);
  requestAudioReplay();
});

settingsToggleEl?.addEventListener("click", () => {
  setSettingsOpen(appEl.dataset.settingsOpen !== "true");
  if (appEl.dataset.settingsOpen !== "true") {
    setGiftHandOpen(false);
  }
  requestAudioReplay();
});

settingsCloseEl?.addEventListener("click", () => {
  setSettingsOpen(false);
});

modelServiceProviderEl?.addEventListener("change", () => {
  const provider = selectedModelProvider();
  if (provider?.baseUrl) {
    modelServiceBaseUrlEl.value = String(provider.baseUrl);
  } else if (modelServiceProviderEl.value !== "openai_compatible") {
    modelServiceBaseUrlEl.value = "";
  }
  modelServiceApiKeyEl.placeholder = provider?.apiKeyRequired === false
    ? "本地服务通常无需填写"
    : state.modelService?.hasApiKey
      ? "已保存，留空表示继续使用原密钥"
      : "粘贴服务商提供的 API Key";
  modelServiceMessageEl.textContent = String(provider?.description || "");
});

modelServiceModelsEl?.addEventListener("click", () => {
  void runModelServiceRequest("models");
});

modelServiceTestEl?.addEventListener("click", () => {
  void runModelServiceRequest("test");
});

modelServiceSaveEl?.addEventListener("click", () => {
  void runModelServiceRequest("save");
});

settingsBackdropEl?.addEventListener("click", () => {
  setSettingsOpen(false);
});

giftHandToggleEl?.addEventListener("click", () => {
  setGiftHandOpen(!state.giftHandOpen);
});

giftHandCloseEl?.addEventListener("click", () => {
  setGiftHandOpen(false);
});

giftHandOpenLibraryEl?.addEventListener("click", () => {
  setGiftHandOpen(false);
  setSettingsOpen(true);
});

historyToggleEl.addEventListener("click", () => {
  setHistoryOpen(appEl.dataset.historyOpen !== "true");
  requestAudioReplay();
});

historyCloseEl.addEventListener("click", () => {
  setHistoryOpen(false);
});

instantToggleEl.addEventListener("click", () => {
  setInstantText(!state.instantText);
});

avatarToggleEl?.addEventListener("click", () => {
  const nextMode = state.avatarMode === "live2d" ? "static" : "live2d";
  void setAvatarMode(nextMode);
  requestAudioReplay();
});

avatarSizeRangeEl?.addEventListener("input", (event) => {
  const percent = Number(event.target.value);
  setAvatarScale(percent / 100);
});

document.addEventListener(
  "pointerdown",
  () => {
    unlockAudio();
  },
  { once: true }
);

document.addEventListener("pointerdown", (event) => {
  requestAudioReplay();
  if (!composerDockEl?.contains(event.target) && !inputEl.value.trim() && !state.sending) {
    setComposerExpanded(false);
  }
  if (
    state.giftHandOpen &&
    giftHandPanelEl &&
    giftHandToggleEl &&
    !giftHandPanelEl.contains(event.target) &&
    !giftHandToggleEl.contains(event.target)
  ) {
    setGiftHandOpen(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && appEl.dataset.settingsOpen === "true") {
    setSettingsOpen(false);
  }
  if (event.key === "Escape" && state.giftHandOpen) {
    setGiftHandOpen(false);
  }
});

document.addEventListener(
  "keydown",
  () => {
    unlockAudio();
  },
  { once: true }
);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    requestAudioReplay();
  }
});

document.addEventListener("dragenter", (event) => {
  if (!eventHasFileDrag(event)) {
    return;
  }
  event.preventDefault();
  state.dragDepth += 1;
  setGiftDropOverlayVisible(true);
});

document.addEventListener("dragover", (event) => {
  if (!eventHasFileDrag(event)) {
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = "copy";
  }
  setGiftDropOverlayVisible(true);
});

document.addEventListener("dragleave", (event) => {
  if (state.dragDepth <= 0) {
    return;
  }
  event.preventDefault();
  state.dragDepth = Math.max(0, state.dragDepth - 1);
  if (state.dragDepth === 0) {
    setGiftDropOverlayVisible(false);
  }
});

document.addEventListener("drop", (event) => {
  if (!eventHasFileDrag(event)) {
    return;
  }
  event.preventDefault();
  state.dragDepth = 0;
  setGiftDropOverlayVisible(false);
  const file = extractDroppedGiftFile(event);
  if (!file) {
    debugOutputEl.textContent = "现在可以试试拖一首 mp3 / ogg / wav / m4a / flac，或者一张 png / jpg / jpeg / webp 给 Akane。";
    return;
  }
  void uploadGiftFile(file);
});

updateClock();
syncViewportHeight();
setInstantText(false);
setVoiceEnabled(loadVoiceEnabledPreference());
setAvatarScale(loadAvatarScalePreference(), { persist: false });
void setAvatarMode(loadAvatarModePreference(), { persist: false });
bindAvatarVoiceSync();
scheduleLive2dPreload();
setComposerExpanded(false);
setSettingsOpen(false);
setGiftHandOpen(false);
setInterval(updateClock, 60_000);
window.addEventListener("resize", syncViewportHeight);
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncViewportHeight);
  window.visualViewport.addEventListener("scroll", syncViewportHeight);
}

void (async () => {
  await Promise.all([fetchAppConfig(), fetchModelServiceConfig()]);
  ensureIdentity();
  updateSettingsStatusCopy();
  if (new URLSearchParams(window.location.search).get("configure") === "model") {
    setSettingsOpen(true);
    requestAnimationFrame(() => {
      modelServiceSettingsEl?.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }
  setInterval(() => {
    void pollDueReminders();
  }, REMINDER_POLL_INTERVAL_MS);
  await initializeScene();
  void pollDueReminders();
})();
