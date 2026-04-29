import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { getCurrentWindow } from "@tauri-apps/api/window";

import {
  APP_DISPLAY_NAME,
  CHARACTER_NAME,
  COMMON_EMOTION_CANDIDATES,
  DEFAULT_EMOTION,
  DEFAULT_OUTFIT,
  INPUT_PLACEHOLDER,
  LOCAL_CLICK_LINES,
  MUSIC_EMOTION,
  PROACTIVE_WAKE_PROMPT,
  REQUIRED_EMOTIONS,
  RECOMMENDED_EMOTIONS,
  SESSION_DISPLAY_TITLE,
  TTS_TEST_TEXT,
  buildCharacterSnapshot
} from "./character-profile.js";
import "./styles.css";

const bundledCharacterAssets = import.meta.glob("./assets/characters/猫娘/*.{png,jpg,jpeg,webp}", {
  eager: true,
  import: "default",
  query: "?url"
});
const characterPackCharacterAssets = import.meta.glob(
  "../../desktop_pet_creator_kit/characters/akane_sample/assets/characters/**/*.{png,jpg,jpeg,webp}",
  {
    eager: true,
    import: "default",
    query: "?url"
  }
);

const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauriRuntime ? getCurrentWindow() : null;

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const PROFILE_USER_ID = "master";
const CLIENT_MODE = "desktop_pet";
const DESKTOP_HEALTH_PATH = "/desktop-pet/health";
const LEGACY_HEALTH_PATH = "/health";
const BASE_CAPABILITIES = ["speech_segments", "tts"];
const AUDIO_PLAYBACK_CAPABILITY = "audio_playback";
const THINK_TIMEOUT_MS = 5 * 60 * 1000;
const TTS_TIMEOUT_MS = 45 * 1000;
const ASR_TIMEOUT_MS = 2 * 60 * 1000;
const DESKTOP_CONTEXT_POLL_MS = 1500;
const DESKTOP_CONTEXT_MAX_AGE_MS = 2 * 60 * 1000;
const SCREEN_VISION_FRAME_INTERVAL_MS = 1500;
const DEFAULT_SCREEN_VISION_FRAMES_PER_CLIP = 4;
const SCREEN_VISION_MAX_EDGE = 960;
const SCREEN_VISION_JPEG_QUALITY = 0.64;
const SCREEN_VISION_SAMPLE_WIDTH = 32;
const SCREEN_VISION_SAMPLE_HEIGHT = 18;
const DEFAULT_SCREEN_VISION_INTERVAL_SEC = 25;
const SCREEN_VISION_INTERVAL_MIN_SEC = 15;
const SCREEN_VISION_INTERVAL_MAX_SEC = 600;
const SCREEN_VISION_FRAME_COUNT_MIN = 1;
const SCREEN_VISION_FRAME_COUNT_MAX = 5;
const DEFAULT_SCREEN_VISION_MODE = "summary";
const SCREEN_VISION_MODES = new Set(["summary", "direct"]);
const SCREEN_VISION_DIFF_THRESHOLD = 10;
const SCREEN_VISION_FORCE_AFTER_SKIPS = 2;
const PROACTIVE_WAKE_DEFAULT_SEC = 30;
const PROACTIVE_WAKE_MIN_SEC = 15;
const PROACTIVE_WAKE_MAX_SEC = 600;
const PROACTIVE_WAKE_RETRY_MS = 5000;
const CLIPBOARD_TEXT_LIMIT = 600;
const BACKEND_RETRY_MS = 30 * 1000;
const VOICE_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4"
];
const MUSIC_FILE_EXTENSIONS = new Set(["mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm"]);
const MIN_RECORDING_MS = 700;
const SEGMENT_MIN_MS = 1200;
const SEGMENT_MAX_MS = 4500;
const SEGMENT_CHAR_RATE = 120;
const CLIENT_SEGMENT_SOFT_LIMIT = 56;
const CLIENT_SEGMENT_MAX = 5;
const LOCAL_CLICK_DELAY_MS = 240;
const INPUT_HISTORY_LIMIT = 24;
const SCALE_MIN = 0.75;
const SCALE_MAX = 1.45;
const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];
const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const PET_HIT_POLYGON = [
  [32, 0],
  [72, 0],
  [88, 12],
  [94, 32],
  [100, 66],
  [100, 88],
  [93, 100],
  [8, 100],
  [0, 78],
  [8, 36],
  [18, 12]
];

const DEFAULT_STATE = {
  x: null,
  y: null,
  width: null,
  height: null,
  scale: 1,
  opacity: 1,
  skipTaskbar: true,
  alwaysOnTop: true,
  clickThrough: false,
  backendUrl: DEFAULT_BACKEND_URL,
  profileUserId: PROFILE_USER_ID,
  sessionId: "",
  outfit: DEFAULT_OUTFIT,
  currentEmotion: DEFAULT_EMOTION,
  restoreLatestOnStartup: true,
  voiceEnabled: false,
  voiceInputEnabled: true,
  voiceVolume: 0.85,
  desktopContextEnabled: true,
  clipboardContextEnabled: false,
  screenVisionEnabled: false,
  screenVisionMode: DEFAULT_SCREEN_VISION_MODE,
  proactiveWakeEnabled: false,
  proactiveWakeIntervalSec: PROACTIVE_WAKE_DEFAULT_SEC,
  screenVisionIntervalSec: DEFAULT_SCREEN_VISION_INTERVAL_SEC,
  screenVisionFrameCount: DEFAULT_SCREEN_VISION_FRAMES_PER_CLIP,
  hitTestEnabled: true,
  hitboxOverlay: false
};

const bundledOutfit = buildBundledOutfit();
const characterPackOutfits = buildCharacterPackOutfits();
const localOutfits = characterPackOutfits.length ? characterPackOutfits : [bundledOutfit];
const resourceState = {
  health: "unknown",
  healthMessage: "Not checked",
  healthEndpoint: LEGACY_HEALTH_PATH,
  contractVersion: "",
  contractSource: "unknown",
  capabilities: [],
  endpoints: {},
  tts: {
    enabled: null,
    endpoint: "/tts",
    responseMediaType: "audio/mpeg"
  },
  asr: {
    available: null,
    endpoint: "/asr",
    uploadField: "file"
  },
  manifest: null,
  outfit: getDefaultLocalOutfit(),
  source: getLocalResourceSource(),
  loadedAt: 0
};

const state = { ...DEFAULT_STATE };
const unlistenFns = [];
let saveTimer = 0;
let webglProbe = null;
let sending = false;
let activeTurnToken = 0;
let thinkController = null;
let runtimeMode = "idle";
let bubbleToken = 0;
let bubbleTimer = 0;
let bubbleKind = "none";
let replyDisplayActive = false;
let segmentTimer = 0;
let lastTurnSignature = "";
let lastTurnTextKey = "";
let lastActivityActionSignature = "";
let motionTimer = 0;
let transientEmotionTimer = 0;
let transientEmotionToken = 0;
let localInteractionTimer = 0;
let localInteractionToken = 0;
let localInteractionActive = false;
let lastLocalClickIndex = -1;
let previewEmotionTimer = 0;
let previewEmotionRestore = "";
let previewEmotionToken = 0;
let dragState = null;
let clickTimer = 0;
let inputHistory = [];
let inputHistoryIndex = -1;
let inputHistoryDraft = "";
let applyingInputHistory = false;
let suppressClickUntil = 0;
let hitSyncFrame = 0;
let pendingHitSyncForce = false;
let lastHitRegionSignature = "";
let settingsSnapshotTimer = 0;
let ttsToken = 0;
let ttsController = null;
let ttsObjectUrl = "";
let ttsActive = false;
let ttsQueue = [];
let lastTtsSignature = "";
let resolveTtsWait = null;
let musicTrack = null;
let musicQueue = [];
let musicQueueIndex = -1;
let musicPlaying = false;
let musicPaused = false;
let musicLoading = false;
let musicEmotionActive = false;
let musicDropHover = false;
let voiceInputState = "idle";
let voiceRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let voiceMimeType = "";
let voiceStartedAt = 0;
let voiceShortcutHeld = false;
let asrController = null;
let voiceInputToken = 0;
let desktopContextPollTimer = 0;
let proactiveWakeTimer = 0;
let proactiveWakeLastAt = 0;
let proactiveWakeRunning = false;
let screenVisionTimer = 0;
let screenVisionStream = null;
let screenVisionVideo = null;
let screenVisionCanvas = null;
let screenVisionSampleCanvas = null;
let screenVisionFrames = [];
let screenVisionRecentFrames = [];
let screenVisionLastSample = null;
let screenVisionLastForegroundKey = "";
let screenVisionLastSubmitAt = 0;
let screenVisionSkippedClips = 0;
let screenVisionStatus = "off";
let screenVisionError = "";
let screenVisionActiveClipId = "";
let backendRetryTimer = 0;
let lastDesktopForeground = null;

const els = {
  stage: document.querySelector(".stage"),
  hitboxOverlay: document.querySelector("#hitbox-overlay"),
  hitbox: document.querySelector("#pet-hitbox"),
  petImage: document.querySelector("#pet-image"),
  menu: document.querySelector("#debug-menu"),
  menuTitle: document.querySelector("#debug-menu .menu-head strong"),
  menuSummary: document.querySelector("#menu-summary"),
  toggle: document.querySelector("#debug-toggle"),
  close: document.querySelector("#close-window"),
  quickInput: document.querySelector("#quick-input"),
  openSettings: document.querySelector("#open-settings"),
  openWorkspace: document.querySelector("#open-workspace"),
  stopReply: document.querySelector("#stop-reply"),
  bubble: document.querySelector("#bubble"),
  bubbleText: document.querySelector("#bubble-text"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  voiceRecordButton: document.querySelector("#voice-record-button"),
  scale: document.querySelector("#scale-range"),
  scaleOutput: document.querySelector("#scale-output"),
  scalePresets: document.querySelector("#scale-presets"),
  opacity: document.querySelector("#opacity-range"),
  opacityOutput: document.querySelector("#opacity-output"),
  opacityPresets: document.querySelector("#opacity-presets"),
  backendUrl: document.querySelector("#backend-url-input"),
  backendSave: document.querySelector("#backend-url-save"),
  outfit: document.querySelector("#outfit-input"),
  outfitSave: document.querySelector("#outfit-save"),
  newSession: document.querySelector("#new-session"),
  alwaysOnTop: document.querySelector("#always-on-top-toggle"),
  taskbar: document.querySelector("#taskbar-toggle"),
  webgl: document.querySelector("#webgl-toggle"),
  passthrough: document.querySelector("#passthrough-probe"),
  hitTestToggle: document.querySelector("#hit-test-toggle"),
  hitboxOverlayToggle: document.querySelector("#hitbox-overlay-toggle"),
  reset: document.querySelector("#reset-window"),
  reloadResources: document.querySelector("#reload-resources"),
  closeMenuButton: document.querySelector("#close-window-menu"),
  previousMusic: document.querySelector("#previous-music"),
  nextMusic: document.querySelector("#next-music"),
  toggleMusic: document.querySelector("#toggle-music"),
  stopMusic: document.querySelector("#stop-music"),
  resourceDetails: document.querySelector("#resource-details"),
  emotionGrid: document.querySelector("#emotion-grid"),
  connectionStatus: document.querySelector("#connection-status"),
  status: document.querySelector("#runtime-status"),
  voicePlayer: document.querySelector("#voice-player"),
  musicPlayer: document.querySelector("#music-player"),
  canvas: document.querySelector("#webgl-probe")
};

setPetEmotion(DEFAULT_EMOTION, { persist: false, force: true });
boot();

async function boot() {
  bindUi();
  applyCharacterChrome();
  applyVisualState();
  updateConnectionStatus();

  if (!isTauriRuntime) {
    state.sessionId = generateSessionId();
    setStatus("Browser preview");
    await reloadCharacterResources({ startup: true });
    scheduleNativeHitTestSync();
    return;
  }

  try {
    const loaded = await invoke("load_pet_state");
    Object.assign(state, normalizeState(loaded));
    applyVisualState();
    await reloadCharacterResources({ startup: true });
    setPetEmotion(state.currentEmotion, { persist: false, force: true });
    await invoke("apply_window_state", { state });
    await syncNativeHitTest({ force: true });
    await registerWindowListeners();
    await registerFileDropHandlers();
    await registerSettingsBridge();
    scheduleSave(0);
    void ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
    scheduleDesktopContextPoll();
    scheduleScreenVisionCapture({ immediate: true });
    scheduleProactiveWake();
  } catch (error) {
    setStatus(`Tauri init failed: ${formatError(error)}`);
  }
}

function bindUi() {
  els.hitbox.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || state.clickThrough) return;
    if (event.detail >= 2) {
      event.preventDefault();
      event.stopPropagation();
      cancelLocalClick();
      endManualDrag();
      showChatInput();
      return;
    }

    event.preventDefault();
    closeMenu();
    hideChatInput();
    beginManualDrag(event);
  });

  els.hitbox.addEventListener("pointermove", (event) => {
    void continueManualDrag(event);
  });
  els.hitbox.addEventListener("pointerup", handlePetPointerUp);
  els.hitbox.addEventListener("pointercancel", endManualDrag);
  els.hitbox.addEventListener("lostpointercapture", endManualDrag);
  els.hitbox.addEventListener("click", (event) => {
    if (event.button !== 0 || event.detail !== 1) return;
    if (Date.now() < suppressClickUntil) return;
    scheduleLocalClick();
  });
  els.hitbox.addEventListener("dblclick", (event) => {
    event.preventDefault();
    event.stopPropagation();
    cancelLocalClick();
    endManualDrag();
    showChatInput();
  });
  els.hitbox.addEventListener("contextmenu", openContextMenu);
  els.petImage.addEventListener("load", scheduleNativeHitTestSync);

  els.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitChatInput();
  });

  els.chatInput.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitChatInput();
      return;
    }
    if (event.key === "ArrowUp" && shouldNavigateInputHistory(event, "up")) {
      event.preventDefault();
      navigateInputHistory("up");
      return;
    }
    if (event.key === "ArrowDown" && shouldNavigateInputHistory(event, "down")) {
      event.preventDefault();
      navigateInputHistory("down");
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      hideChatInput();
    }
  });
  els.chatInput.addEventListener("input", () => {
    autoResizeChatInput();
    if (!applyingInputHistory) resetInputHistoryCursor();
  });
  els.chatInput.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (!els.chatInput.value.trim() && document.activeElement !== els.chatInput) {
        hideChatInput();
      }
    }, 180);
  });
  els.chatInput.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
  });
  els.voiceRecordButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    void toggleVoiceRecording();
  });
  els.toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenuNear(els.toggle);
  });
  els.close.addEventListener("click", () => {
    void closePetWindow();
  });
  els.quickInput.addEventListener("click", () => {
    closeMenu();
    showChatInput();
  });
  els.openSettings.addEventListener("click", () => {
    void openSettingsWindow();
  });
  els.openWorkspace.addEventListener("click", () => {
    void openWorkspaceWindow();
  });
  els.stopReply.addEventListener("click", () => {
    interruptReply({ announce: true });
  });
  els.musicPlayer.addEventListener("ended", () => {
    void handleMusicEnded();
  });
  els.musicPlayer.addEventListener("error", () => {
    const name = getMusicDisplayName();
    stopMusic({ silent: true });
    setRuntimeStatus(`音乐播放失败${name ? `：${name}` : ""}`, { mode: "error" });
    showBubbleText("这首歌好像没放出来……", { transient: true, durationMs: 2200, kind: "music" });
  });

  window.addEventListener("contextmenu", (event) => {
    if (event.target.closest("#debug-menu, #chat-form")) return;
    event.preventDefault();
    openContextMenu(event);
  });

  window.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#debug-menu, #debug-toggle, #chat-form")) closeMenu();
  });

  window.addEventListener("keydown", (event) => {
    if (isVoiceShortcut(event) && !event.repeat) {
      event.preventDefault();
      voiceShortcutHeld = true;
      if (voiceInputState !== "recording") {
        showChatInput();
        void startVoiceRecording();
      }
      return;
    }

    if (event.key !== "Escape") return;
    if (voiceInputState === "recording") {
      void cancelVoiceRecording({ notice: true });
      return;
    }
    if (!els.chatForm.hidden) {
      hideChatInput();
      return;
    }
    closeMenu();
  });
  window.addEventListener("keyup", (event) => {
    if (!isVoiceShortcut(event) || !voiceShortcutHeld) return;
    event.preventDefault();
    voiceShortcutHeld = false;
    if (voiceInputState === "recording") {
      void stopVoiceRecording();
    }
  });
  window.addEventListener("resize", scheduleNativeHitTestSync);

  els.scale.addEventListener("input", () => {
    updateVisualScale(Number(els.scale.value));
  });

  els.opacity.addEventListener("input", () => {
    updateVisualOpacity(Number(els.opacity.value));
  });

  els.scalePresets.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scale]");
    if (!button) return;
    updateVisualScale(Number(button.dataset.scale), { commitNow: true });
  });

  els.opacityPresets.addEventListener("click", (event) => {
    const button = event.target.closest("[data-opacity]");
    if (!button) return;
    updateVisualOpacity(Number(button.dataset.opacity), { saveNow: true });
  });

  els.backendSave.addEventListener("click", () => {
    void updateBackendUrlFromInput();
  });
  els.backendUrl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void updateBackendUrlFromInput();
    }
  });

  els.outfitSave.addEventListener("click", () => {
    void updateOutfitFromInput();
  });
  els.outfit.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void updateOutfitFromInput();
    }
  });

  els.emotionGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-emotion]");
    if (!button) return;
    previewEmotion(button.dataset.emotion);
  });

  els.connectionStatus.addEventListener("click", () => {
    void reloadCharacterResources({ userTriggered: true });
  });

  els.newSession.addEventListener("click", () => {
    void startNewSession();
  });

  els.alwaysOnTop.addEventListener("click", async () => {
    await setAlwaysOnTop(!state.alwaysOnTop);
  });

  els.taskbar.addEventListener("click", async () => {
    await setSkipTaskbar(!state.skipTaskbar);
  });

  els.webgl.addEventListener("click", () => {
    toggleWebglProbe();
  });

  els.passthrough.addEventListener("click", async () => {
    closeMenu();
    await probeClickThrough(5000);
  });

  els.hitTestToggle.addEventListener("click", async () => {
    await setHitTestEnabled(!state.hitTestEnabled);
  });

  els.hitboxOverlayToggle.addEventListener("click", () => {
    setHitboxOverlay(!state.hitboxOverlay);
  });

  els.reset.addEventListener("click", async () => {
    await resetWindowPlacement();
  });

  els.reloadResources.addEventListener("click", () => {
    void reloadCharacterResources({ userTriggered: true });
  });

  els.previousMusic.addEventListener("click", () => {
    void playPreviousMusicTrack();
  });

  els.nextMusic.addEventListener("click", () => {
    void playNextMusicTrack();
  });

  els.toggleMusic.addEventListener("click", () => {
    void toggleMusicPlayback();
  });

  els.stopMusic.addEventListener("click", () => {
    stopMusic({ announce: true });
  });

  els.closeMenuButton.addEventListener("click", () => {
    void closePetWindow();
  });
}

function beginManualDrag(event) {
  if (!isTauriRuntime) return;
  dragState = {
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    lastScreenX: event.screenX,
    lastScreenY: event.screenY,
    moved: false
  };

  try {
    els.hitbox.setPointerCapture(event.pointerId);
  } catch {
    // Pointer capture may be unavailable while the webview is losing focus.
  }
}

async function continueManualDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;

  const totalDx = event.clientX - dragState.startClientX;
  const totalDy = event.clientY - dragState.startClientY;
  if (!dragState.moved && Math.hypot(totalDx, totalDy) < 5) return;

  dragState.moved = true;
  cancelLocalClick();
  hideBubble();
  const dx = Math.round(event.screenX - dragState.lastScreenX);
  const dy = Math.round(event.screenY - dragState.lastScreenY);
  if (!dx && !dy) return;

  dragState.lastScreenX = event.screenX;
  dragState.lastScreenY = event.screenY;
  event.preventDefault();

  const geometry = await tauriCall("move_window_by", { dx, dy }, { quiet: true });
  if (geometry) {
    Object.assign(state, geometry);
    scheduleSave(250);
  }
}

function handlePetPointerUp(event) {
  const moved = endManualDrag(event);
  if (moved) {
    suppressClickUntil = Date.now() + 350;
  }
}

function endManualDrag(event) {
  if (!dragState) return false;
  const pointerId = dragState.pointerId;
  const moved = Boolean(dragState.moved);
  dragState = null;
  try {
    if (event?.currentTarget?.hasPointerCapture?.(pointerId)) {
      event.currentTarget.releasePointerCapture(pointerId);
    }
  } catch {
    // Nothing to release.
  }
  return moved;
}

async function registerWindowListeners() {
  if (!appWindow) return;

  unlistenFns.push(await appWindow.onMoved(({ payload }) => {
    state.x = Math.round(payload.x);
    state.y = Math.round(payload.y);
    scheduleSave(250);
  }));

  unlistenFns.push(await appWindow.onResized(({ payload }) => {
    state.width = Math.round(payload.width);
    state.height = Math.round(payload.height);
    scheduleNativeHitTestSync({ force: true });
    scheduleSave(250);
  }));

  window.addEventListener("beforeunload", () => {
    unlistenFns.forEach((unlisten) => unlisten());
    window.clearTimeout(desktopContextPollTimer);
    window.clearTimeout(proactiveWakeTimer);
    stopScreenVisionCapture({ clearRemote: false });
    window.clearTimeout(backendRetryTimer);
    window.clearTimeout(transientEmotionTimer);
    if (thinkController) thinkController.abort();
    if (asrController) asrController.abort();
    stopTts();
    stopMusic({ silent: true });
    cleanupVoiceRecorder();
  });
}

async function registerFileDropHandlers() {
  if (!appWindow?.onDragDropEvent) return;
  unlistenFns.push(await appWindow.onDragDropEvent((event) => {
    const payload = event?.payload || {};
    const type = String(payload.type || "").toLowerCase();
    if (type === "drop") {
      musicDropHover = false;
      void handleDroppedFiles(payload.paths || []);
      return;
    }
    if (type === "over" || type === "enter") {
      showMusicDropHint();
      return;
    }
    musicDropHover = false;
  }));
}

async function registerSettingsBridge() {
  if (!isTauriRuntime) return;

  unlistenFns.push(await listen(SETTINGS_COMMAND_EVENT, (event) => {
    void handleSettingsCommand(event.payload);
  }));
}

async function handleSettingsCommand(payload) {
  const command = String(payload?.command || "").trim();
  if (!command) return;

  switch (command) {
    case "requestSnapshot":
      await broadcastSettingsSnapshot();
      break;
    case "openInput":
      closeMenu();
      showChatInput();
      break;
    case "openWorkspace":
      await openWorkspaceWindow();
      break;
    case "setBackendUrl":
      await updateBackendUrl(payload.value);
      break;
    case "setOutfit":
      await updateOutfit(payload.value);
      break;
    case "setScale":
      updateVisualScale(Number(payload.value), { commitNow: true });
      break;
    case "setOpacity":
      updateVisualOpacity(Number(payload.value), { saveNow: true });
      break;
    case "setVoiceEnabled":
      setVoiceEnabled(Boolean(payload.value));
      break;
    case "setVoiceInputEnabled":
      setVoiceInputEnabled(Boolean(payload.value));
      break;
    case "setVoiceVolume":
      setVoiceVolume(Number(payload.value));
      break;
    case "setDesktopContextEnabled":
      setDesktopContextEnabled(Boolean(payload.value));
      break;
    case "setClipboardContextEnabled":
      setClipboardContextEnabled(Boolean(payload.value));
      break;
    case "setScreenVisionEnabled":
      await setScreenVisionEnabled(Boolean(payload.value));
      break;
    case "setScreenVisionMode":
      setScreenVisionMode(payload.value);
      break;
    case "setProactiveWakeEnabled":
      setProactiveWakeEnabled(Boolean(payload.value));
      break;
    case "setProactiveWakeIntervalSec":
      setProactiveWakeIntervalSec(Number(payload.value));
      break;
    case "setScreenVisionIntervalSec":
      setScreenVisionIntervalSec(Number(payload.value));
      break;
    case "setScreenVisionFrameCount":
      setScreenVisionFrameCount(Number(payload.value));
      break;
    case "clearScreenVision":
      await clearScreenVisionWorkspace();
      break;
    case "setRestoreLatestOnStartup":
      setRestoreLatestOnStartup(Boolean(payload.value));
      break;
    case "stopReply":
      interruptReply({ announce: true });
      break;
    case "stopTts":
      stopTts();
      setRuntimeStatus("语音已停止", { mode: "stopped" });
      break;
    case "testTts":
      await testTts();
      break;
    case "previousMusic":
      await playPreviousMusicTrack();
      break;
    case "nextMusic":
      await playNextMusicTrack();
      break;
    case "toggleMusic":
      await toggleMusicPlayback();
      break;
    case "stopMusic":
      stopMusic({ announce: true });
      break;
    case "setAlwaysOnTop":
      await setAlwaysOnTop(Boolean(payload.value));
      break;
    case "setSkipTaskbar":
      await setSkipTaskbar(Boolean(payload.value));
      break;
    case "setHitTestEnabled":
      await setHitTestEnabled(Boolean(payload.value));
      break;
    case "setHitboxOverlay":
      setHitboxOverlay(Boolean(payload.value));
      break;
    case "toggleWebgl":
      toggleWebglProbe();
      break;
    case "probeClickThrough":
      await probeClickThrough(5000);
      break;
    case "resetWindow":
      await resetWindowPlacement();
      break;
    case "resetVisuals":
      await resetVisuals();
      break;
    case "newSession":
      await startNewSession();
      break;
    case "reloadResources":
      await reloadCharacterResources({ userTriggered: true });
      break;
    case "previewEmotion":
      previewEmotion(payload.value);
      break;
    case "closePet":
      await closePetWindow();
      break;
    default:
      setRuntimeStatus(`未知设置命令：${command}`);
      break;
  }

  scheduleSettingsSnapshot();
}

function applyCharacterChrome() {
  document.title = APP_DISPLAY_NAME;
  if (els.menuTitle) els.menuTitle.textContent = APP_DISPLAY_NAME;
  if (els.chatInput) els.chatInput.placeholder = INPUT_PLACEHOLDER;
  if (els.hitbox) els.hitbox.setAttribute("aria-label", CHARACTER_NAME);
  if (els.petImage) els.petImage.alt = CHARACTER_NAME;
  if (els.close) els.close.title = `关闭 ${APP_DISPLAY_NAME}`;
}

function scheduleSettingsSnapshot(delay = 40) {
  if (!isTauriRuntime) return;
  window.clearTimeout(settingsSnapshotTimer);
  settingsSnapshotTimer = window.setTimeout(() => {
    void broadcastSettingsSnapshot();
  }, delay);
}

async function broadcastSettingsSnapshot() {
  if (!isTauriRuntime) return;
  try {
    await emit(SETTINGS_SNAPSHOT_EVENT, buildSettingsSnapshot());
  } catch {
    // The settings window may not be open yet.
  }
}

function buildSettingsSnapshot() {
  const activeOutfit = getActiveOutfit();
  const emotions = getActiveEmotions();
  const issues = buildResourceIssues(activeOutfit, emotions);
  return {
    character: buildCharacterSnapshot(),
    state: {
      scale: state.scale,
      opacity: state.opacity,
      skipTaskbar: state.skipTaskbar,
      alwaysOnTop: state.alwaysOnTop,
      backendUrl: state.backendUrl,
      profileUserId: state.profileUserId,
      sessionId: state.sessionId,
      outfit: state.outfit,
      currentEmotion: state.currentEmotion,
      restoreLatestOnStartup: state.restoreLatestOnStartup,
      voiceEnabled: state.voiceEnabled,
      voiceInputEnabled: state.voiceInputEnabled,
      voiceVolume: state.voiceVolume,
      desktopContextEnabled: state.desktopContextEnabled,
      clipboardContextEnabled: state.clipboardContextEnabled,
      screenVisionEnabled: state.screenVisionEnabled,
      screenVisionMode: state.screenVisionMode,
      proactiveWakeEnabled: state.proactiveWakeEnabled,
      proactiveWakeIntervalSec: state.proactiveWakeIntervalSec,
      screenVisionIntervalSec: state.screenVisionIntervalSec,
      screenVisionFrameCount: state.screenVisionFrameCount,
      recommendedScreenVisionIntervalSec: recommendedScreenVisionIntervalSec(state.proactiveWakeIntervalSec),
      hitTestEnabled: state.hitTestEnabled,
      hitboxOverlay: state.hitboxOverlay
    },
    resource: {
      health: resourceState.health,
      healthMessage: resourceState.healthMessage,
      healthEndpoint: resourceState.healthEndpoint,
      contractVersion: resourceState.contractVersion,
      contractSource: resourceState.contractSource,
      capabilities: Array.isArray(resourceState.capabilities) ? [...resourceState.capabilities] : [],
      endpoints: { ...(resourceState.endpoints || {}) },
      tts: { ...(resourceState.tts || {}) },
      asr: { ...(resourceState.asr || {}) },
      source: resourceState.source,
      activeOutfit: activeOutfit.id || DEFAULT_OUTFIT,
      activeOutfitName: activeOutfit.name || activeOutfit.id || DEFAULT_OUTFIT,
      requestedOutfit: state.outfit || DEFAULT_OUTFIT,
      defaultOutfit: getManifestDefaultOutfit(resourceState.manifest),
      defaultEmotion: getManifestDefaultEmotion(resourceState.manifest),
      emotionCount: emotions.length,
      outfits: getAvailableOutfits().map(serializeOutfit).filter((item) => item.id),
      emotions: emotions.map(serializeEmotion).filter((item) => item.id),
      missingRequired: issues.missingRequired,
      missingRecommended: issues.missingRecommended,
      loadedAt: resourceState.loadedAt,
      sessionShort: shortId(state.sessionId),
      retrying: Boolean(backendRetryTimer)
    },
    runtimeStatus: els.status.textContent || "",
    runtimeMode,
    active: {
      sending,
      speaking: ttsActive,
      voiceInput: voiceInputState,
      musicPlaying,
      musicPaused,
      screenVision: screenVisionStatus,
      screenVisionMode: state.screenVisionMode,
      screenVisionClipId: screenVisionActiveClipId,
      screenVisionError,
      screenVisionFrameBufferSize: screenVisionRecentFrames.length,
      proactiveWake: state.proactiveWakeEnabled ? "enabled" : "off",
      proactiveWakeRunning,
      proactiveWakeLastAt,
      bubbleVisible: els.bubble.classList.contains("visible"),
      bubbleKind,
      replyDisplayActive
    },
    tts: {
      active: ttsActive,
      queueLength: ttsQueue.length
    },
    music: buildMusicSnapshot(),
    webglEnabled: els.stage.classList.contains("show-webgl")
  };
}

function normalizeState(value) {
  const incoming = value ?? {};
  const scale = clamp(Number(incoming.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX);
  const legacySize = isLegacyWindowSize(incoming.width, incoming.height, scale);
  return {
    ...DEFAULT_STATE,
    ...incoming,
    width: legacySize ? null : incoming.width ?? DEFAULT_STATE.width,
    height: legacySize ? null : incoming.height ?? DEFAULT_STATE.height,
    scale,
    opacity: clamp(Number(incoming.opacity ?? DEFAULT_STATE.opacity), 0.55, 1),
    skipTaskbar: Boolean(incoming.skipTaskbar ?? DEFAULT_STATE.skipTaskbar),
    alwaysOnTop: Boolean(incoming.alwaysOnTop ?? DEFAULT_STATE.alwaysOnTop),
    clickThrough: false,
    backendUrl: normalizeBackendUrl(incoming.backendUrl),
    profileUserId: PROFILE_USER_ID,
    sessionId: String(incoming.sessionId || "").trim() || generateSessionId(),
    outfit: normalizeOutfitName(incoming.outfit),
    currentEmotion: resolveEmotionEntry(incoming.currentEmotion).id,
    restoreLatestOnStartup: Boolean(incoming.restoreLatestOnStartup ?? DEFAULT_STATE.restoreLatestOnStartup),
    voiceEnabled: Boolean(incoming.voiceEnabled ?? DEFAULT_STATE.voiceEnabled),
    voiceInputEnabled: Boolean(incoming.voiceInputEnabled ?? DEFAULT_STATE.voiceInputEnabled),
    voiceVolume: clamp(Number(incoming.voiceVolume ?? DEFAULT_STATE.voiceVolume), 0, 1),
    desktopContextEnabled: Boolean(incoming.desktopContextEnabled ?? DEFAULT_STATE.desktopContextEnabled),
    clipboardContextEnabled: Boolean(
      incoming.clipboardContextEnabled ?? DEFAULT_STATE.clipboardContextEnabled
    ),
    screenVisionEnabled: Boolean(incoming.screenVisionEnabled ?? DEFAULT_STATE.screenVisionEnabled),
    screenVisionMode: normalizeScreenVisionMode(incoming.screenVisionMode ?? DEFAULT_STATE.screenVisionMode),
    proactiveWakeEnabled: Boolean(incoming.proactiveWakeEnabled ?? DEFAULT_STATE.proactiveWakeEnabled),
    proactiveWakeIntervalSec: normalizeProactiveWakeIntervalSec(
      incoming.proactiveWakeIntervalSec ?? DEFAULT_STATE.proactiveWakeIntervalSec
    ),
    screenVisionIntervalSec: normalizeScreenVisionIntervalSec(
      incoming.screenVisionIntervalSec ?? DEFAULT_STATE.screenVisionIntervalSec
    ),
    screenVisionFrameCount: normalizeScreenVisionFrameCount(
      incoming.screenVisionFrameCount ?? DEFAULT_STATE.screenVisionFrameCount
    ),
    hitTestEnabled: Boolean(incoming.hitTestEnabled ?? DEFAULT_STATE.hitTestEnabled),
    hitboxOverlay: Boolean(incoming.hitboxOverlay ?? DEFAULT_STATE.hitboxOverlay)
  };
}

function normalizeScreenVisionMode(value) {
  const mode = String(value || DEFAULT_SCREEN_VISION_MODE).trim().toLowerCase();
  return SCREEN_VISION_MODES.has(mode) ? mode : DEFAULT_SCREEN_VISION_MODE;
}

function normalizeProactiveWakeIntervalSec(value) {
  return Math.round(clamp(Number(value || PROACTIVE_WAKE_DEFAULT_SEC), PROACTIVE_WAKE_MIN_SEC, PROACTIVE_WAKE_MAX_SEC));
}

function normalizeScreenVisionIntervalSec(value) {
  return Math.round(
    clamp(Number(value || DEFAULT_SCREEN_VISION_INTERVAL_SEC), SCREEN_VISION_INTERVAL_MIN_SEC, SCREEN_VISION_INTERVAL_MAX_SEC)
  );
}

function normalizeScreenVisionFrameCount(value) {
  return Math.round(
    clamp(
      Number(value || DEFAULT_SCREEN_VISION_FRAMES_PER_CLIP),
      SCREEN_VISION_FRAME_COUNT_MIN,
      SCREEN_VISION_FRAME_COUNT_MAX
    )
  );
}

function recommendedScreenVisionIntervalSec(wakeIntervalSec) {
  const raw = normalizeProactiveWakeIntervalSec(wakeIntervalSec) * 0.75;
  const rounded = Math.round(raw / 5) * 5;
  return normalizeScreenVisionIntervalSec(rounded);
}

function isLegacyWindowSize(width, height, scale) {
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h)) return false;
  return Math.abs(w - 360 * scale) <= 3 && Math.abs(h - 620 * scale) <= 3;
}

function applyVisualState() {
  document.documentElement.style.setProperty("--pet-scale", String(state.scale));
  document.documentElement.style.setProperty("--pet-opacity", String(state.opacity));
  els.scale.value = String(state.scale);
  els.opacity.value = String(state.opacity);
  els.scaleOutput.value = `${Math.round(state.scale * 100)}%`;
  els.opacityOutput.value = `${Math.round(state.opacity * 100)}%`;
  if (els.voicePlayer) els.voicePlayer.volume = state.voiceVolume;
  els.backendUrl.value = state.backendUrl;
  els.outfit.value = state.outfit || DEFAULT_OUTFIT;
  els.stage.classList.toggle("show-hitbox-overlay", state.hitboxOverlay);
  updateVoiceRecordButton();
  updateMenuLabels();
  scheduleNativeHitTestSync();
  autoResizeChatInput();
}

function updateMenuLabels() {
  els.taskbar.textContent = state.skipTaskbar ? "显示任务栏" : "隐藏任务栏";
  els.alwaysOnTop.textContent = state.alwaysOnTop ? "取消置顶" : "保持置顶";
  els.webgl.textContent = els.stage.classList.contains("show-webgl") ? "隐藏 WebGL" : "WebGL";
  els.hitTestToggle.textContent = state.hitTestEnabled ? "Hit-Test: on" : "Hit-Test: off";
  els.hitboxOverlayToggle.textContent = state.hitboxOverlay ? "Hitbox: on" : "Hitbox: off";
  if (els.menuSummary) {
    const outfit = getActiveOutfit();
    const source = resourceState.source === "manifest" ? "后端资源" : "本地资源";
    els.menuSummary.textContent = `${outfit.id || DEFAULT_OUTFIT} · ${source} · ${state.currentEmotion || DEFAULT_EMOTION}`;
  }
  renderPresetChips();
  renderResourceDetails();
  renderEmotionGrid();
  scheduleSettingsSnapshot();
}

function updateVisualScale(value, { commitNow = false } = {}) {
  state.scale = clamp(Number(value), SCALE_MIN, SCALE_MAX);
  applyVisualState();
  if (commitNow) {
    window.clearTimeout(scaleTimer);
    void commitVisualScale();
  } else {
    scheduleScaleCommit();
  }
}

function updateVisualOpacity(value, { saveNow = false } = {}) {
  state.opacity = clamp(Number(value), 0.55, 1);
  applyVisualState();
  scheduleSave(saveNow ? 0 : undefined);
}

function setVoiceEnabled(enabled) {
  state.voiceEnabled = Boolean(enabled);
  if (!state.voiceEnabled) {
    stopTts();
  }
  scheduleSave(0);
  setRuntimeStatus(state.voiceEnabled ? "语音播放已开启" : "语音播放已关闭", { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setVoiceInputEnabled(enabled) {
  state.voiceInputEnabled = Boolean(enabled);
  if (!state.voiceInputEnabled) {
    void cancelVoiceRecording();
  }
  scheduleSave(0);
  setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
  setRuntimeStatus(state.voiceInputEnabled ? "语音输入已开启" : "语音输入已关闭", { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setVoiceVolume(value) {
  state.voiceVolume = clamp(Number(value), 0, 1);
  if (els.voicePlayer) els.voicePlayer.volume = state.voiceVolume;
  if (els.musicPlayer) els.musicPlayer.volume = state.voiceVolume;
  scheduleSave(0);
  scheduleSettingsSnapshot();
}

function setDesktopContextEnabled(enabled) {
  state.desktopContextEnabled = Boolean(enabled);
  if (!state.desktopContextEnabled) {
    window.clearTimeout(desktopContextPollTimer);
    desktopContextPollTimer = 0;
  } else {
    scheduleDesktopContextPoll({ immediate: true });
  }
  scheduleSave(0);
  setRuntimeStatus(state.desktopContextEnabled ? "前台窗口感知已开启" : "前台窗口感知已关闭", {
    mode: "idle"
  });
  scheduleSettingsSnapshot();
}

function setClipboardContextEnabled(enabled) {
  state.clipboardContextEnabled = Boolean(enabled);
  scheduleSave(0);
  setRuntimeStatus(state.clipboardContextEnabled ? "剪贴板上下文已开启" : "剪贴板上下文已关闭", {
    mode: "idle"
  });
  scheduleSettingsSnapshot();
}

async function setScreenVisionEnabled(enabled) {
  state.screenVisionEnabled = Boolean(enabled);
  screenVisionError = "";
  if (state.screenVisionEnabled) {
    screenVisionStatus = "starting";
    setRuntimeStatus("正在请求屏幕权限", { mode: "idle" });
    const started = await ensureScreenVisionCapture();
    if (started) {
      scheduleScreenVisionCapture({ immediate: true });
      setRuntimeStatus("看屏幕已开启", { mode: "idle" });
    } else {
      state.screenVisionEnabled = false;
      await clearScreenVisionWorkspace({ quiet: true });
      setRuntimeStatus(`看屏幕开启失败：${screenVisionError || "未获得屏幕权限"}`, { mode: "error" });
    }
  } else {
    stopScreenVisionCapture();
    await clearScreenVisionWorkspace({ quiet: true });
    setRuntimeStatus("看屏幕已关闭", { mode: "idle" });
  }
  scheduleSave(0);
  scheduleSettingsSnapshot();
}

function setScreenVisionMode(value) {
  state.screenVisionMode = normalizeScreenVisionMode(value);
  screenVisionLastSubmitAt = 0;
  screenVisionSkippedClips = 0;
  scheduleSave(0);
  setRuntimeStatus(
    state.screenVisionMode === "direct"
      ? `看屏幕模式：${CHARACTER_NAME} 直看最近截图`
      : "看屏幕模式：先整理屏幕印象",
    { mode: "idle" }
  );
  scheduleSettingsSnapshot();
}

function setProactiveWakeEnabled(enabled) {
  state.proactiveWakeEnabled = Boolean(enabled);
  if (state.proactiveWakeEnabled) {
    scheduleProactiveWake({ immediate: false });
    setRuntimeStatus("主动搭话已开启", { mode: "idle" });
  } else {
    window.clearTimeout(proactiveWakeTimer);
    proactiveWakeTimer = 0;
    proactiveWakeRunning = false;
    setRuntimeStatus("主动搭话已关闭", { mode: "idle" });
  }
  scheduleSave(0);
  scheduleSettingsSnapshot();
}

function setProactiveWakeIntervalSec(value) {
  state.proactiveWakeIntervalSec = normalizeProactiveWakeIntervalSec(value);
  const recommended = recommendedScreenVisionIntervalSec(state.proactiveWakeIntervalSec);
  if (!Number.isFinite(Number(state.screenVisionIntervalSec))) {
    state.screenVisionIntervalSec = recommended;
  }
  scheduleSave(0);
  scheduleProactiveWake({ immediate: false });
  setRuntimeStatus(`主动搭话间隔：${state.proactiveWakeIntervalSec} 秒`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setScreenVisionIntervalSec(value) {
  state.screenVisionIntervalSec = normalizeScreenVisionIntervalSec(value);
  scheduleSave(0);
  setRuntimeStatus(`视觉摘要间隔：${state.screenVisionIntervalSec} 秒`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setScreenVisionFrameCount(value) {
  state.screenVisionFrameCount = normalizeScreenVisionFrameCount(value);
  if (screenVisionFrames.length > state.screenVisionFrameCount) {
    screenVisionFrames = screenVisionFrames.slice(-state.screenVisionFrameCount);
  }
  if (screenVisionRecentFrames.length > state.screenVisionFrameCount) {
    screenVisionRecentFrames = screenVisionRecentFrames.slice(-state.screenVisionFrameCount);
  }
  scheduleSave(0);
  setRuntimeStatus(`屏幕帧数：${state.screenVisionFrameCount} 张`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setRestoreLatestOnStartup(enabled) {
  state.restoreLatestOnStartup = Boolean(enabled);
  scheduleSave(0);
  setRuntimeStatus(state.restoreLatestOnStartup ? "启动时会恢复上一轮" : "启动时不恢复上一轮", {
    mode: "idle"
  });
  scheduleSettingsSnapshot();
}

function renderPresetChips() {
  renderValueChips(els.scalePresets, SCALE_PRESETS, "scale", state.scale);
  renderValueChips(els.opacityPresets, OPACITY_PRESETS, "opacity", state.opacity);
}

function renderValueChips(container, values, dataKey, activeValue) {
  if (!container) return;
  const signature = `${dataKey}:${values.join(",")}:${activeValue}`;
  if (container.dataset.signature === signature) return;
  container.dataset.signature = signature;
  container.replaceChildren(
    ...values.map((value) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset[dataKey] = String(value);
      button.textContent = `${Math.round(value * 100)}%`;
      button.classList.toggle("active", Math.abs(Number(activeValue) - value) < 0.001);
      return button;
    })
  );
}

function renderResourceDetails() {
  if (!els.resourceDetails) return;
  const activeOutfit = getActiveOutfit();
  const outfits = getManifestOutfits();
  const count = getActiveEmotions().length;
  const source = resourceSourceLabel(resourceState.source);
  const requested =
    state.outfit && activeOutfit.id && state.outfit !== activeOutfit.id ? ` · 请求 ${state.outfit}` : "";
  const outfitHint = outfits.length > 1 ? ` · 可用服装 ${outfits.length}` : "";
  const sessionHint = state.sessionId ? ` · 会话 ${shortId(state.sessionId)}` : "";
  els.resourceDetails.textContent = `${source} · ${activeOutfit.id || DEFAULT_OUTFIT} · ${count} 表情${requested}${outfitHint}${sessionHint}`;
}

function renderEmotionGrid() {
  if (!els.emotionGrid) return;
  const emotions = getActiveEmotions();
  const signature = emotions
    .map((emotion) => `${emotion.id}:${emotion.name || ""}`)
    .join("|");
  const active = state.currentEmotion || DEFAULT_EMOTION;
  const gridSignature = `${signature}::${active}`;
  if (els.emotionGrid.dataset.signature === gridSignature) return;
  els.emotionGrid.dataset.signature = gridSignature;

  els.emotionGrid.replaceChildren(
    ...emotions.map((emotion) => {
      const id = String(emotion.id || emotion.name || "").trim();
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.emotion = id;
      button.textContent = String(emotion.name || id);
      button.title = id;
      button.classList.toggle("active", id === active);
      return button;
    })
  );
}

function showChatInput() {
  if (sending) return;
  cancelLocalClick();
  closeMenu();
  els.chatForm.hidden = false;
  autoResizeChatInput();
  scheduleNativeHitTestSync({ force: true });
  window.setTimeout(() => {
    els.chatInput.focus();
    els.chatInput.select();
  }, 0);
}

function submitChatInput() {
  const text = els.chatInput.value;
  hideChatInput({ clear: true });
  void sendMessage(text);
}

function hideChatInput({ clear = false } = {}) {
  if (els.chatForm.hidden && !clear) return;
  els.chatForm.hidden = true;
  if (clear) els.chatInput.value = "";
  resetInputHistoryCursor();
  autoResizeChatInput();
  els.chatInput.blur();
  scheduleNativeHitTestSync({ force: true });
}

function setChatInputText(text, { append = false } = {}) {
  const value = String(text || "").trim();
  if (!value) return;
  const current = els.chatInput.value.trim();
  els.chatInput.value = append && current ? `${current} ${value}` : value;
  showChatInput();
  autoResizeChatInput();
}

function shouldNavigateInputHistory(event, direction) {
  if (event.ctrlKey || event.altKey || event.metaKey || !inputHistory.length) return false;
  const input = els.chatInput;
  const value = input.value || "";
  const start = Number(input.selectionStart ?? value.length);
  const end = Number(input.selectionEnd ?? value.length);
  if (start !== end) return false;
  if (!value.includes("\n")) return true;
  return direction === "up" ? start === 0 : end === value.length;
}

function navigateInputHistory(direction) {
  if (!inputHistory.length) return;
  if (inputHistoryIndex === -1) inputHistoryDraft = els.chatInput.value;

  if (direction === "up") {
    inputHistoryIndex =
      inputHistoryIndex === -1 ? inputHistory.length - 1 : Math.max(0, inputHistoryIndex - 1);
  } else if (inputHistoryIndex >= inputHistory.length - 1) {
    inputHistoryIndex = -1;
  } else {
    inputHistoryIndex += 1;
  }

  applyChatInputValue(inputHistoryIndex === -1 ? inputHistoryDraft : inputHistory[inputHistoryIndex]);
}

function applyChatInputValue(value) {
  applyingInputHistory = true;
  els.chatInput.value = String(value || "");
  autoResizeChatInput();
  const end = els.chatInput.value.length;
  els.chatInput.setSelectionRange(end, end);
  applyingInputHistory = false;
}

function rememberInputHistory(text) {
  const value = String(text || "").trim();
  if (!value) return;
  inputHistory = inputHistory.filter((item) => item !== value);
  inputHistory.push(value);
  if (inputHistory.length > INPUT_HISTORY_LIMIT) {
    inputHistory = inputHistory.slice(-INPUT_HISTORY_LIMIT);
  }
  resetInputHistoryCursor();
}

function resetInputHistoryCursor() {
  inputHistoryIndex = -1;
  inputHistoryDraft = "";
}

function restoreFailedInput(text) {
  const value = String(text || "").trim();
  if (!value) return;
  showChatInput();
  els.chatInput.value = value;
  autoResizeChatInput();
  window.setTimeout(() => {
    els.chatInput.focus();
    const end = els.chatInput.value.length;
    els.chatInput.setSelectionRange(end, end);
  }, 0);
}

function autoResizeChatInput() {
  const input = els.chatInput;
  if (!input) return;
  const minHeight = readCssPx("--chat-input-min-height", 42);
  const maxHeight = readCssPx("--chat-input-max-height", 96);
  input.style.height = `${minHeight}px`;
  const nextHeight = Math.min(input.scrollHeight, maxHeight);
  input.style.height = `${Math.max(minHeight, nextHeight)}px`;
}

function isVoiceShortcut(event) {
  return event.ctrlKey && event.shiftKey && !event.altKey && event.code === "Space";
}

async function toggleVoiceRecording() {
  if (voiceInputState === "recording") {
    await stopVoiceRecording();
  } else {
    await startVoiceRecording();
  }
}

async function startVoiceRecording() {
  if (!state.voiceInputEnabled) {
    showError("语音输入已关闭");
    return;
  }
  if (sending) {
    showBubbleText("我正在回复这轮消息，等一下再听你说。", { transient: true, durationMs: 2200 });
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    showError("当前 WebView 不支持录音");
    return;
  }
  if (voiceInputState === "processing") return;

  try {
    voiceStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });
    voiceChunks = [];
    voiceMimeType = selectVoiceMimeType();
    const options = voiceMimeType ? { mimeType: voiceMimeType } : undefined;
    try {
      voiceRecorder = new MediaRecorder(voiceStream, options);
    } catch {
      voiceRecorder = new MediaRecorder(voiceStream);
      voiceMimeType = voiceRecorder.mimeType || voiceMimeType;
    }
    voiceRecorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size > 0) voiceChunks.push(event.data);
    });
    voiceStartedAt = Date.now();
    voiceRecorder.start();
    setVoiceInputState("recording");
    setPetEmotion("listening", { persist: false });
    setPetMotion("thinking");
    showBubbleText("正在听……", { transient: false });
    setRuntimeStatus("语音录制中", { mode: "listening" });
  } catch (error) {
    cleanupVoiceRecorder();
    setVoiceInputState("idle");
    showError(describeVoiceError(error));
  }
}

function stopVoiceRecording() {
  if (voiceInputState !== "recording" || !voiceRecorder) return Promise.resolve();

  const recorder = voiceRecorder;
  return new Promise((resolve) => {
    recorder.addEventListener(
      "stop",
      () => {
        const durationMs = Date.now() - voiceStartedAt;
        const mimeType = recorder.mimeType || voiceMimeType || "audio/webm";
        const blob = new Blob(voiceChunks, { type: mimeType });
        cleanupVoiceRecorder();

        if (durationMs < MIN_RECORDING_MS || blob.size < 512) {
          setVoiceInputState("idle");
          showError("录音太短啦，我没听清。");
          resolve();
          return;
        }

        void transcribeVoiceBlob(blob).finally(resolve);
      },
      { once: true }
    );

    recorder.addEventListener(
      "error",
      () => {
        cleanupVoiceRecorder();
        setVoiceInputState("idle");
        showError("录音失败了");
        resolve();
      },
      { once: true }
    );

    try {
      recorder.stop();
    } catch (error) {
      cleanupVoiceRecorder();
      setVoiceInputState("idle");
      showError(describeVoiceError(error));
      resolve();
    }
  });
}

async function cancelVoiceRecording({ notice = false } = {}) {
  voiceInputToken += 1;
  if (asrController) {
    asrController.abort();
    asrController = null;
  }
  if (voiceRecorder && voiceRecorder.state !== "inactive") {
    try {
      voiceRecorder.stop();
    } catch {
      // Cancellation should stay quiet.
    }
  }
  cleanupVoiceRecorder();
  setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
  if (notice) {
    showBubbleText("语音输入已取消。", { transient: true, durationMs: 1600 });
    setRuntimeStatus("语音输入已取消", { mode: "idle" });
  }
}

async function transcribeVoiceBlob(blob) {
  const token = ++voiceInputToken;
  setVoiceInputState("processing");
  setPetEmotion("thinking", { persist: false });
  setPetMotion("thinking", { durationMs: 1400 });
  showBubbleText("我在识别语音……", { transient: false });
  setRuntimeStatus("语音识别中", { mode: "thinking" });

  const controller = new AbortController();
  asrController = controller;
  const timeoutId = window.setTimeout(() => controller.abort(), ASR_TIMEOUT_MS);
  const form = new FormData();
  form.append("file", blob, getVoiceFilename());
  form.append("language", "zh");

  try {
    const requestInit = {
      method: "POST",
      cache: "no-store",
      body: form,
      signal: controller.signal
    };
    if (isTauriRuntime) {
      requestInit.connectTimeout = 30_000;
    }

    const response = await backendFetch(buildBackendEndpointUrl("asr", "/asr", { t: Date.now() }), requestInit);
    if (token !== voiceInputToken) return;
    const payload = await readJsonResponse(response);
    if (token !== voiceInputToken) return;
    if (!response.ok) {
      throw new Error(extractBackendErrorMessage(payload) || `ASR HTTP ${response.status}`);
    }

    const text = String(payload?.text || payload?.transcript || "").trim();
    if (!payload?.ok || !text) {
      throw new Error(extractBackendErrorMessage(payload) || "没听清，可以再说一次。");
    }

    setChatInputText(text, { append: Boolean(els.chatInput.value.trim()) });
    setTransientEmotion("success", { durationMs: 2600 });
    showBubbleText("我听写好了，确认一下再发送。", {
      transient: true,
      durationMs: 2400,
      kind: "status"
    });
    setRuntimeStatus("语音已转成文字", { mode: "idle" });
  } catch (error) {
    if (token === voiceInputToken && !isAbortLike(error)) {
      showError(describeVoiceError(error));
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (asrController === controller) asrController = null;
    if (token === voiceInputToken) {
      setVoiceInputState(state.voiceInputEnabled ? "idle" : "disabled");
    }
  }
}

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function readBackendErrorMessage(response, fallback = "请求失败") {
  const statusText = response?.status ? `HTTP ${response.status}` : fallback;
  const contentType = String(response?.headers?.get?.("content-type") || "").toLowerCase();
  try {
    if (contentType.includes("json")) {
      const payload = await response.json();
      return extractBackendErrorMessage(payload) || statusText;
    }
    const text = String(await response.text()).trim();
    if (text.startsWith("{")) {
      try {
        const payload = JSON.parse(text);
        const message = extractBackendErrorMessage(payload);
        if (message) return message;
      } catch {
        // Fall back to text below.
      }
    }
    return text ? friendlyErrorMessage(text) : statusText;
  } catch {
    return statusText;
  }
}

function extractBackendErrorMessage(payload) {
  if (!payload || typeof payload !== "object") return "";
  const detail = payload.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const detailMessage = extractBackendErrorMessage(detail);
    if (detailMessage) return detailMessage;
  }
  for (const key of ["message", "error", "reason"]) {
    const value = String(payload[key] || "").trim();
    if (value) return value;
  }
  return "";
}

function setVoiceInputState(nextState) {
  voiceInputState = nextState;
  updateVoiceRecordButton();
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function updateVoiceRecordButton() {
  if (!els.voiceRecordButton) return;
  const effectiveState = state.voiceInputEnabled ? voiceInputState : "disabled";
  els.voiceRecordButton.classList.toggle("recording", effectiveState === "recording");
  els.voiceRecordButton.classList.toggle("processing", effectiveState === "processing");
  els.voiceRecordButton.disabled = effectiveState === "disabled" || effectiveState === "processing";
  if (effectiveState === "recording") {
    els.voiceRecordButton.textContent = "停";
    els.voiceRecordButton.title = "停止录音";
  } else if (effectiveState === "processing") {
    els.voiceRecordButton.textContent = "…";
    els.voiceRecordButton.title = "正在识别语音";
  } else {
    els.voiceRecordButton.textContent = "麦";
    els.voiceRecordButton.title = state.voiceInputEnabled ? "语音输入 Ctrl+Shift+Space" : "语音输入已关闭";
  }
}

function cleanupVoiceRecorder() {
  for (const track of voiceStream?.getTracks?.() || []) {
    track.stop();
  }
  voiceRecorder = null;
  voiceStream = null;
  voiceChunks = [];
  voiceStartedAt = 0;
}

function selectVoiceMimeType() {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
  return VOICE_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function getVoiceFilename() {
  const mimeType = String(voiceMimeType || "").toLowerCase();
  if (mimeType.includes("ogg")) return "akane_voice_input.ogg";
  if (mimeType.includes("mp4")) return "akane_voice_input.m4a";
  return "akane_voice_input.webm";
}

function describeVoiceError(error) {
  const name = String(error?.name || "");
  if (name === "NotAllowedError" || name === "SecurityError") return "没有麦克风权限";
  if (name === "NotFoundError" || name === "DevicesNotFoundError") return "没有找到可用麦克风";
  return friendlyErrorMessage(formatError(error));
}

function scheduleLocalClick() {
  const bubbleBusy = els.bubble.classList.contains("visible") && !canLocalInteractionReplaceBubble();
  if (sending || ttsActive || ttsQueue.length > 0 || bubbleBusy || !els.chatForm.hidden || !els.menu.hidden) {
    return;
  }
  cancelLocalClick();
  clickTimer = window.setTimeout(() => {
    clickTimer = 0;
    showLocalInteraction();
  }, LOCAL_CLICK_DELAY_MS);
}

function cancelLocalClick() {
  window.clearTimeout(clickTimer);
  clickTimer = 0;
}

function canLocalInteractionReplaceBubble() {
  if (!els.bubble.classList.contains("visible")) return true;
  return localInteractionActive || (!sending && !ttsActive && ttsQueue.length === 0 && !replyDisplayActive);
}

function showLocalInteraction() {
  if (sending || !els.chatForm.hidden) return;
  cancelEmotionPreview({ restore: true });
  const item = pickLocalClickLine();
  const token = ++localInteractionToken;
  window.clearTimeout(localInteractionTimer);
  localInteractionActive = true;
  setPetMotion("click", { durationMs: 680 });
  setPetEmotion(item.emotion, { persist: false });
  showBubbleText(item.text, { transient: true, durationMs: 2600, local: true });
  localInteractionTimer = window.setTimeout(() => {
    if (token !== localInteractionToken || sending) return;
    localInteractionActive = false;
    setPetEmotion(musicPlaying ? MUSIC_EMOTION : DEFAULT_EMOTION, { persist: false });
  }, 2700);
}

function clearLocalInteraction() {
  localInteractionToken += 1;
  localInteractionActive = false;
  window.clearTimeout(localInteractionTimer);
}

function pickLocalClickLine() {
  if (LOCAL_CLICK_LINES.length <= 1) {
    lastLocalClickIndex = 0;
    return LOCAL_CLICK_LINES[0];
  }
  let index = Math.floor(Math.random() * LOCAL_CLICK_LINES.length);
  if (index === lastLocalClickIndex) {
    index = (index + 1 + Math.floor(Math.random() * (LOCAL_CLICK_LINES.length - 1))) % LOCAL_CLICK_LINES.length;
  }
  lastLocalClickIndex = index;
  return LOCAL_CLICK_LINES[index];
}

function previewEmotion(emotion) {
  if (sending || !emotion) return;
  const previous = previewEmotionRestore || state.currentEmotion || DEFAULT_EMOTION;
  const token = ++previewEmotionToken;
  window.clearTimeout(previewEmotionTimer);
  previewEmotionRestore = previous;
  const resolved = setPetEmotion(emotion, { persist: false });
  showBubbleText(`表情预览：${resolved}`, { transient: true, durationMs: 2200 });
  previewEmotionTimer = window.setTimeout(() => {
    if (token !== previewEmotionToken || sending) return;
    cancelEmotionPreview({ restore: true });
  }, 2300);
}

function cancelEmotionPreview({ restore = false } = {}) {
  window.clearTimeout(previewEmotionTimer);
  previewEmotionTimer = 0;
  previewEmotionToken += 1;
  const restoreEmotion = previewEmotionRestore;
  previewEmotionRestore = "";
  if (restore && restoreEmotion) {
    setPetEmotion(restoreEmotion, { persist: false });
  }
}

function openContextMenu(event) {
  event.preventDefault();
  event.stopPropagation();
  cancelLocalClick();
  showMenu(event.clientX, event.clientY);
}

function toggleMenuNear(anchor) {
  if (!els.menu.hidden) {
    closeMenu();
    return;
  }
  const rect = anchor.getBoundingClientRect();
  showMenu(rect.right - 2, rect.bottom + 8);
}

function showMenu(x, y) {
  els.menu.hidden = false;
  updateConnectionStatus();
  const rect = els.menu.getBoundingClientRect();
  const maxX = window.innerWidth - rect.width - 8;
  const maxY = window.innerHeight - rect.height - 8;
  els.menu.style.left = `${Math.max(8, Math.min(x, maxX))}px`;
  els.menu.style.top = `${Math.max(8, Math.min(y, maxY))}px`;
  scheduleNativeHitTestSync({ force: true });
}

function closeMenu() {
  els.menu.hidden = true;
  scheduleNativeHitTestSync({ force: true });
}

function scheduleNativeHitTestSync({ force = false } = {}) {
  pendingHitSyncForce = pendingHitSyncForce || force;
  if (hitSyncFrame) return;

  hitSyncFrame = window.requestAnimationFrame(() => {
    const shouldForce = pendingHitSyncForce;
    pendingHitSyncForce = false;
    hitSyncFrame = 0;
    void syncNativeHitTest({ force: shouldForce });
  });
}

async function syncNativeHitTest({ force = false } = {}) {
  const regions = collectHitRegions();
  renderHitboxOverlay(regions);

  const signature = JSON.stringify({
    enabled: state.hitTestEnabled,
    regions
  });
  if (!force && signature === lastHitRegionSignature) return;
  lastHitRegionSignature = signature;

  if (!isTauriRuntime) return;

  await tauriCall("update_hit_regions", { regions }, { quiet: true });
  await tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled }, { quiet: true });
}

function collectHitRegions() {
  const regions = [];
  const petRegion = buildPetHitRegion();
  if (petRegion) regions.push(petRegion);

  addElementHitRegion(regions, els.toggle, "debug-toggle");
  addElementHitRegion(regions, els.close, "close-button");
  if (!els.chatForm.hidden) addElementHitRegion(regions, els.chatForm, "chat-form");
  if (!els.menu.hidden) addElementHitRegion(regions, els.menu, "debug-menu");

  return regions;
}

function buildPetHitRegion() {
  const rect = rectToPhysical(els.hitbox.getBoundingClientRect());
  if (!isUsableRect(rect)) return null;

  return {
    kind: "pet",
    rect,
    polygon: PET_HIT_POLYGON.map(([x, y]) => ({
      x: Math.round(rect.x + (rect.width * x) / 100),
      y: Math.round(rect.y + (rect.height * y) / 100)
    }))
  };
}

function addElementHitRegion(regions, element, kind) {
  if (!element || element.hidden) return;

  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") return;

  const rect = rectToPhysical(element.getBoundingClientRect());
  if (!isUsableRect(rect)) return;

  regions.push({ kind, rect, polygon: [] });
}

function rectToPhysical(rect) {
  const ratio = window.devicePixelRatio || 1;
  return {
    x: Math.round(rect.left * ratio),
    y: Math.round(rect.top * ratio),
    width: Math.round(rect.width * ratio),
    height: Math.round(rect.height * ratio)
  };
}

function isUsableRect(rect) {
  return rect.width > 1 && rect.height > 1;
}

function renderHitboxOverlay(regions) {
  if (!els.hitboxOverlay) return;
  els.hitboxOverlay.replaceChildren();
  if (!state.hitboxOverlay) return;

  const ratio = window.devicePixelRatio || 1;
  for (const region of regions) {
    const marker = document.createElement("div");
    marker.className = "hitbox-overlay-shape";
    marker.dataset.kind = region.kind;
    marker.style.left = `${region.rect.x / ratio}px`;
    marker.style.top = `${region.rect.y / ratio}px`;
    marker.style.width = `${region.rect.width / ratio}px`;
    marker.style.height = `${region.rect.height / ratio}px`;

    const clipPath = buildOverlayClipPath(region);
    if (clipPath) marker.style.clipPath = clipPath;

    els.hitboxOverlay.append(marker);
  }
}

function buildOverlayClipPath(region) {
  if (!Array.isArray(region.polygon) || region.polygon.length < 3) return "";
  const width = Math.max(1, region.rect.width);
  const height = Math.max(1, region.rect.height);
  const points = region.polygon.map((point) => {
    const x = ((point.x - region.rect.x) / width) * 100;
    const y = ((point.y - region.rect.y) / height) * 100;
    return `${x.toFixed(2)}% ${y.toFixed(2)}%`;
  });
  return `polygon(${points.join(", ")})`;
}

let scaleTimer = 0;
function scheduleScaleCommit() {
  window.clearTimeout(scaleTimer);
  scaleTimer = window.setTimeout(commitVisualScale, 120);
}

async function commitVisualScale() {
  const geometry = await tauriCall("set_visual_scale", { scale: state.scale });
  if (geometry) Object.assign(state, geometry);
  scheduleNativeHitTestSync({ force: true });
  scheduleSave(0);
}

function scheduleSave(delay = 500) {
  if (!isTauriRuntime) return;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(saveNow, delay);
}

async function saveNow() {
  if (!isTauriRuntime) return;
  try {
    const geometry = await invoke("get_window_geometry");
    Object.assign(state, geometry);
    await invoke("save_pet_state", { state });
  } catch (error) {
    setStatus(`Save failed: ${formatError(error)}`);
  }
}

async function closePetWindow() {
  closeMenu();
  hideChatInput();
  interruptReply({ announce: false });
  await cancelVoiceRecording();
  if (!isTauriRuntime) {
    setStatus("Close is Tauri only");
    return;
  }

  await saveNow();
  await tauriCall("close_pet_app", {});
}

async function openSettingsWindow() {
  closeMenu();
  if (!isTauriRuntime) {
    setStatus("设置窗口仅 Tauri 可用");
    return;
  }
  await tauriCall("open_settings_window", {});
  scheduleSettingsSnapshot(120);
}

async function openWorkspaceWindow() {
  closeMenu();
  if (!isTauriRuntime) {
    setStatus("手边物品窗口仅 Tauri 可用");
    return;
  }
  await saveNow();
  await tauriCall("open_workspace_window", {});
  scheduleSettingsSnapshot(120);
}

async function updateBackendUrlFromInput() {
  await updateBackendUrl(els.backendUrl.value);
}

async function updateBackendUrl(value) {
  state.backendUrl = normalizeBackendUrl(value);
  els.backendUrl.value = state.backendUrl;
  clearBackendRetry();
  scheduleSave(0);
  setStatus("后端地址已保存，正在检查连接。", { durationMs: 1800 });
  await reloadCharacterResources({ userTriggered: true });
  void ensureBackendSession();
}

async function updateOutfitFromInput() {
  await updateOutfit(els.outfit.value);
}

async function updateOutfit(value) {
  const outfit = normalizeOutfitName(value);
  state.outfit = outfit || DEFAULT_OUTFIT;
  els.outfit.value = state.outfit;
  cancelEmotionPreview({ restore: true });
  scheduleSave(0);
  setStatus(`服装已设置：${state.outfit}`);
  await reloadCharacterResources({ userTriggered: true });
}

async function setAlwaysOnTop(enabled) {
  state.alwaysOnTop = Boolean(enabled);
  updateMenuLabels();
  await tauriCall("set_always_on_top", { enabled: state.alwaysOnTop });
  scheduleSave(0);
}

async function setSkipTaskbar(enabled) {
  state.skipTaskbar = Boolean(enabled);
  updateMenuLabels();
  await tauriCall("set_taskbar_visible", { visible: !state.skipTaskbar });
  scheduleSave(0);
}

async function setHitTestEnabled(enabled) {
  state.hitTestEnabled = Boolean(enabled);
  updateMenuLabels();
  await tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled });
  scheduleNativeHitTestSync({ force: true });
  scheduleSave(0);
}

function setHitboxOverlay(enabled) {
  state.hitboxOverlay = Boolean(enabled);
  applyVisualState();
  scheduleNativeHitTestSync({ force: true });
  scheduleSave(0);
}

function toggleWebglProbe() {
  const enabled = !els.stage.classList.contains("show-webgl");
  els.stage.classList.toggle("show-webgl", enabled);
  updateMenuLabels();
  scheduleSettingsSnapshot();
  if (enabled) startWebglProbe();
}

async function resetWindowPlacement() {
  Object.assign(state, {
    x: null,
    y: null,
    width: null,
    height: null,
    scale: 1
  });
  applyVisualState();
  await tauriCall("reset_window_geometry", {});
  await tauriCall("set_hit_test_enabled", { enabled: state.hitTestEnabled });
  scheduleNativeHitTestSync({ force: true });
  scheduleSave(0);
  setStatus("位置已重置");
}

async function resetVisuals() {
  state.scale = 1;
  state.opacity = 1;
  applyVisualState();
  await commitVisualScale();
  scheduleSave(0);
  setStatus("大小和透明度已恢复默认。", { durationMs: 1800 });
}

async function startNewSession() {
  interruptReply({ announce: false });
  state.sessionId = generateSessionId();
  lastTurnSignature = "";
  lastTurnTextKey = "";
  lastActivityActionSignature = "";
  cancelEmotionPreview({ restore: false });
  closeMenu();
  setPetEmotion(DEFAULT_EMOTION);
  setRuntimeStatus("新对话", { mode: "idle" });
  showBubbleText("新的对话已经准备好了。", { transient: true, durationMs: 2400 });
  scheduleSave(0);
  if (resourceState.health === "online") {
    await ensureBackendSession();
  }
  updateConnectionStatus();
}

async function reloadCharacterResources({ startup = false, userTriggered = false, silent = false } = {}) {
  resourceState.health = "checking";
  resourceState.healthMessage = "Checking";
  updateConnectionStatus();

  const healthy = await checkBackendHealth();
  if (!healthy) {
    useBundledResources();
    setPetEmotion(state.currentEmotion || DEFAULT_EMOTION, { force: true });
    scheduleBackendRetry();
    const message = "本地待机中：后端暂时连不上。";
    if (!silent && (startup || userTriggered)) showBubbleText(message, { transient: true, durationMs: 3200 });
    setRuntimeStatus(message, { mode: "offline" });
    return false;
  }

  try {
    clearBackendRetry();
    const manifest = await fetchResourceManifest();
    applyResourceManifest(manifest);
    setPetEmotion(state.currentEmotion || DEFAULT_EMOTION, { force: true });
    const count = getActiveEmotions().length;
    const message = `资源已加载：${getActiveOutfit().id} / ${count}`;
    if (!silent && userTriggered) showBubbleText(message, { transient: true });
    if (!silent || runtimeMode === "offline" || runtimeMode === "checking") {
      setRuntimeStatus(message, { mode: "idle" });
    }
    return true;
  } catch (error) {
    resourceState.healthMessage = formatError(error);
    useBundledResources();
    setPetEmotion(state.currentEmotion || DEFAULT_EMOTION, { force: true });
    const message = `资源暂时没拉到：${friendlyErrorMessage(formatError(error))}`;
    if (!silent && (startup || userTriggered)) showBubbleText(message, { transient: true, durationMs: 3600 });
    setRuntimeStatus(message, { mode: "error" });
    return false;
  } finally {
    updateConnectionStatus();
  }
}

async function checkBackendHealth() {
  const query = new URLSearchParams({
    user_id: state.sessionId || "desktop_pet_next_health",
    real_user_id: PROFILE_USER_ID,
    t: String(Date.now())
  });

  try {
    const response = await backendFetch(`${state.backendUrl}${DESKTOP_HEALTH_PATH}?${query.toString()}`, {
      method: "GET",
      cache: "no-store",
      connectTimeout: 3500
    });
    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
    const payload = await readJsonResponse(response);
    applyBackendHealthPayload(payload, { endpoint: DESKTOP_HEALTH_PATH, contractSource: "desktop_pet" });
    resourceState.health = "online";
    resourceState.healthMessage = "Connected";
    clearBackendRetry();
    updateConnectionStatus();
    return true;
  } catch (error) {
    return checkLegacyBackendHealth(error);
  }
}

async function checkLegacyBackendHealth(primaryError) {
  try {
    const response = await backendFetch(`${state.backendUrl}${LEGACY_HEALTH_PATH}?t=${Date.now()}`, {
      method: "GET",
      cache: "no-store",
      connectTimeout: 3500
    });
    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
    const payload = await readJsonResponse(response);
    applyBackendHealthPayload(payload, { endpoint: LEGACY_HEALTH_PATH, contractSource: "legacy" });
    resourceState.health = "online";
    resourceState.healthMessage = "Connected (legacy health)";
    clearBackendRetry();
    updateConnectionStatus();
    return true;
  } catch (legacyError) {
    resourceState.health = "offline";
    resourceState.healthMessage = formatError(primaryError || legacyError);
    resourceState.healthEndpoint = DESKTOP_HEALTH_PATH;
    resourceState.contractSource = "unavailable";
    scheduleBackendRetry();
    updateConnectionStatus();
    return false;
  }
}

function applyBackendHealthPayload(payload, { endpoint, contractSource } = {}) {
  const data = payload && typeof payload === "object" ? payload : {};
  const tts = data.tts && typeof data.tts === "object" ? data.tts : {};
  const asr = data.asr && typeof data.asr === "object" ? data.asr : {};
  resourceState.healthEndpoint = endpoint || LEGACY_HEALTH_PATH;
  resourceState.contractVersion = String(data.contract_version || data.contractVersion || "");
  resourceState.contractSource = contractSource || (resourceState.contractVersion ? "desktop_pet" : "legacy");
  resourceState.capabilities = Array.isArray(data.capabilities)
    ? data.capabilities.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  resourceState.endpoints = data.endpoints && typeof data.endpoints === "object" ? { ...data.endpoints } : {};
  resourceState.tts = {
    enabled: typeof tts.enabled === "boolean" ? tts.enabled : null,
    endpoint: String(tts.endpoint || resourceState.endpoints.tts || "/tts"),
    responseMediaType: String(tts.response_media_type || tts.responseMediaType || "audio/mpeg")
  };
  resourceState.asr = {
    available: Boolean(resourceState.endpoints.asr || asr.endpoint || resourceState.capabilities.includes("asr")),
    endpoint: String(asr.endpoint || resourceState.endpoints.asr || "/asr"),
    uploadField: String(asr.upload_field || asr.uploadField || "file")
  };
}

function scheduleBackendRetry(delay = BACKEND_RETRY_MS) {
  if (!isTauriRuntime || backendRetryTimer || resourceState.health === "online") return;
  backendRetryTimer = window.setTimeout(async () => {
    backendRetryTimer = 0;
    const recovered = await reloadCharacterResources({ silent: true });
    if (recovered) {
      void ensureBackendSession();
      if (!isReplyActive() && els.chatForm.hidden) {
        showBubbleText("后端已经连回来了。", { transient: true, durationMs: 2200 });
      }
    }
    scheduleSettingsSnapshot();
  }, delay);
  scheduleSettingsSnapshot();
}

function clearBackendRetry() {
  if (!backendRetryTimer) return;
  window.clearTimeout(backendRetryTimer);
  backendRetryTimer = 0;
  scheduleSettingsSnapshot();
}

async function fetchResourceManifest() {
  const query = new URLSearchParams({
    user_id: state.sessionId,
    real_user_id: PROFILE_USER_ID,
    t: String(Date.now())
  });
  const response = await backendFetch(buildBackendEndpointUrl("resource_manifest", "/resource-manifest", query), {
    method: "GET",
    cache: "no-store",
    connectTimeout: 5000
  });
  if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
  return response.json();
}

function applyResourceManifest(manifest) {
  syncResourceContractFromManifest(manifest);
  const outfits = Array.isArray(manifest?.characters?.outfits) ? manifest.characters.outfits : [];
  const defaultOutfit = getManifestDefaultOutfit(manifest);
  const outfit =
    findEntry(outfits, state.outfit) ||
    findEntry(outfits, defaultOutfit) ||
    findEntry(outfits, DEFAULT_OUTFIT) ||
    outfits[0] ||
    null;

  if (!outfit || !Array.isArray(outfit.emotions) || outfit.emotions.length === 0) {
    throw new Error("manifest has no character emotions");
  }

  const emotions = outfit.emotions
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      ...item,
      id: String(item.id || item.name || "").trim(),
      name: String(item.name || item.id || "").trim(),
      aliases: Array.isArray(item.aliases) ? item.aliases.map((alias) => String(alias || "").trim()).filter(Boolean) : [],
      url: resolveAssetUrl(item.path, state.backendUrl)
    }))
    .filter((item) => item.id && item.url);

  if (emotions.length === 0) {
    throw new Error("manifest emotions have no image paths");
  }

  resourceState.manifest = manifest;
  resourceState.outfit = {
    ...outfit,
    id: String(outfit.id || DEFAULT_OUTFIT),
    name: String(outfit.name || outfit.id || DEFAULT_OUTFIT),
    aliases: Array.isArray(outfit.aliases) ? outfit.aliases : [],
    emotions
  };
  resourceState.source = "manifest";
  resourceState.loadedAt = Date.now();
  state.outfit = resourceState.outfit.id;
}

function syncResourceContractFromManifest(manifest) {
  const desktop = getDesktopManifestContract(manifest);
  if (!desktop) return;
  resourceState.contractVersion = String(
    desktop.contract_version || desktop.contractVersion || resourceState.contractVersion || ""
  );
  if (resourceState.contractVersion) {
    resourceState.contractSource = "desktop_pet";
  }
}

function getDesktopManifestContract(manifest) {
  const clients = manifest?.clients;
  if (!clients || typeof clients !== "object") return null;
  const desktop = clients.desktop_pet;
  return desktop && typeof desktop === "object" ? desktop : null;
}

function getManifestDefaultOutfit(manifest) {
  const desktop = getDesktopManifestContract(manifest);
  return String(
    desktop?.default_outfit ||
      desktop?.defaultOutfit ||
      manifest?.defaults?.desktop_pet_outfit ||
      manifest?.defaults?.outfit ||
      DEFAULT_OUTFIT
  );
}

function getManifestDefaultEmotion(manifest) {
  const desktop = getDesktopManifestContract(manifest);
  return String(
    desktop?.default_emotion ||
      desktop?.defaultEmotion ||
      manifest?.defaults?.desktop_pet_emotion ||
      manifest?.defaults?.emotion ||
      DEFAULT_EMOTION
  );
}

function useBundledResources() {
  resourceState.manifest = null;
  resourceState.outfit = findEntry(localOutfits, state.outfit) || getDefaultLocalOutfit();
  resourceState.source = getLocalResourceSource();
  resourceState.loadedAt = Date.now();
  if (!state.outfit) state.outfit = resourceState.outfit.id;
}

async function ensureBackendSession({ restoreLatest = false } = {}) {
  if (resourceState.health !== "online") return null;
  try {
    const response = await backendFetch(buildBackendEndpointUrl("session_ensure", "/sessions/ensure", { t: Date.now() }), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      connectTimeout: 5000,
      body: JSON.stringify({
        user_id: state.sessionId,
        real_user_id: PROFILE_USER_ID,
        display_title: SESSION_DISPLAY_TITLE
      })
    });

    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
    const bundle = await response.json();
    if (restoreLatest && restoreLatestReply(bundle)) {
      setRuntimeStatus("已恢复上一轮回复", { mode: "idle" });
    } else {
      setRuntimeStatus("后端已就绪", { mode: "idle" });
    }
    return bundle;
  } catch (error) {
    resourceState.health = "offline";
    resourceState.healthMessage = formatError(error);
    scheduleBackendRetry();
    updateConnectionStatus();
    setRuntimeStatus(`本地待机中：${friendlyErrorMessage(formatError(error))}`, { mode: "offline" });
    return null;
  }
}

function restoreLatestReply(bundle) {
  const payload = bundle?.latest_final_json;
  if (!payload || typeof payload !== "object") return false;
  return renderPayload(payload, {
    source: "restore",
    speaking: false,
    persistEmotion: false,
    force: true
  });
}

function scheduleDesktopContextPoll({ immediate = false } = {}) {
  window.clearTimeout(desktopContextPollTimer);
  desktopContextPollTimer = 0;
  if (!isTauriRuntime || !state.desktopContextEnabled) return;

  const delay = immediate ? 0 : DESKTOP_CONTEXT_POLL_MS;
  desktopContextPollTimer = window.setTimeout(async () => {
    desktopContextPollTimer = 0;
    await refreshDesktopForegroundCache();
    scheduleDesktopContextPoll();
  }, delay);
}

async function refreshDesktopForegroundCache() {
  const snapshot = await tauriCall("get_desktop_context_snapshot", {}, { quiet: true });
  const foreground = normalizeForegroundContext(snapshot?.foreground);
  if (isUsableForegroundContext(foreground)) {
    lastDesktopForeground = {
      ...foreground,
      capturedAt: Number(snapshot?.capturedAt || Date.now())
    };
  }
}

async function collectDesktopContextForTurn() {
  if (!state.desktopContextEnabled) return null;

  let foreground = null;
  if (isTauriRuntime) {
    const snapshot = await tauriCall("get_desktop_context_snapshot", {}, { quiet: true });
    const current = normalizeForegroundContext(snapshot?.foreground);
    if (isUsableForegroundContext(current)) {
      foreground = current;
      lastDesktopForeground = {
        ...current,
        capturedAt: Number(snapshot?.capturedAt || Date.now())
      };
    }
  }

  if (!foreground && isFreshForegroundCache(lastDesktopForeground)) {
    foreground = normalizeForegroundContext(lastDesktopForeground);
  }

  const clipboard = await collectClipboardContext();
  if (!foreground && !clipboard.included) return null;

  return {
    ok: true,
    enabled: true,
    captured_at: Date.now(),
    platform: navigator.platform || "unknown",
    foreground: foreground || emptyForegroundContext("unavailable"),
    clipboard
  };
}

async function collectClipboardContext() {
  if (!state.clipboardContextEnabled) return { included: false };
  try {
    if (!navigator.clipboard?.readText) return { included: false, reason: "unsupported" };
    const raw = await navigator.clipboard.readText();
    const text = String(raw || "").trim();
    if (!text) return { included: true, text: "", empty: true, source: "web_clipboard" };
    return {
      included: true,
      text: text.slice(0, CLIPBOARD_TEXT_LIMIT),
      truncated: text.length > CLIPBOARD_TEXT_LIMIT,
      source: "web_clipboard"
    };
  } catch (error) {
    return {
      included: false,
      reason: "read_failed",
      error: formatError(error).slice(0, 160)
    };
  }
}

function scheduleScreenVisionCapture({ immediate = false } = {}) {
  window.clearTimeout(screenVisionTimer);
  screenVisionTimer = 0;
  if (!isTauriRuntime || !state.screenVisionEnabled) {
    screenVisionStatus = state.screenVisionEnabled ? screenVisionStatus : "off";
    return;
  }

  const delay = immediate ? 0 : SCREEN_VISION_FRAME_INTERVAL_MS;
  screenVisionTimer = window.setTimeout(async () => {
    screenVisionTimer = 0;
    await captureScreenVisionFrame();
    scheduleScreenVisionCapture();
  }, delay);
}

async function ensureScreenVisionCapture() {
  if (screenVisionStream && screenVisionVideo) {
    screenVisionStatus = "watching";
    return true;
  }
  if (!navigator.mediaDevices?.getDisplayMedia) {
    screenVisionStatus = "unsupported";
    screenVisionError = "当前 WebView 不支持屏幕捕获";
    return false;
  }
  try {
    screenVisionStream = await navigator.mediaDevices.getDisplayMedia({
      audio: false,
      video: {
        frameRate: { ideal: 2, max: 4 },
        width: { max: 1280 },
        height: { max: 720 }
      }
    });
    const [track] = screenVisionStream.getVideoTracks();
    if (track) {
      track.addEventListener("ended", () => {
        stopScreenVisionCapture({ clearRemote: true });
        state.screenVisionEnabled = false;
        scheduleSave(0);
        scheduleSettingsSnapshot();
      });
    }
    screenVisionVideo = document.createElement("video");
    screenVisionVideo.muted = true;
    screenVisionVideo.playsInline = true;
    screenVisionVideo.srcObject = screenVisionStream;
    await screenVisionVideo.play();
    screenVisionCanvas = document.createElement("canvas");
    screenVisionSampleCanvas = document.createElement("canvas");
    screenVisionStatus = "watching";
    screenVisionError = "";
    return true;
  } catch (error) {
    screenVisionStatus = "error";
    screenVisionError = formatError(error).slice(0, 160);
    stopScreenVisionCapture({ clearRemote: false });
    return false;
  }
}

async function captureScreenVisionFrame() {
  if (!state.screenVisionEnabled) return;
  if (!(await ensureScreenVisionCapture())) return;
  if (!screenVisionVideo?.videoWidth || !screenVisionVideo?.videoHeight) return;

  const frame = readCompressedScreenVisionFrame();
  if (!frame) return;
  const frameCount = normalizeScreenVisionFrameCount(state.screenVisionFrameCount);
  pushScreenVisionRecentFrame(frame, frameCount);
  if (state.screenVisionMode === "direct") {
    screenVisionStatus = "watching";
    scheduleSettingsSnapshot(120);
    return;
  }
  screenVisionFrames.push(frame);
  if (screenVisionFrames.length > frameCount) {
    screenVisionFrames = screenVisionFrames.slice(-frameCount);
  }
  if (screenVisionFrames.length < frameCount) return;

  const now = Date.now();
  if (now - screenVisionLastSubmitAt < state.screenVisionIntervalSec * 1000) return;
  await maybeSubmitScreenVisionClip();
}

function pushScreenVisionRecentFrame(frame, frameCount = normalizeScreenVisionFrameCount(state.screenVisionFrameCount)) {
  if (!frame) return;
  screenVisionRecentFrames.push(frame);
  if (screenVisionRecentFrames.length > frameCount) {
    screenVisionRecentFrames = screenVisionRecentFrames.slice(-frameCount);
  }
}

function latestDesktopScreenFramesForThink() {
  if (!state.screenVisionEnabled || state.screenVisionMode !== "direct") return [];
  const frameCount = normalizeScreenVisionFrameCount(state.screenVisionFrameCount);
  return screenVisionRecentFrames.slice(-frameCount).map((frame) => ({
    captured_at: frame.captured_at,
    width: frame.width,
    height: frame.height,
    data_url: frame.data_url
  }));
}

function readCompressedScreenVisionFrame() {
  const sourceWidth = screenVisionVideo.videoWidth;
  const sourceHeight = screenVisionVideo.videoHeight;
  if (!sourceWidth || !sourceHeight) return null;
  const scale = Math.min(1, SCREEN_VISION_MAX_EDGE / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  screenVisionCanvas.width = width;
  screenVisionCanvas.height = height;
  const ctx = screenVisionCanvas.getContext("2d", { alpha: false });
  if (!ctx) return null;
  ctx.drawImage(screenVisionVideo, 0, 0, width, height);

  const sample = sampleScreenVisionFrame(screenVisionCanvas);
  const dataUrl = screenVisionCanvas.toDataURL("image/jpeg", SCREEN_VISION_JPEG_QUALITY);
  return {
    captured_at: Math.floor(Date.now() / 1000),
    width,
    height,
    data_url: dataUrl,
    sample
  };
}

function sampleScreenVisionFrame(sourceCanvas) {
  screenVisionSampleCanvas.width = SCREEN_VISION_SAMPLE_WIDTH;
  screenVisionSampleCanvas.height = SCREEN_VISION_SAMPLE_HEIGHT;
  const sampleCtx = screenVisionSampleCanvas.getContext("2d", { alpha: false });
  if (!sampleCtx) return [];
  sampleCtx.drawImage(sourceCanvas, 0, 0, SCREEN_VISION_SAMPLE_WIDTH, SCREEN_VISION_SAMPLE_HEIGHT);
  const data = sampleCtx.getImageData(0, 0, SCREEN_VISION_SAMPLE_WIDTH, SCREEN_VISION_SAMPLE_HEIGHT).data;
  const sample = [];
  for (let i = 0; i < data.length; i += 4) {
    sample.push(Math.round((data[i] + data[i + 1] + data[i + 2]) / 3));
  }
  return sample;
}

async function maybeSubmitScreenVisionClip() {
  if (screenVisionStatus === "uploading" || screenVisionStatus === "observing") {
    return;
  }
  const frames = screenVisionFrames.slice(-normalizeScreenVisionFrameCount(state.screenVisionFrameCount));
  const first = frames[0];
  const last = frames[frames.length - 1];
  const foreground = normalizeForegroundContext(lastDesktopForeground) || emptyForegroundContext("unavailable");
  const foregroundKey = `${foreground.process_name}|${foreground.title}`;
  const diff = screenVisionLastSample ? sampleDifference(screenVisionLastSample, last.sample) : 100;
  const foregroundChanged = foregroundKey && foregroundKey !== screenVisionLastForegroundKey;
  const shouldSubmit =
    foregroundChanged ||
    diff >= SCREEN_VISION_DIFF_THRESHOLD ||
    screenVisionSkippedClips >= SCREEN_VISION_FORCE_AFTER_SKIPS;

  if (!shouldSubmit) {
    screenVisionSkippedClips += 1;
    screenVisionFrames = frames.slice(-1);
    screenVisionStatus = "quiet";
    scheduleSettingsSnapshot(120);
    return;
  }

  screenVisionLastSample = last.sample;
  screenVisionLastForegroundKey = foregroundKey;
  screenVisionLastSubmitAt = Date.now();
  screenVisionSkippedClips = 0;
  screenVisionStatus = "uploading";
  scheduleSettingsSnapshot(120);

  try {
    const response = await backendFetch(buildBackendEndpointUrl("screen_vision_clip", "/desktop-pet/vision/clip", { t: Date.now() }), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      connectTimeout: 20_000,
      body: JSON.stringify({
        user_id: state.sessionId,
        real_user_id: PROFILE_USER_ID,
        mode: "background",
        foreground,
        captured_start_ts: first.captured_at,
        captured_end_ts: last.captured_at,
        frames: frames.map((frame) => ({
          captured_at: frame.captured_at,
          width: frame.width,
          height: frame.height,
          data_url: frame.data_url
        }))
      })
    });
    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
    const payload = await response.json();
    screenVisionActiveClipId = String(payload?.clip?.clip_id || "");
    screenVisionStatus = "watching";
    screenVisionError = "";
  } catch (error) {
    screenVisionStatus = "error";
    screenVisionError = formatError(error).slice(0, 160);
  } finally {
    screenVisionFrames = frames.slice(-1);
    scheduleSettingsSnapshot();
  }
}

function sampleDifference(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || !left.length || left.length !== right.length) return 100;
  let total = 0;
  for (let i = 0; i < left.length; i += 1) {
    total += Math.abs(Number(left[i] || 0) - Number(right[i] || 0));
  }
  return total / left.length;
}

function stopScreenVisionCapture({ clearRemote = true } = {}) {
  window.clearTimeout(screenVisionTimer);
  screenVisionTimer = 0;
  if (screenVisionStream) {
    for (const track of screenVisionStream.getTracks()) {
      try {
        track.stop();
      } catch {
        // Ignore capture cleanup errors.
      }
    }
  }
  screenVisionStream = null;
  screenVisionVideo = null;
  screenVisionCanvas = null;
  screenVisionSampleCanvas = null;
  screenVisionFrames = [];
  screenVisionRecentFrames = [];
  screenVisionLastSample = null;
  screenVisionLastForegroundKey = "";
  screenVisionSkippedClips = 0;
  screenVisionStatus = "off";
  screenVisionActiveClipId = "";
  if (clearRemote) {
    void clearScreenVisionWorkspace({ quiet: true });
  }
}

async function clearScreenVisionWorkspace({ quiet = false } = {}) {
  screenVisionActiveClipId = "";
  try {
    await backendFetch(buildBackendEndpointUrl("screen_vision_clear", "/desktop-pet/vision/clear", { t: Date.now() }), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      connectTimeout: 5000,
      body: JSON.stringify({
        user_id: state.sessionId,
        real_user_id: PROFILE_USER_ID,
        scope: "session"
      })
    });
    if (!quiet) setRuntimeStatus("屏幕印象已清空", { mode: "idle" });
  } catch (error) {
    if (!quiet) setRuntimeStatus(`清空失败：${formatError(error)}`, { mode: "error" });
  } finally {
    scheduleSettingsSnapshot();
  }
}

function normalizeForegroundContext(value) {
  if (!value || typeof value !== "object") return null;
  return {
    title: String(value.title || "").trim(),
    process_name: String(value.process_name || value.processName || "").trim(),
    pid: Number.isFinite(Number(value.pid)) ? Number(value.pid) : null,
    source: String(value.source || "").trim() || "unknown"
  };
}

function emptyForegroundContext(source) {
  return {
    title: "",
    process_name: "",
    pid: null,
    source
  };
}

function isFreshForegroundCache(value) {
  if (!value) return false;
  const capturedAt = Number(value.capturedAt || value.captured_at || 0);
  return capturedAt > 0 && Date.now() - capturedAt <= DESKTOP_CONTEXT_MAX_AGE_MS;
}

function isUsableForegroundContext(value) {
  if (!value || value.source !== "foreground") return false;
  const processName = String(value.process_name || "").toLowerCase();
  const title = String(value.title || "").toLowerCase();
  if (!value.title && !value.process_name) return false;
  if (processName === "akane_desktop_pet_next.exe") return false;
  if (processName === "msedgewebview2.exe" && title.includes("akane")) return false;
  return true;
}

function interruptReply({ announce = false } = {}) {
  const hadActivity = isReplyActive();
  activeTurnToken += 1;
  sending = false;

  if (thinkController) {
    thinkController.abort();
    thinkController = null;
  }

  stopTts();
  clearLocalInteraction();
  lastTurnSignature = "";
  lastTurnTextKey = "";
  lastActivityActionSignature = "";
  window.clearTimeout(bubbleTimer);
  window.clearTimeout(segmentTimer);
  bubbleToken += 1;
  bubbleKind = "none";
  replyDisplayActive = false;

  if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
    setPetEmotion(DEFAULT_EMOTION);
  }
  setPetMotion("idle");

  if (announce) {
    showBubbleText(hadActivity ? "已停止回复。" : "现在没有正在回复的内容。", {
      transient: true,
      durationMs: hadActivity ? 1800 : 1500
    });
    setRuntimeStatus(hadActivity ? "已停止回复" : "空闲中", { mode: hadActivity ? "stopped" : "idle" });
  } else {
    hideBubble();
  }

  updateActivityControls();
  scheduleSettingsSnapshot();
  return hadActivity;
}

function isTurnActive(turnToken) {
  return turnToken === activeTurnToken;
}

function isReplyActive() {
  return sending || ttsActive || ttsQueue.length > 0 || replyDisplayActive;
}

function isAbortLike(error) {
  const name = String(error?.name || "").toLowerCase();
  const message = formatError(error).toLowerCase();
  return name === "aborterror" || message.includes("abort") || message.includes("cancel");
}

async function sendMessage(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return;

  rememberInputHistory(trimmed);
  interruptReply({ announce: false });
  const turnToken = ++activeTurnToken;
  sending = true;
  let restoreText = "";
  cancelEmotionPreview({ restore: true });
  clearLocalInteraction();
  lastTurnSignature = "";
  lastTurnTextKey = "";
  showThinking();
  scheduleSettingsSnapshot();

  try {
    if (resourceState.health !== "online") {
      const healthy = await reloadCharacterResources();
      if (!healthy) throw new Error("后端未连接");
    }
    if (!isTurnActive(turnToken)) return;
    const stream = sendThinkStream(trimmed, turnToken);
    const rendered = await processThinkStream(stream, turnToken);
    if (!rendered) throw new Error("未收到回复");
  } catch (error) {
    if (!isTurnActive(turnToken)) return;
    restoreText = trimmed;
    showError(isAbortLike(error) ? "请求超时" : formatError(error));
  } finally {
    if (isTurnActive(turnToken)) {
      sending = false;
      if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
        setPetEmotion(DEFAULT_EMOTION);
      }
      if (!els.bubble.classList.contains("visible")) {
        setPetMotion("idle");
      }
      if (restoreText) {
        restoreFailedInput(restoreText);
      }
      updateActivityControls();
      scheduleSettingsSnapshot();
    }
  }
}

function scheduleProactiveWake({ immediate = false, delayMs = null } = {}) {
  window.clearTimeout(proactiveWakeTimer);
  proactiveWakeTimer = 0;
  if (!isTauriRuntime || !state.proactiveWakeEnabled) return;
  const baseDelay = Number.isFinite(Number(delayMs))
    ? Number(delayMs)
    : immediate
      ? 1000
      : state.proactiveWakeIntervalSec * 1000;
  const jitter = immediate || Number.isFinite(Number(delayMs)) ? 1 : 0.85 + Math.random() * 0.3;
  proactiveWakeTimer = window.setTimeout(() => {
    proactiveWakeTimer = 0;
    void runProactiveWake();
  }, Math.max(1000, Math.round(baseDelay * jitter)));
}

async function runProactiveWake() {
  if (!state.proactiveWakeEnabled) return;
  if (!canStartProactiveWake()) {
    scheduleProactiveWake({ delayMs: PROACTIVE_WAKE_RETRY_MS });
    return;
  }
  await sendProactiveWake();
  scheduleProactiveWake();
}

function canStartProactiveWake() {
  if (proactiveWakeRunning || sending || ttsActive || ttsQueue.length > 0 || replyDisplayActive) return false;
  if (voiceInputState === "recording" || voiceInputState === "processing") return false;
  if (!els.chatForm.hidden || !els.menu.hidden) return false;
  if (localInteractionActive) return false;
  const bubbleVisible = els.bubble.classList.contains("visible");
  if (bubbleVisible && !["status", "vision", "none"].includes(bubbleKind)) return false;
  return true;
}

async function sendProactiveWake() {
  const turnToken = ++activeTurnToken;
  proactiveWakeRunning = true;
  sending = true;
  lastTurnSignature = "";
  lastTurnTextKey = "";
  scheduleSettingsSnapshot();

  try {
    if (resourceState.health !== "online") {
      const healthy = await reloadCharacterResources({ silent: true });
      if (!healthy) return;
    }
    if (!isTurnActive(turnToken)) return;
    const stream = sendThinkStream(PROACTIVE_WAKE_PROMPT, turnToken, {
      turnKind: "desktop_pet_proactive",
      transientUserMessage: true,
      desktopScreenFrames: latestDesktopScreenFramesForThink()
    });
    await processThinkStream(stream, turnToken);
    proactiveWakeLastAt = Date.now();
  } catch (error) {
    if (!isTurnActive(turnToken) || isAbortLike(error)) return;
    setRuntimeStatus(`主动搭话暂时失败：${formatError(error)}`, { mode: "error" });
  } finally {
    proactiveWakeRunning = false;
    if (isTurnActive(turnToken)) {
      sending = false;
      if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
        setPetEmotion(DEFAULT_EMOTION);
      }
      if (!els.bubble.classList.contains("visible")) {
        setPetMotion("idle");
      }
      updateActivityControls();
      scheduleSettingsSnapshot();
    }
  }
}

async function* sendThinkStream(message, turnToken, options = {}) {
  const controller = new AbortController();
  thinkController = controller;
  const timeoutId = window.setTimeout(() => controller.abort(), THINK_TIMEOUT_MS);
  const desktopContext = await collectDesktopContextForTurn();
  if (!isTurnActive(turnToken)) return;
  const requestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({
      user_id: state.sessionId,
      real_user_id: PROFILE_USER_ID,
      message,
      turn_kind: String(options.turnKind || ""),
      transient_user_message: Boolean(options.transientUserMessage),
      client_mode: CLIENT_MODE,
      client_capabilities: buildClientCapabilities(),
      current_visual: buildCurrentVisual(),
      desktop_context: desktopContext,
      desktop_screen_frames: Array.isArray(options.desktopScreenFrames) ? options.desktopScreenFrames : [],
      desktop_activity: buildDesktopMusicActivity()
    })
  };

  if (isTauriRuntime) {
    requestInit.connectTimeout = 30_000;
  } else {
    requestInit.signal = controller.signal;
  }

  let response;
  try {
    response = await backendFetch(buildBackendEndpointUrl("think", "/think", { t: Date.now() }), requestInit);
  } finally {
    window.clearTimeout(timeoutId);
    if (thinkController === controller) thinkController = null;
  }

  if (!isTurnActive(turnToken)) return;
  if (!response.ok) {
    throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
  }

  const raw = await response.text();
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length && raw.trim()) {
    lines.push(raw.trim());
  }

  for (const line of lines) {
    if (!isTurnActive(turnToken)) return;
    try {
      yield JSON.parse(line);
    } catch {
      // Skip malformed stream lines.
    }
  }
}

async function processThinkStream(stream, turnToken) {
  let partialSpeech = "";
  let rendered = false;
  let streamErrored = false;
  let streamErrorMessage = "";

  for await (const event of stream) {
    if (!isTurnActive(turnToken)) return false;
    const type = String(event?.type || "").trim().toLowerCase();

    if (type === "turn_start") {
      showThinking();
    } else if (type === "ui") {
      applyPayloadEmotion(event);
    } else if (type === "speech_chunk") {
      partialSpeech += String(event?.text || "");
    } else if (type === "final" || type === "final_ui") {
      const payload = event?.payload || event;
      if (renderPayload(payload)) rendered = true;
    } else if (type === "npc_turn") {
      if (!rendered && renderPayload(event)) rendered = true;
    } else if (type === "stream_error" || type === "error") {
      streamErrored = true;
      streamErrorMessage = String(event?.message || "Stream error");
      if (event?.partial && !rendered && renderPayload(event.partial)) rendered = true;
    } else if (type === "stream_end") {
      if (event?.partial && !rendered && renderPayload(event.partial)) rendered = true;
    }
  }

  if (!isTurnActive(turnToken)) return false;
  if (!rendered && partialSpeech.trim()) {
    rendered = renderPayload({ speech: partialSpeech.trim() });
  }

  if (!rendered && streamErrored) {
    throw new Error(streamErrorMessage || "未收到完整回应");
  }
  return rendered;
}

function renderPayload(
  payload,
  { source = "live", speaking = source === "live", persistEmotion = true, force = false } = {}
) {
  if (!payload || typeof payload !== "object") return false;
  applyPayloadEmotion(payload, { persist: persistEmotion });
  applyPayloadActivity(payload);

  const segments = normalizeSegments(payload.speech_segments || payload.segments);
  if (segments.length > 0) {
    const signature = `segments:${segments.join("\u241e")}`;
    const textKey = buildSpeechTextKey(segments.join(""));
    if (!force && (signature === lastTurnSignature || (textKey && textKey === lastTurnTextKey))) return false;
    lastTurnSignature = signature;
    lastTurnTextKey = textKey;
    showSpeechSegments(segments, { speaking });
    if (source === "live") setRuntimeStatus("回复中", { mode: "replying" });
    if (source === "live") queueTtsItems(segments, signature);
    return true;
  }

  const speech = String(payload.speech || payload.text || "").trim();
  if (!speech) return false;
  const clientSegments = splitSpeechText(speech);
  const displaySegments = clientSegments.length > 1 ? clientSegments : [];
  const signature = displaySegments.length ? `text-segments:${displaySegments.join("\u241e")}` : `text:${speech}`;
  const textKey = buildSpeechTextKey(displaySegments.length ? displaySegments.join("") : speech);
  if (!force && (signature === lastTurnSignature || (textKey && textKey === lastTurnTextKey))) return false;
  lastTurnSignature = signature;
  lastTurnTextKey = textKey;
  if (displaySegments.length) {
    showSpeechSegments(displaySegments, { speaking });
  } else {
    showBubbleText(speech, { transient: false, dismiss: true, speaking, kind: "reply" });
  }
  if (source === "live") {
    setRuntimeStatus("回复中", { mode: "replying" });
    queueTtsItems(displaySegments.length ? displaySegments : [speech], signature);
  }
  return true;
}

function applyPayloadEmotion(payload, { persist = true } = {}) {
  const emotion = String(payload?.emotion || "").trim();
  if (emotion) setPetEmotion(emotion, { persist });
}

function applyPayloadActivity(payload) {
  const activity = payload?.activity;
  if (!activity || typeof activity !== "object" || !musicTrack) return;
  const action = String(activity.action || "").trim().toLowerCase();
  const target = String(activity.target || activity.handle || "current").trim().toLowerCase();
  const sourceId = String(activity.source_id || activity.sourceId || "").trim();
  const requestedIndex = sourceId ? findMusicTrackIndexBySourceId(sourceId) : -1;
  const targetsCurrent =
    ["current", "", "local_music_current"].includes(target) ||
    sourceId === musicTrack.sourceId ||
    requestedIndex >= 0;
  if (!targetsCurrent) return;
  const signature = `${action}:${target}:${sourceId}:${musicTrack.sourceId}`;
  if (signature === lastActivityActionSignature) return;
  lastActivityActionSignature = signature;

  if ((action === "next" || action === "skip") && hasNextMusicTrack()) {
    void playNextMusicTrack();
  } else if ((action === "previous" || action === "prev") && hasPreviousMusicTrack()) {
    void playPreviousMusicTrack();
  } else if (action === "play" && requestedIndex >= 0 && requestedIndex !== musicQueueIndex) {
    void playMusicQueueIndex(requestedIndex, {
      message: `切到这首：《${musicQueue[requestedIndex].displayName}》。`
    });
  } else if (action === "pause" && musicPlaying) {
    els.musicPlayer.pause();
    musicPlaying = false;
    musicPaused = true;
    setMusicEmotion(false);
    setRuntimeStatus(`音乐已暂停：${getMusicDisplayName()}`, { mode: "music-paused" });
  } else if ((action === "resume" || action === "play") && musicPaused) {
    void toggleMusicPlayback();
  } else if (action === "stop") {
    stopMusic({ announce: false });
  } else {
    return;
  }
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function normalizeSegments(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (item && typeof item === "object") return item.text || item.speech || item.content || "";
      return item;
    })
    .map((item) => String(item || "").trim())
    .filter(Boolean);
}

function buildClientCapabilities() {
  const capabilities = [...BASE_CAPABILITIES];
  if (state.desktopContextEnabled) capabilities.push("desktop_context");
  if (state.screenVisionEnabled && state.screenVisionMode === "summary") capabilities.push("screen_vision");
  if (musicTrack) capabilities.push(AUDIO_PLAYBACK_CAPABILITY);
  return capabilities;
}

function buildSpeechTextKey(text) {
  return String(text || "").replace(/\s+/g, "").trim();
}

function splitSpeechText(text) {
  const source = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!source || source.length <= CLIENT_SEGMENT_SOFT_LIMIT) return [source].filter(Boolean);

  const sentenceChunks = source
    .split(/\n+/)
    .flatMap((line) => line.match(/[^。！？!?；;…]+[。！？!?；;…]*/g) || [line])
    .map((item) => item.trim())
    .filter(Boolean);

  const segments = [];
  let current = "";
  const pushCurrent = () => {
    if (!current.trim()) return;
    segments.push(current.trim());
    current = "";
  };

  for (const chunk of sentenceChunks) {
    if (chunk.length > CLIENT_SEGMENT_SOFT_LIMIT * 1.6) {
      pushCurrent();
      segments.push(...hardWrapText(chunk, CLIENT_SEGMENT_SOFT_LIMIT));
      continue;
    }

    const next = current ? `${current}${chunk}` : chunk;
    if (current && next.length > CLIENT_SEGMENT_SOFT_LIMIT) {
      pushCurrent();
      current = chunk;
    } else {
      current = next;
    }
  }

  pushCurrent();
  return limitClientSegments(segments.filter(Boolean));
}

function hardWrapText(text, limit) {
  const chunks = [];
  let rest = String(text || "").trim();
  while (rest.length > limit) {
    chunks.push(rest.slice(0, limit).trim());
    rest = rest.slice(limit).trim();
  }
  if (rest) chunks.push(rest);
  return chunks;
}

function limitClientSegments(segments) {
  if (segments.length <= CLIENT_SEGMENT_MAX) return segments;
  return [
    ...segments.slice(0, CLIENT_SEGMENT_MAX - 1),
    segments.slice(CLIENT_SEGMENT_MAX - 1).join("")
  ];
}

function showThinking() {
  setPetEmotion("thinking");
  setPetMotion("thinking");
  setRuntimeStatus("思考中", { mode: "thinking" });
  showBubbleText("……", { transient: false, kind: "thinking" });
}

function showError(message) {
  const friendly = friendlyErrorMessage(message);
  setTransientEmotion("confused", { durationMs: 3600 });
  setRuntimeStatus(friendly, { mode: "error" });
  showBubbleText(friendly, { transient: true, durationMs: 3500, kind: "error" });
}

function showSpeechSegments(segments, { speaking = true } = {}) {
  const items = Array.isArray(segments) ? segments.filter(Boolean) : [];
  if (!items.length) return;

  clearLocalInteraction();
  window.clearTimeout(bubbleTimer);
  window.clearTimeout(segmentTimer);
  const token = ++bubbleToken;
  bubbleKind = "reply";
  replyDisplayActive = true;
  let index = 0;

  const showNext = () => {
    if (token !== bubbleToken) return;
    const text = items[index];
    setBubbleContent(text);
    els.bubble.classList.add("visible");
    scheduleNativeHitTestSync({ force: true });
    if (speaking) setPetMotion("speaking");
    updateActivityControls();
    index += 1;

    if (index < items.length) {
      segmentTimer = window.setTimeout(
        showNext,
        Math.max(SEGMENT_MIN_MS, Math.min(SEGMENT_MAX_MS, text.length * SEGMENT_CHAR_RATE))
      );
      return;
    }

    scheduleBubbleReset(Math.max(text.length, 4), token);
  };

  showNext();
}

function showBubbleText(
  text,
  { transient = false, dismiss = false, durationMs = 1800, speaking = false, local = false, kind = "" } = {}
) {
  const nextKind = kind || (local ? "local" : "status");
  if (!local) clearLocalInteraction();
  window.clearTimeout(bubbleTimer);
  window.clearTimeout(segmentTimer);
  const token = ++bubbleToken;
  bubbleKind = text ? nextKind : "none";
  replyDisplayActive = text ? nextKind === "reply" : false;
  setBubbleContent(text || "");
  if (text) {
    els.bubble.classList.add("visible");
    if (speaking) setPetMotion("speaking");
  } else {
    els.bubble.classList.remove("visible");
  }
  scheduleNativeHitTestSync({ force: true });
  updateActivityControls();

  if (dismiss) {
    scheduleBubbleReset(Math.max(String(text || "").length, 4), token);
  } else if (transient) {
    bubbleTimer = window.setTimeout(() => {
      if (token !== bubbleToken) return;
      hideBubble(token);
    }, durationMs);
  }
}

function scheduleBubbleReset(charCount, token) {
  const ms = Math.max(3000, Math.min(15000, (charCount || 40) * 70));
  bubbleTimer = window.setTimeout(() => {
    if (token !== bubbleToken) return;
    hideBubble(token);
  }, ms);
}

function hideBubble(token = null) {
  if (token !== null && token !== bubbleToken) return;
  window.clearTimeout(bubbleTimer);
  window.clearTimeout(segmentTimer);
  if (token === null) bubbleToken += 1;
  if (bubbleKind !== "local") clearLocalInteraction();
  bubbleKind = "none";
  replyDisplayActive = false;
  els.bubble.classList.remove("visible");
  setBubbleContent("");
  scheduleNativeHitTestSync({ force: true });
  setPetMotion(ttsActive ? "speaking" : "idle");
  updateActivityControls();
}

function setBubbleContent(text) {
  const value = String(text || "").trim();
  els.bubbleText.textContent = value;
  els.bubble.dataset.size = getBubbleSizeForText(value);
}

function getBubbleSizeForText(text) {
  const value = String(text || "");
  if (!value) return "empty";
  const lineCount = value.split(/\r?\n/u).length;
  if (value.length <= 18 && lineCount <= 1) return "short";
  if (value.length >= 50 || lineCount >= 3) return "long";
  return "medium";
}

function setPetMotion(motion, { durationMs = 0 } = {}) {
  window.clearTimeout(motionTimer);
  const next = motion && motion !== "idle" ? motion : "idle";
  if (next === "click" && els.stage.dataset.motion === "click") {
    els.stage.dataset.motion = "idle";
    void els.stage.offsetWidth;
  }
  els.stage.dataset.motion = next;
  if (durationMs > 0) {
    motionTimer = window.setTimeout(() => {
      if (!sending) els.stage.dataset.motion = "idle";
    }, durationMs);
  }
}

function showMusicDropHint() {
  if (musicDropHover) return;
  musicDropHover = true;
  setRuntimeStatus(`把音频拖给 ${CHARACTER_NAME} 就可以播放`, { mode: "idle" });
  if (!sending && !replyDisplayActive) {
    showBubbleText("要放这首吗？", { transient: true, durationMs: 1400, kind: "music" });
  }
}

async function handleDroppedFiles(paths) {
  const files = Array.isArray(paths) ? paths.map((item) => String(item || "")).filter(Boolean) : [];
  const audioPaths = files.filter(isSupportedMusicPath);
  if (!audioPaths.length) {
    showBubbleText("这个暂时不像能播放的音频文件。", { transient: true, durationMs: 2400, kind: "music" });
    setRuntimeStatus("拖入的文件不是支持的音频格式", { mode: "error" });
    return;
  }
  await addDroppedAudioFiles(audioPaths);
}

function isSupportedMusicPath(path) {
  const extension = String(path || "").split(/[\\/]/u).pop()?.split(".").pop()?.toLowerCase() || "";
  return MUSIC_FILE_EXTENSIONS.has(extension);
}

async function addDroppedAudioFiles(paths) {
  if (!isTauriRuntime) return;
  musicLoading = true;
  updateActivityControls();
  scheduleSettingsSnapshot();
  setRuntimeStatus(paths.length > 1 ? `正在准备 ${paths.length} 首音乐` : "正在准备音乐", { mode: "music" });
  try {
    const tracks = [];
    const errors = [];
    for (const path of paths) {
      try {
        const asset = await invoke("prepare_audio_asset", { path });
        tracks.push(normalizeMusicTrack(asset));
      } catch (error) {
        errors.push(formatError(error));
      }
    }
    if (!tracks.length) {
      throw new Error(errors[0] || "没有可播放的音频文件");
    }
    await enqueueMusicTracks(tracks);
  } catch (error) {
    stopMusic({ silent: true });
    setRuntimeStatus(`音乐准备失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    showBubbleText("这首好像暂时放不了。", { transient: true, durationMs: 2200, kind: "music" });
  } finally {
    musicLoading = false;
    updateActivityControls();
    scheduleSettingsSnapshot();
  }
}

async function enqueueMusicTracks(tracks) {
  const items = Array.isArray(tracks) ? tracks.filter((track) => track?.cachedPath) : [];
  if (!items.length) return;
  const shouldStart = !musicTrack || musicQueueIndex < 0 || !musicQueue.length;
  if (shouldStart) {
    musicQueue = items;
    musicQueueIndex = 0;
    await playMusicQueueIndex(0, {
      message: items.length > 1 ? `收到，先放《${items[0].displayName}》。` : `收到，放《${items[0].displayName}》。`
    });
    return;
  }

  musicQueue.push(...items);
  const text = items.length > 1 ? `已加入 ${items.length} 首，队列现在 ${musicQueue.length} 首。` : `已加入队列：《${items[0].displayName}》。`;
  setRuntimeStatus(text, { mode: "music" });
  showBubbleText(text, { transient: true, durationMs: 2400, kind: "music" });
  updateActivityControls();
  scheduleSettingsSnapshot();
}

async function playMusicQueueIndex(index, { message = "" } = {}) {
  if (index < 0 || index >= musicQueue.length) return false;
  resetMusicElement();
  musicQueueIndex = index;
  musicTrack = musicQueue[index];
  if (!musicTrack.cachedPath) throw new Error("缺少可播放音频路径");

  els.musicPlayer.src = convertFileSrc(musicTrack.cachedPath);
  els.musicPlayer.currentTime = 0;
  els.musicPlayer.volume = state.voiceVolume;
  await els.musicPlayer.play();

  musicPlaying = true;
  musicPaused = false;
  setMusicEmotion(true);
  const name = getMusicDisplayName();
  const queueLabel = getMusicQueueLabel();
  setRuntimeStatus(`播放中：${name}${queueLabel ? ` · ${queueLabel}` : ""}`, { mode: "music" });
  if (message) showBubbleText(message, { transient: true, durationMs: 2400, kind: "music" });
  updateActivityControls();
  scheduleSettingsSnapshot();
  return true;
}

async function playNextMusicTrack({ auto = false } = {}) {
  if (!hasNextMusicTrack()) {
    if (!auto) showBubbleText("后面没有下一首啦。", { transient: true, durationMs: 1800, kind: "music" });
    return false;
  }
  const next = musicQueue[musicQueueIndex + 1];
  return playMusicQueueIndex(musicQueueIndex + 1, {
    message: auto ? `下一首，《${next.displayName}》。` : `切到下一首：《${next.displayName}》。`
  });
}

async function playPreviousMusicTrack() {
  if (!hasPreviousMusicTrack()) {
    showBubbleText("前面没有上一首啦。", { transient: true, durationMs: 1800, kind: "music" });
    return false;
  }
  const previous = musicQueue[musicQueueIndex - 1];
  return playMusicQueueIndex(musicQueueIndex - 1, {
    message: `切回上一首：《${previous.displayName}》。`
  });
}

async function handleMusicEnded() {
  if (!musicTrack) return;
  if (await playNextMusicTrack({ auto: true })) return;
  stopMusic({ ended: true });
}

async function toggleMusicPlayback() {
  if (!musicTrack) {
    showBubbleText("把音频文件拖给我就能放啦。", { transient: true, durationMs: 2200, kind: "music" });
    setRuntimeStatus("等待拖入音频文件", { mode: "idle" });
    return;
  }
  if (musicPlaying) {
    els.musicPlayer.pause();
    musicPlaying = false;
    musicPaused = true;
    setMusicEmotion(false);
    setRuntimeStatus(`音乐已暂停：${getMusicDisplayName()}`, { mode: "music-paused" });
  } else {
    try {
      await els.musicPlayer.play();
      musicPlaying = true;
      musicPaused = false;
      setMusicEmotion(true);
      const queueLabel = getMusicQueueLabel();
      setRuntimeStatus(`播放中：${getMusicDisplayName()}${queueLabel ? ` · ${queueLabel}` : ""}`, { mode: "music" });
    } catch (error) {
      setRuntimeStatus(`音乐继续失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    }
  }
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function stopMusic({ announce = false, ended = false, silent = false } = {}) {
  const hadTrack = Boolean(musicTrack);
  const name = getMusicDisplayName();
  musicPlaying = false;
  musicPaused = false;
  musicLoading = false;
  musicTrack = null;
  musicQueue = [];
  musicQueueIndex = -1;
  if (els.musicPlayer) {
    resetMusicElement();
  }
  setMusicEmotion(false);
  if (!silent && hadTrack) {
    const message = ended ? `《${name}》放完啦。` : `已停止音乐${name ? `：${name}` : ""}`;
    setRuntimeStatus(message, { mode: ended ? "idle" : "stopped" });
    if (announce || ended) {
      showBubbleText(ended ? "这首放完啦。" : "音乐停好啦。", { transient: true, durationMs: 1900, kind: "music" });
    }
  }
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function setMusicEmotion(active) {
  if (active) {
    musicEmotionActive = true;
    setPetEmotion(MUSIC_EMOTION, { persist: false });
    return;
  }
  if (musicEmotionActive && state.currentEmotion === resolveEmotionEntry(MUSIC_EMOTION).id) {
    setPetEmotion(DEFAULT_EMOTION, { persist: false });
  }
  musicEmotionActive = false;
}

function normalizeMusicTrack(asset) {
  const value = asset && typeof asset === "object" ? asset : {};
  const fileName = String(value.fileName || "audio");
  const cachedPath = String(value.cachedPath || "");
  return {
    originalPath: String(value.originalPath || ""),
    cachedPath,
    sourceId: `local:${fileName}:${simpleHash(cachedPath || fileName)}`,
    fileName,
    displayName: String(value.displayName || value.fileName || "未命名音乐"),
    extension: String(value.extension || "").toLowerCase(),
    sizeBytes: Number(value.sizeBytes || 0)
  };
}

function getMusicDisplayName() {
  return String(musicTrack?.displayName || musicTrack?.fileName || "").trim();
}

function resetMusicElement() {
  if (!els.musicPlayer) return;
  els.musicPlayer.pause();
  els.musicPlayer.removeAttribute("src");
  els.musicPlayer.load();
}

function hasPreviousMusicTrack() {
  return musicQueueIndex > 0 && musicQueueIndex < musicQueue.length;
}

function hasNextMusicTrack() {
  return musicQueueIndex >= 0 && musicQueueIndex < musicQueue.length - 1;
}

function getPreviousMusicTrack() {
  return hasPreviousMusicTrack() ? musicQueue[musicQueueIndex - 1] : null;
}

function getNextMusicTrack() {
  return hasNextMusicTrack() ? musicQueue[musicQueueIndex + 1] : null;
}

function getMusicQueueLabel() {
  if (!musicQueue.length || musicQueueIndex < 0) return "";
  return musicQueue.length > 1 ? `${musicQueueIndex + 1}/${musicQueue.length}` : "";
}

function summarizeMusicTrack(track) {
  if (!track || typeof track !== "object") return null;
  return {
    sourceId: track.sourceId,
    fileName: track.fileName,
    displayName: track.displayName,
    extension: track.extension,
    sizeBytes: track.sizeBytes
  };
}

function findMusicTrackIndexBySourceId(sourceId) {
  const normalized = String(sourceId || "").trim();
  if (!normalized) return -1;
  return musicQueue.findIndex((track) => track?.sourceId === normalized);
}

function buildMusicSnapshot() {
  const previous = getPreviousMusicTrack();
  const next = getNextMusicTrack();
  return {
    track: musicTrack ? { ...musicTrack } : null,
    queue: musicQueue.map(summarizeMusicTrack).filter(Boolean),
    queueIndex: musicQueueIndex,
    queueCount: musicQueue.length,
    queueLabel: getMusicQueueLabel(),
    hasPrevious: hasPreviousMusicTrack(),
    hasNext: hasNextMusicTrack(),
    previousDisplayName: previous?.displayName || "",
    nextDisplayName: next?.displayName || "",
    playing: musicPlaying,
    paused: musicPaused,
    loading: musicLoading,
    displayName: getMusicDisplayName()
  };
}

function simpleHash(value) {
  const text = String(value || "");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  }
  return Math.abs(hash).toString(36);
}

function buildDesktopMusicActivity() {
  if (!musicTrack) return null;
  const currentTime = Number(els.musicPlayer?.currentTime || 0);
  const duration = Number(els.musicPlayer?.duration || 0);
  return {
    type: "audio_playback",
    title: getMusicDisplayName() || "未命名音乐",
    source_id: musicTrack.sourceId || "local_music_current",
    handle: "current",
    status: musicPlaying ? "running" : musicPaused ? "paused" : "stopped",
    progress_seconds: Number.isFinite(currentTime) ? Math.max(0, currentTime) : 0,
    duration_seconds: Number.isFinite(duration) ? Math.max(0, duration) : 0,
    source_kind: "local_file",
    file_name: musicTrack.fileName,
    extension: musicTrack.extension,
    queue_count: musicQueue.length,
    queue_index: musicQueueIndex >= 0 ? musicQueueIndex + 1 : 0,
    queue_titles: musicQueue.map((track) => track.displayName).filter(Boolean).slice(0, 8),
    previous_title: getPreviousMusicTrack()?.displayName || "",
    next_title: getNextMusicTrack()?.displayName || ""
  };
}

function queueTtsItems(items, signature = "") {
  const normalized = (Array.isArray(items) ? items : [items])
    .map((item) => normalizeTtsText(item))
    .filter(Boolean);
  if (!state.voiceEnabled || !normalized.length) return;
  if (resourceState.tts?.enabled === false) {
    setRuntimeStatus("后端语音暂未开启", { mode: "error" });
    return;
  }
  if (signature && signature === lastTtsSignature) return;

  stopTts({ resetSignature: false });
  lastTtsSignature = signature || `tts:${normalized.join("\u241e")}`;
  ttsToken += 1;
  const token = ttsToken;
  ttsQueue = normalized;
  void runTtsQueue(token);
}

async function testTts() {
  if (!state.voiceEnabled) {
    setVoiceEnabled(true);
  }
  queueTtsItems([TTS_TEST_TEXT], `test:${Date.now()}`);
}

function stopTts({ resetSignature = true } = {}) {
  ttsToken += 1;
  ttsQueue = [];
  if (resetSignature) lastTtsSignature = "";
  if (ttsController) {
    ttsController.abort();
    ttsController = null;
  }
  finishTtsWait(false);
  stopTtsAudio();
  setTtsActive(false);
}

async function runTtsQueue(token) {
  setTtsActive(true);
  try {
    while (token === ttsToken && state.voiceEnabled && ttsQueue.length > 0) {
      const text = ttsQueue.shift();
      if (text) await playTtsText(text, token);
    }
  } finally {
    if (token === ttsToken) {
      ttsQueue = [];
      setTtsActive(false);
    }
  }
}

async function playTtsText(text, token) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), TTS_TIMEOUT_MS);
  ttsController = controller;

  try {
    const requestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ text })
    };
    if (isTauriRuntime) {
      requestInit.connectTimeout = 30_000;
    } else {
      requestInit.signal = controller.signal;
    }

    const response = await backendFetch(buildBackendEndpointUrl("tts", "/tts", { t: Date.now() }), requestInit);
    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `TTS HTTP ${response.status}`));

    const arrayBuffer = await response.arrayBuffer();
    if (token !== ttsToken || controller.signal.aborted) return;

    const contentType = response.headers.get("content-type") || "audio/mpeg";
    const blob = new Blob([arrayBuffer], { type: contentType });
    ttsObjectUrl = URL.createObjectURL(blob);
    els.voicePlayer.src = ttsObjectUrl;
    els.voicePlayer.currentTime = 0;
    els.voicePlayer.volume = state.voiceVolume;
    await els.voicePlayer.play();
    if (token !== ttsToken || controller.signal.aborted) return;
    await waitForTtsAudio(token);
  } catch (error) {
    if (!controller.signal.aborted && token === ttsToken) {
      setRuntimeStatus(`语音播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (ttsController === controller) ttsController = null;
    cleanupTtsObjectUrl();
  }
}

function waitForTtsAudio(token) {
  return new Promise((resolve) => {
    const cleanup = () => {
      els.voicePlayer.removeEventListener("ended", handleEnded);
      els.voicePlayer.removeEventListener("error", handleError);
      resolveTtsWait = null;
    };
    const finish = () => {
      cleanup();
      resolve(token === ttsToken);
    };
    const handleEnded = () => finish();
    const handleError = () => finish();
    resolveTtsWait = finish;
    els.voicePlayer.addEventListener("ended", handleEnded, { once: true });
    els.voicePlayer.addEventListener("error", handleError, { once: true });
  });
}

function finishTtsWait(completed) {
  if (!resolveTtsWait) return;
  const resolve = resolveTtsWait;
  resolveTtsWait = null;
  resolve(completed);
}

function stopTtsAudio() {
  if (!els.voicePlayer) return;
  els.voicePlayer.pause();
  els.voicePlayer.removeAttribute("src");
  els.voicePlayer.load();
  cleanupTtsObjectUrl();
}

function cleanupTtsObjectUrl() {
  if (!ttsObjectUrl) return;
  URL.revokeObjectURL(ttsObjectUrl);
  ttsObjectUrl = "";
}

function setTtsActive(active) {
  const next = Boolean(active);
  if (ttsActive === next) return;
  ttsActive = next;
  if (ttsActive) {
    setPetMotion("speaking");
    setRuntimeStatus("语音播放中", { mode: "speaking" });
  } else {
    if (!els.bubble.classList.contains("visible")) {
      setPetMotion("idle");
    }
    updateActivityControls();
    scheduleSettingsSnapshot();
  }
}

function normalizeTtsText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

async function probeClickThrough(durationMs) {
  if (!isTauriRuntime) {
    setStatus("仅 Tauri 可用");
    return;
  }

  setStatus("临时穿透中");
  try {
    state.clickThrough = true;
    await invoke("set_click_through", { enabled: true });
    window.setTimeout(async () => {
      state.clickThrough = false;
      await invoke("set_click_through", { enabled: false });
      setStatus("已恢复交互");
    }, durationMs);
  } catch (error) {
    state.clickThrough = false;
    setStatus(`穿透失败：${formatError(error)}`);
  }
}

async function tauriCall(command, args, { quiet = false } = {}) {
  if (!isTauriRuntime) return null;
  try {
    return await invoke(command, args);
  } catch (error) {
    if (!quiet) setStatus(`${command}: ${formatError(error)}`);
    return null;
  }
}

function backendFetch(input, init) {
  if (isTauriRuntime) {
    return tauriFetch(input, init);
  }
  return window.fetch(input, init);
}

function buildBackendEndpointUrl(name, fallbackPath, params = null) {
  const endpoint = getBackendEndpoint(name, fallbackPath);
  const base = `${state.backendUrl.replace(/\/+$/, "")}/`;
  const url = new URL(endpoint, base);
  const entries =
    params instanceof URLSearchParams
      ? [...params.entries()]
      : Object.entries(params || {});
  for (const [key, value] of entries) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function getBackendEndpoint(name, fallbackPath) {
  const endpoints = resourceState.endpoints && typeof resourceState.endpoints === "object" ? resourceState.endpoints : {};
  const specialized =
    name === "tts"
      ? resourceState.tts?.endpoint
      : name === "asr"
        ? resourceState.asr?.endpoint
        : "";
  const value = String(specialized || endpoints[name] || fallbackPath || "").trim();
  if (!value) return "/";
  if (/^https?:\/\//i.test(value)) return value;
  return value.startsWith("/") ? value : `/${value}`;
}

function setStatus(message, { transient = true, durationMs = 1800 } = {}) {
  setRuntimeStatus(message);
  showBubbleText(message, { transient, durationMs, kind: "status" });
}

function setRuntimeStatus(message, { mode = null } = {}) {
  if (mode) runtimeMode = mode;
  els.status.textContent = message;
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function updateActivityControls() {
  if (els.stopReply) {
    els.stopReply.disabled = !isReplyActive();
  }
  if (els.toggleMusic) {
    els.toggleMusic.disabled = !musicTrack || musicLoading;
    els.toggleMusic.textContent = musicPlaying ? "暂停音乐" : musicTrack ? "继续音乐" : "音乐";
  }
  if (els.stopMusic) {
    els.stopMusic.disabled = !musicTrack && !musicLoading;
  }
  if (els.previousMusic) {
    els.previousMusic.disabled = musicLoading || !hasPreviousMusicTrack();
  }
  if (els.nextMusic) {
    els.nextMusic.disabled = musicLoading || !hasNextMusicTrack();
  }
}

function updateConnectionStatus() {
  const healthLabel = {
    online: "已连接",
    offline: "离线",
    checking: "检查中",
    unknown: "未知"
  }[resourceState.health] || "未知";
  const outfit = getActiveOutfit();
  const count = getActiveEmotions().length;
  const source = resourceSourceLabel(resourceState.source);
  const contract = resourceState.contractVersion || (resourceState.contractSource === "legacy" ? "legacy" : "");
  els.connectionStatus.textContent = `后端：${healthLabel}${contract ? ` · ${contract}` : ""} · ${source} · ${outfit.id}(${count})`;
  els.connectionStatus.title = `点击重新检查后端与资源${resourceState.healthEndpoint ? ` · ${resourceState.healthEndpoint}` : ""}`;
  updateMenuLabels();
}

function setTransientEmotion(emotion, { durationMs = 2400 } = {}) {
  const token = ++transientEmotionToken;
  window.clearTimeout(transientEmotionTimer);
  const resolved = setPetEmotion(emotion, { persist: false });
  transientEmotionTimer = window.setTimeout(() => {
    if (token !== transientEmotionToken || sending || ttsActive || voiceInputState === "recording") return;
    setPetEmotion(musicPlaying ? MUSIC_EMOTION : DEFAULT_EMOTION, { persist: false });
  }, durationMs);
  return resolved;
}

function clearTransientEmotionRestore() {
  transientEmotionToken += 1;
  window.clearTimeout(transientEmotionTimer);
  transientEmotionTimer = 0;
}

function setPetEmotion(emotion, { persist = true, force = false } = {}) {
  if (persist || force) clearTransientEmotionRestore();
  if (persist && previewEmotionRestore) {
    cancelEmotionPreview({ restore: false });
  }
  const entry = resolveEmotionEntry(emotion);
  if (!force && state.currentEmotion === entry.id && els.petImage.src) return entry.id;
  state.currentEmotion = entry.id;
  els.petImage.src = entry.url;
  updateMenuLabels();
  if (persist) scheduleSave(0);
  return entry.id;
}

function resolveEmotionEntry(value) {
  const emotions = getActiveEmotions();
  const candidates = buildEmotionCandidates(value);
  for (const candidate of candidates) {
    const match = findEntry(emotions, candidate);
    if (match) return match;
  }
  return findEntry(emotions, DEFAULT_EMOTION) || findEntry(emotions, "normal") || emotions[0] || getDefaultLocalOutfit().emotions[0];
}

function buildEmotionCandidates(value) {
  const raw = String(value || "").trim();
  const result = [];
  const add = (item) => {
    const text = String(item || "").trim();
    if (text && !result.includes(text)) result.push(text);
  };

  add(raw);
  const key = normalizeEntryKey(raw);
  for (const item of COMMON_EMOTION_CANDIDATES[key] || []) add(item);
  add(DEFAULT_EMOTION);
  add("normal");
  return result;
}

function buildCurrentVisual() {
  const outfit = getActiveOutfit();
  const emotions = getActiveEmotions();
  const emotion = resolveEmotionEntry(state.currentEmotion).id;
  return {
    emotion,
    character: {
      outfit: outfit.id,
      available_emotions: emotions.map((item) => ({
        id: item.id,
        name: item.name || item.id,
        aliases: Array.isArray(item.aliases) ? item.aliases : []
      }))
    },
    scene: {},
    available_emotions: emotions.map((item) => item.id)
  };
}

function getActiveOutfit() {
  return resourceState.outfit || getDefaultLocalOutfit();
}

function getActiveEmotions() {
  const emotions = Array.isArray(getActiveOutfit()?.emotions) ? getActiveOutfit().emotions : [];
  return emotions.length ? emotions : getDefaultLocalOutfit().emotions;
}

function getManifestOutfits() {
  return Array.isArray(resourceState.manifest?.characters?.outfits)
    ? resourceState.manifest.characters.outfits.filter((item) => item && typeof item === "object")
    : [];
}

function getAvailableOutfits() {
  const outfits = getManifestOutfits();
  return outfits.length ? outfits : localOutfits;
}

function serializeOutfit(outfit) {
  const id = String(outfit?.id || outfit?.name || "").trim();
  const name = String(outfit?.name || outfit?.id || "").trim();
  const aliases = normalizeAliases(outfit?.aliases);
  const emotions = listOutfitEmotions(outfit);
  const issues = buildResourceIssues(outfit, emotions);
  const active = findEntry([outfit], getActiveOutfit().id) !== null;
  return {
    id,
    name,
    aliases,
    active,
    source: resourceState.source,
    emotionCount: emotions.length,
    allowedEmotionCount: Array.isArray(outfit?.allowed_emotions) ? outfit.allowed_emotions.length : 0,
    missingRequired: issues.missingRequired,
    missingRecommended: issues.missingRecommended
  };
}

function serializeEmotion(emotion) {
  const id = String(emotion?.id || emotion?.name || "").trim();
  return {
    id,
    name: String(emotion?.name || emotion?.id || "").trim(),
    aliases: normalizeAliases(emotion?.aliases),
    path: String(emotion?.path || "").trim(),
    url: String(emotion?.url || "").trim()
  };
}

function listOutfitEmotions(outfit) {
  return (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      ...item,
      id: String(item.id || item.name || "").trim(),
      name: String(item.name || item.id || "").trim(),
      aliases: normalizeAliases(item.aliases)
    }))
    .filter((item) => item.id);
}

function buildResourceIssues(outfit, emotions = listOutfitEmotions(outfit)) {
  const keys = new Set(
    (Array.isArray(emotions) ? emotions : [])
      .flatMap((item) => [item.id, item.name, ...normalizeAliases(item.aliases)])
      .map(normalizeEntryKey)
      .filter(Boolean)
  );
  return {
    missingRequired: REQUIRED_EMOTIONS.filter((item) => !keys.has(normalizeEntryKey(item))),
    missingRecommended: RECOMMENDED_EMOTIONS.filter((item) => !keys.has(normalizeEntryKey(item)))
  };
}

function resourceSourceLabel(source) {
  return {
    manifest: "后端资源",
    character_pack: "角色包资源",
    bundled: "内置资源"
  }[String(source || "")] || "本地资源";
}

function getLocalResourceSource() {
  return characterPackOutfits.length ? "character_pack" : "bundled";
}

function getDefaultLocalOutfit() {
  return findEntry(localOutfits, DEFAULT_OUTFIT) || localOutfits[0] || bundledOutfit;
}

function buildCharacterPackOutfits() {
  const grouped = new Map();
  for (const [path, url] of Object.entries(characterPackCharacterAssets)) {
    const match = path.match(/\/assets\/characters\/([^/]+)\/([^/]+)\.(png|jpe?g|webp)$/i);
    if (!match) continue;
    const outfitId = decodeURIComponent(match[1] || "").trim();
    const emotionId = decodeURIComponent(match[2] || "").trim();
    if (!outfitId || !emotionId || !url) continue;
    const entry = grouped.get(outfitId) || [];
    entry.push({
      id: emotionId,
      name: emotionId,
      aliases: [],
      url: String(url || ""),
      path
    });
    grouped.set(outfitId, entry);
  }

  return [...grouped.entries()]
    .map(([outfitId, emotions]) => ({
      id: outfitId,
      name: outfitId,
      aliases: [],
      emotions: sortEmotions(emotions)
    }))
    .filter((outfit) => outfit.emotions.length)
    .sort((a, b) => {
      if (a.id === DEFAULT_OUTFIT) return -1;
      if (b.id === DEFAULT_OUTFIT) return 1;
      return a.id.localeCompare(b.id, "zh-CN");
    });
}

function buildBundledOutfit() {
  const emotions = Object.entries(bundledCharacterAssets)
    .map(([path, url]) => {
      const id = decodeURIComponent(path.split("/").pop()?.replace(/\.(png|jpe?g|webp)$/i, "") || "");
      return {
        id,
        name: id,
        aliases: [],
        url: String(url || "")
      };
    })
    .filter((item) => item.id && item.url)
    .sort(compareEmotionEntries);

  return {
    id: DEFAULT_OUTFIT,
    name: DEFAULT_OUTFIT,
    aliases: [],
    emotions
  };
}

function sortEmotions(emotions) {
  return [...emotions].sort(compareEmotionEntries);
}

function compareEmotionEntries(a, b) {
  if (a.id === DEFAULT_EMOTION) return -1;
  if (b.id === DEFAULT_EMOTION) return 1;
  return String(a.id || "").localeCompare(String(b.id || ""), "zh-CN");
}

function findEntry(items, value) {
  const raw = String(value || "").trim();
  if (!raw || !Array.isArray(items)) return null;
  const key = normalizeEntryKey(raw);
  return (
    items.find((item) => {
      if (!item || typeof item !== "object") return false;
      return entryLookupValues(item).some((option) => option === raw || normalizeEntryKey(option) === key);
    }) || null
  );
}

function entryLookupValues(entry) {
  const values = [];
  for (const key of ["id", "name"]) {
    const value = String(entry?.[key] || "").trim();
    if (value && !values.includes(value)) values.push(value);
  }
  for (const alias of entry?.aliases || []) {
    const value = String(alias || "").trim();
    if (value && !values.includes(value)) values.push(value);
  }
  return values;
}

function normalizeAliases(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);
}

function normalizeEntryKey(value) {
  return String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
}

function resolveAssetUrl(path, backendUrl) {
  const raw = String(path || "").trim();
  if (!raw) return "";
  if (/^(https?:|file:|data:|blob:)/i.test(raw)) return encodeURI(raw);
  const base = String(backendUrl || "").trim().replace(/\/+$/, "");
  if (!base) return encodeURI(raw);
  if (raw.startsWith("/")) return encodeURI(`${base}${raw}`);
  return encodeURI(`${base}/${raw.replace(/^\/+/, "")}`);
}

function startWebglProbe() {
  if (webglProbe) {
    webglProbe.running = true;
    webglProbe.frame = requestAnimationFrame(renderWebglProbe);
    return;
  }

  const gl = els.canvas.getContext("webgl", {
    alpha: true,
    premultipliedAlpha: false,
    antialias: true
  });

  if (!gl) {
    setStatus("WebGL unavailable");
    return;
  }

  const vertexSource = `
    attribute vec2 position;
    uniform float time;
    void main() {
      float sway = sin(time + position.y * 2.4) * 0.08;
      gl_Position = vec4(position.x + sway, position.y, 0.0, 1.0);
    }
  `;
  const fragmentSource = `
    precision mediump float;
    uniform float time;
    void main() {
      gl_FragColor = vec4(1.0, 0.28 + sin(time) * 0.12, 0.42, 0.72);
    }
  `;

  const program = createProgram(gl, vertexSource, fragmentSource);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([
      -0.22, -0.2,
      0.22, -0.2,
      0, 0.3
    ]),
    gl.STATIC_DRAW
  );

  webglProbe = {
    gl,
    program,
    buffer,
    position: gl.getAttribLocation(program, "position"),
    time: gl.getUniformLocation(program, "time"),
    running: true,
    startedAt: performance.now(),
    frame: 0
  };

  setStatus("WebGL ready");
  webglProbe.frame = requestAnimationFrame(renderWebglProbe);
}

function renderWebglProbe(now) {
  if (!webglProbe?.running || !els.stage.classList.contains("show-webgl")) {
    if (webglProbe) webglProbe.running = false;
    return;
  }

  const { gl, program, position, time } = webglProbe;
  resizeCanvasToDisplaySize(els.canvas);
  gl.viewport(0, 0, els.canvas.width, els.canvas.height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(program);
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  gl.uniform1f(time, (now - webglProbe.startedAt) / 1000);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  webglProbe.frame = requestAnimationFrame(renderWebglProbe);
}

function createProgram(gl, vertexSource, fragmentSource) {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? "WebGL link failed");
  }
  return program;
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) ?? "WebGL compile failed");
  }
  return shader;
}

function resizeCanvasToDisplaySize(canvas) {
  const width = Math.max(1, Math.floor(canvas.clientWidth * window.devicePixelRatio));
  const height = Math.max(1, Math.floor(canvas.clientHeight * window.devicePixelRatio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
}

function normalizeBackendUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "") || DEFAULT_BACKEND_URL;
}

function normalizeOutfitName(value) {
  return String(value || "").trim() || DEFAULT_OUTFIT;
}

function generateSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `desktop_pet_next_${crypto.randomUUID()}`;
  }
  return `desktop_pet_next_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

function readCssPx(name, fallback) {
  const value = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 10 ? text.slice(-10) : text;
}

function formatError(error) {
  if (error?.name === "AbortError") return "请求超时";
  return error instanceof Error ? error.message : String(error);
}

function friendlyErrorMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "请求失败，稍后再试。";
  if (/麦克风|microphone|notallowed|securityerror|permission/i.test(text)) return "没有麦克风权限。";
  if (/notfound|devicesnotfound|no device/i.test(text)) return "没有找到可用麦克风。";
  if (/ASR|语音识别|录音/i.test(text)) return "语音识别暂时失败，可以再试一次。";
  if (/TTS|语音播放/i.test(text)) return "语音播放暂时失败，文字回复还在。";
  if (/workspace|手边|summary/i.test(text)) return "手边物品暂时打不开，请确认后端已经启动。";
  if (/后端未连接|failed to fetch|connection|network|fetch|dns|refused|timed out|timeout|请求超时/i.test(text)) {
    return "后端暂时连不上，请确认服务已经启动。";
  }
  if (/HTTP 5\d\d/i.test(text)) return "后端处理时出错了，稍后再试。";
  if (/HTTP 4\d\d/i.test(text)) return "请求没有被后端接受。";
  return text.length > 44 ? `${text.slice(0, 44)}…` : text;
}
