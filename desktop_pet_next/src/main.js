import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { emit, emitTo, listen } from "@tauri-apps/api/event";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { currentMonitor, getCurrentWindow, primaryMonitor } from "@tauri-apps/api/window";

import {
  APP_DISPLAY_NAME,
  CHARACTER_NAME,
  COMMON_EMOTION_CANDIDATES,
  DEFAULT_EMOTION,
  DEFAULT_OUTFIT,
  LOCAL_CLICK_LINES,
  MUSIC_EMOTION,
  REQUIRED_EMOTIONS,
  RECOMMENDED_EMOTIONS,
  buildCharacterSnapshot,
  getActiveCharacterPackId,
  getActiveCharacterProfile,
  getActiveCharacterText,
  listCharacterPacks,
  selectCharacterPack,
  setRuntimeCharacterPacks
} from "./character-profile.js";
import { createVisualRenderer } from "./visual-renderer.js";
import "./styles.css";

const bundledCharacterAssets = import.meta.glob("./assets/characters/猫娘/*.{png,jpg,jpeg,webp}", {
  eager: true,
  import: "default",
  query: "?url"
});
// Character-pack portraits are loaded from disk through Tauri at runtime.
const characterPackCharacterAssets = {};

const isTauriRuntime = Boolean(window.__TAURI_INTERNALS__);
const appWindow = isTauriRuntime ? getCurrentWindow() : null;

const DEFAULT_BACKEND_URL = "http://127.0.0.1:9999";
const PROFILE_USER_ID = "master";
const CLIENT_MODE = "desktop_pet";

const DESKTOP_HEALTH_PATH = "/desktop-pet/health";
const LEGACY_HEALTH_PATH = "/health";
const BASE_CAPABILITIES = ["speech_segments", "tts", "file_drop", "tool_actions"];
const AUDIO_PLAYBACK_CAPABILITY = "audio_playback";
const THINK_TIMEOUT_MS = 5 * 60 * 1000;
const TTS_TIMEOUT_MS = 45 * 1000;
const TTS_SLOW_REQUEST_MS = 1200;
const TTS_CHUNK_SOFT_LIMIT = 24;
const TTS_PREWARM_TEXT = "嗯。";
const TTS_PREWARM_DELAY_MS = 650;
const TTS_PREWARM_TIMEOUT_MS = 12 * 1000;
const TTS_PREWARM_COOLDOWN_MS = 10 * 60 * 1000;
const ASR_TIMEOUT_MS = 2 * 60 * 1000;
const DESKTOP_CONTEXT_POLL_MS = 1500;
const DESKTOP_CONTEXT_TURN_WAIT_MS = 280;
const DESKTOP_CONTEXT_TURN_WAIT_FOCUSED_MS = 1200;
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
const PROACTIVE_WAKE_RETRY_MS = 15000;
const MUSIC_TIMELINE_POLL_MS = 8000;
const MUSIC_TIMELINE_RETRY_MS = 30000;
const MUSIC_TIMELINE_INITIAL_DELAY_MS = 6500;
const SYSTEM_MEDIA_POLL_MS = 2000;
const SYSTEM_MEDIA_MAX_AGE_MS = 10000;
const SYSTEM_MEDIA_LYRICS_RETRY_MS = 30000;
const SYSTEM_MEDIA_LYRICS_TURN_WAIT_MS = 1200;
const SYSTEM_MEDIA_LYRICS_TURN_WAIT_FOCUSED_MS = 2400;
const CLIPBOARD_TEXT_LIMIT = 600;
const BACKEND_RETRY_MS = 30 * 1000;
const WORKSPACE_TASK_POLL_MS = 12 * 1000;
const WORKSPACE_TASK_IDLE_POLL_MS = 30 * 1000;
const WORKSPACE_TASK_RECENT_UPDATE_MS = 5 * 60 * 1000;
const VOICE_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4"
];
const MUSIC_FILE_EXTENSIONS = new Set(["mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "webm"]);
const MUSIC_LYRIC_EXTENSIONS = new Set(["lrc"]);
const MUSIC_PLAY_MODES = Object.freeze(["列表循环", "单曲循环", "随机播放"]);
const MIN_RECORDING_MS = 700;
const SEGMENT_MIN_MS = 1200;
const SEGMENT_MAX_MS = 4500;
const SEGMENT_CHAR_RATE = 120;
const CLIENT_SEGMENT_SOFT_LIMIT = 56;
const CLIENT_SEGMENT_MAX = 5;
const LOCAL_CLICK_DELAY_MS = 240;
const INPUT_HISTORY_LIMIT = 24;
const CHAT_INPUT_IDLE_HIDE_MS = 5000;
const PET_PHYSICS_FRAME_MS = 20;
const PET_PHYSICS_GRAVITY = 0.8;
const PET_PHYSICS_GROUND_FRICTION = 0.94;
const PET_PHYSICS_AIR_FRICTION = 0.99;
const PET_PHYSICS_BOUNCE = 0.4;
const PET_DRAG_THRESHOLD_PX = 5;
const PET_DRAG_VELOCITY_FACTOR = 0.4;
const PET_THROW_THRESHOLD = 8;
const PET_WALL_PAIN_THRESHOLD = 7;
const PET_MOTION_RESTORE_MS = 1800;
const PET_IDLE_JUMP_AFTER_MS = 90000;
const PET_IDLE_JUMP_COOLDOWN_MS = 150000;
const PET_PHYSICS_MIN_SPEED = 0.2;
const PET_PHYSICS_FLOOR_CLEARANCE = 18;
const MUSIC_EMOTION_RESTORE_DELAY_MS = 1800;
const CARE_PASSIVE_TICK_MS = 60 * 1000;
const CARE_DEFAULT_HUNGER_DECAY_PER_HOUR = 4;
const CARE_DEFAULT_ENERGY_COST_PER_REPLY = 1;
const CARE_DEFAULT_ENERGY_COST_PER_PROACTIVE = 0;
const PROACTIVE_WAKE_STYLE_GUARD = [
  "本轮是主动搭话，不是用户提问。",
  "可以参考桌面线索，但不要把窗口标题或软件名当成必须回应的主题；只有标题时最多当背景。",
  "优先轻短地陪一句、提醒一句，或自然问候；没有新线索也可以不围绕屏幕聊。",
  "回复尽量短，1 到 2 个自然小气泡。"
].join("\n");
const SCALE_MIN = 0.75;
const SCALE_MAX = 1.45;
const SCALE_PRESETS = [0.85, 1, 1.15, 1.3];
const OPACITY_PRESETS = [1, 0.85, 0.7, 0.55];
const MENU_VIEWPORT_MARGIN = 8;
const SETTINGS_COMMAND_EVENT = "akane-next-settings-command";
const SETTINGS_SNAPSHOT_EVENT = "akane-next-settings-snapshot";
const WORKSPACE_REFRESH_EVENT = "akane-next-workspace-refresh";
const SHOP_STATUS_EVENT = "akane-next-shop-status";
const CHARACTER_PACK_ACTIVATED_EVENT = "akane-next-character-pack-activated";
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
  characterPackId: getActiveCharacterPackId(),
  characters: {},
  sessionId: "",
  outfit: getProfileDefaultOutfit(),
  currentEmotion: getProfileDefaultEmotion(),
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
  hitboxOverlay: false,
  care: null,
  voiceSpeed: "1.00x",
  wakeWord: "Akane",
  wakeSensitivity: "中等",
  musicPlayMode: MUSIC_PLAY_MODES[0],
  musicVolumeNormalization: true
};

const bundledOutfit = buildBundledOutfit();
let runtimeCharacterPacks = [];
let runtimeCharacterPackOutfits = [];
let characterPackOutfits = buildCharacterPackOutfits();
let localOutfits = buildLocalOutfits();
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

const state = { ...DEFAULT_STATE, characters: {} };
const playState = {
  mode: "idle",
  vx: 0,
  vy: 0,
  heldEmotion: "",
  lastWallHitAt: 0,
  lastLandAt: 0,
  lastIdleJumpAt: 0
};

function getProfileUserId() {
  return state.profileUserId || PROFILE_USER_ID;
}

function buildBackendCharacterContext() {
  return {
    client_mode: CLIENT_MODE,
    user_id: state.sessionId || "desktop_pet_next",
    session_id: state.sessionId || "desktop_pet_next",
    real_user_id: getProfileUserId(),
    character_pack_id: getCurrentCharacterPackId(),
    emotion: state.currentEmotion || getProfileDefaultEmotion()
  };
}

function getCharacterRuntimeKey(packId = getCurrentCharacterPackId()) {
  const profile = String(getProfileUserId() || PROFILE_USER_ID).trim() || PROFILE_USER_ID;
  const character = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
  return `${profile}::${character}`;
}

function ensureCharacterRuntimeMap() {
  if (!state.characters || typeof state.characters !== "object" || Array.isArray(state.characters)) {
    state.characters = {};
  }
  return state.characters;
}

function persistCurrentCharacterRuntimeState(packId = state.characterPackId || getActiveCharacterPackId()) {
  const normalizedPackId = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
  const map = ensureCharacterRuntimeMap();
  map[getCharacterRuntimeKey(normalizedPackId)] = {
    version: 1,
    characterPackId: normalizedPackId,
    sessionId: String(state.sessionId || "").trim() || generateSessionId(),
    outfit: String(state.outfit || "").trim() || getProfileDefaultOutfit(),
    currentEmotion: String(state.currentEmotion || "").trim() || getProfileDefaultEmotion(),
    x: normalizeNullableInteger(state.x),
    y: normalizeNullableInteger(state.y),
    width: null,
    height: null,
    scale: clamp(Number(state.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX),
    opacity: clamp(Number(state.opacity ?? DEFAULT_STATE.opacity), 0.55, 1),
    care: normalizeCareState(state.care, getProfileCareConfig()),
    updatedAt: Date.now()
  };
  return map[getCharacterRuntimeKey(normalizedPackId)];
}

function findCharacterRuntimeState(packId) {
  const key = getCharacterRuntimeKey(packId);
  return normalizeCharacterRuntimeState(ensureCharacterRuntimeMap()[key]);
}

function createCharacterRuntimeState(packId, profile, { seedFromCurrent = false } = {}) {
  const appearance = profile?.appearance || {};
  const defaultOutfit = String(appearance.defaultOutfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
  const defaultEmotion = String(appearance.defaultEmotion || getProfileDefaultEmotion()).trim() || getProfileDefaultEmotion();
  return {
    version: 1,
    characterPackId: normalizeCharacterPackId(packId) || getActiveCharacterPackId(),
    sessionId: seedFromCurrent ? String(state.sessionId || "").trim() || generateSessionId() : generateSessionId(),
    outfit: seedFromCurrent ? String(state.outfit || defaultOutfit).trim() || defaultOutfit : defaultOutfit,
    currentEmotion: seedFromCurrent ? String(state.currentEmotion || defaultEmotion).trim() || defaultEmotion : defaultEmotion,
    x: normalizeNullableInteger(state.x),
    y: normalizeNullableInteger(state.y),
    width: null,
    height: null,
    scale: clamp(Number(state.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX),
    opacity: clamp(Number(state.opacity ?? DEFAULT_STATE.opacity), 0.55, 1),
    care: seedFromCurrent ? normalizeCareState(state.care, getProfileCareConfig()) : createCareState(profile?.care),
    updatedAt: Date.now()
  };
}

function applyCharacterRuntimeState(packId, profile, options = {}) {
  const normalizedPackId = normalizeCharacterPackId(packId) || getActiveCharacterPackId();
  const map = ensureCharacterRuntimeMap();
  const key = getCharacterRuntimeKey(normalizedPackId);
  const runtime = findCharacterRuntimeState(normalizedPackId) ||
    createCharacterRuntimeState(normalizedPackId, profile, options);
  const appearance = profile?.appearance || {};
  const defaultOutfit = String(appearance.defaultOutfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
  const defaultEmotion = String(appearance.defaultEmotion || getProfileDefaultEmotion()).trim() || getProfileDefaultEmotion();

  state.sessionId = String(runtime.sessionId || "").trim() || generateSessionId();
  state.outfit = normalizeOutfitName(runtime.outfit || defaultOutfit) || defaultOutfit;
  state.currentEmotion = String(runtime.currentEmotion || defaultEmotion).trim() || defaultEmotion;
  state.x = normalizeNullableInteger(runtime.x);
  state.y = normalizeNullableInteger(runtime.y);
  state.width = null;
  state.height = null;
  state.scale = clamp(Number(runtime.scale ?? state.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX);
  state.opacity = clamp(Number(runtime.opacity ?? state.opacity ?? DEFAULT_STATE.opacity), 0.55, 1);
  state.care = normalizeCareState(runtime.care, profile?.care);
  scheduleCareWorkCompletion();
  scheduleCarePassiveTick();

  map[key] = persistCurrentCharacterRuntimeState(normalizedPackId);
  return runtime;
}

const unlistenFns = [];
let saveTimer = 0;
let careWorkTimer = 0;
let carePassiveTimer = 0;
let careAwayClickThrough = false;
let webglProbe = null;
let sending = false;
let activeTurnToken = 0;
let activeTurnLatencyTrace = null;
let thinkController = null;
let runtimeMode = "idle";
let bubbleToken = 0;
let bubbleTimer = 0;
let bubbleKind = "none";
let replyDisplayActive = false;
let segmentTimer = 0;
let lastTurnSignature = "";
let lastTurnTextKey = "";
let lastStateRequestSignature = "";
let firstSpeechSegmentShown = false;
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
let physicsTimer = 0;
let physicsTickInFlight = false;
let monitorBoundsCache = null;
let monitorBoundsCacheAt = 0;
let lastWindowGeometry = null;
let idleJumpTimer = 0;
let lastUserPetInteractionAt = Date.now();
let clickTimer = 0;
let inputHistory = [];
let inputHistoryIndex = -1;
let inputHistoryDraft = "";
let applyingInputHistory = false;
let chatInputIdleTimer = 0;
let suppressClickUntil = 0;
let hitSyncFrame = 0;
let pendingHitSyncForce = false;
let lastHitRegionSignature = "";
let settingsSnapshotTimer = 0;
let settingsBridgeRegistered = false;
let characterActivationBridgeRegistered = false;
let menuAnchor = null;
let characterActivationTask = Promise.resolve();
let lastAppliedLayoutSignature = "";
let musicSnapshotTimer = 0;
let musicTimelineTimer = 0;
let musicTimelineSourceId = "";
let systemMediaPollTimer = 0;
let systemMedia = emptySystemMediaSnapshot();
let systemMediaLyrics = emptySystemMediaLyricsSnapshot();
let systemMediaLyricsLoading = false;
let systemMediaLyricsLastAttemptAt = 0;
const systemMediaLyricsCache = new Map();
const systemMediaLyricsRequests = new Map();
const recentTracksHistory = []; // max 5, newest first
const PANEL_MUSIC_CONTROLS = Object.freeze(["pause", "next", "prev", "recommend"]);
let panelMusicController = "model";
let panelSyncTimer = null;
let ttsToken = 0;
let ttsController = null;
let ttsObjectUrl = "";
let ttsActive = false;
let ttsQueue = [];
let lastTtsSignature = "";
let resolveTtsWait = null;
let streamingTtsTurnToken = 0;
let streamingTtsText = "";
const streamingTtsSegmentKeys = new Set();
let streamingReplyTurnToken = 0;
let streamingReplyText = "";
let streamingReplyFinalized = false;
let streamedReplyLastShownAt = 0;
let streamedReplyLastShownText = "";
let streamedReplyQueue = [];
const streamingReplySegmentKeys = new Set();
let ttsPrewarmTimer = 0;
let ttsPrewarmController = null;
let ttsPrewarmInFlightKey = "";
const ttsPrewarmReadyAtByKey = new Map();
let musicTrack = null;
let musicQueue = [];
let musicQueueIndex = -1;
let musicPlaying = false;
let musicPaused = false;
let musicLoading = false;
let musicEmotionActive = false;
let musicEmotionRestoreTimer = 0;
let musicDropHover = false;
let workspaceMusicRecommendations = [];
let workspaceAudioCatalog = [];
let workspaceMusicRecommendationsRefreshTimer = 0;
let workspaceMusicRecommendationsLoading = false;
let workspaceImporting = false;
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
let proactiveWakeNextAllowedAt = 0;
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
let workspaceTaskPollTimer = 0;
let workspaceTaskWatchPrimed = false;
let workspaceTaskStatusCache = new Map();
let workspaceTaskWatchKey = "";
const workspaceTaskAnnounced = new Set();
let desktopFileDeliveryHandled = new Set();
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
  openWorkshop: document.querySelector("#open-workshop"),
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
  clearMusicQueue: document.querySelector("#clear-music-queue"),
  resourceDetails: document.querySelector("#resource-details"),
  emotionGrid: document.querySelector("#emotion-grid"),
  connectionStatus: document.querySelector("#connection-status"),
  status: document.querySelector("#runtime-status"),
  voicePlayer: document.querySelector("#voice-player"),
  musicPlayer: document.querySelector("#music-player"),
  canvas: document.querySelector("#webgl-probe")
};

const visualRenderer = createVisualRenderer({
  stage: els.stage,
  image: els.petImage,
  onImageLoadError: ({ expression, error }) => {
    const label = String(expression?.name || expression?.id || "unknown").trim();
    console.error("[pet-image] failed to preload:", expression?.url || "", error);
    setRuntimeStatus(
      `立绘加载失败：${label} · ${friendlyErrorMessage(formatError(error))}`,
      { mode: "error" }
    );
  }
});

boot();

async function boot() {
  bindUi();
  applyCharacterChrome();
  applyVisualState();
  updateConnectionStatus();
  scheduleIdleJump();

  if (!isTauriRuntime) {
    state.sessionId = generateSessionId();
    setStatus("Browser preview");
    await reloadCharacterResources({ startup: true });
    scheduleNativeHitTestSync();
    scheduleWorkspaceMusicRecommendationsRefresh();
    return;
  }

  try {
    scheduleTauriRuntimeBridges();
    await loadAndApplyPersistedCharacterState();
    settleCarePassiveState({ persist: false });
    scheduleSave(0);
    await reloadCharacterResources({ startup: true });
    scheduleNativeWindowStateApply({ forceHitTest: true });
    void ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
    scheduleDesktopContextPoll();
    scheduleSystemMediaPoll({ immediate: true });
    scheduleScreenVisionCapture({ immediate: true });
    scheduleProactiveWake();
    scheduleWorkspaceTaskWatch({ delayMs: 2000 });
    scheduleWorkspaceMusicRecommendationsRefresh();
  } catch (error) {
    setStatus(`Tauri init failed: ${formatError(error)}`);
  }
}

function scheduleTauriRuntimeBridges() {
  if (!isTauriRuntime) return;
  window.setTimeout(startTauriRuntimeBridges, 0);
}

function startTauriRuntimeBridges() {
  if (!isTauriRuntime) return;
  void registerCharacterActivationBridge().catch((error) => {
    setStatus(`角色切换桥接不可用：${formatError(error)}`);
  });
  void registerSettingsBridge().catch((error) => {
    setStatus(`设置桥接不可用：${formatError(error)}`);
  });
  void registerPanelBridge().catch(() => {});
  void Promise.allSettled([
    registerWindowListeners(),
    registerFileDropHandlers()
  ]);
}

async function loadAndApplyPersistedCharacterState({ expectedPackId = "" } = {}) {
  const loaded = await invoke("load_pet_state");
  const persistedPackId = String(loaded?.characterPackId || "").trim();
  await refreshRuntimeCharacterPacks({ silent: true, scheduleSnapshot: false });
  Object.assign(state, normalizeState(loaded));

  const pack = selectCharacterPack(state.characterPackId, { persist: false });
  if (expectedPackId && pack.packId !== expectedPackId) {
    throw new Error(`角色状态不一致：期望 ${expectedPackId}，实际 ${pack.packId}。`);
  }
  if (persistedPackId && pack.packId !== persistedPackId) {
    throw new Error(`角色包 ${persistedPackId} 未加载，已保留当前桌宠。`);
  }

  state.characterPackId = pack.packId;
  resourceState.manifest = null;
  resourceState.source = "character_pack";
  refreshLocalResourceAssets();
  applyCharacterRuntimeState(pack.packId, pack.profile);
  applyCharacterChrome();
  applyVisualState();
  if (canRenderCurrentLocalResources()) {
    setPetEmotion(state.currentEmotion, { persist: false, force: true });
  } else {
    state.currentEmotion = getProfileDefaultEmotion();
  }
  return pack;
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
  els.hitbox.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    cancelLocalClick();
    void openPanelWindow();
  });
  els.petImage.addEventListener("load", () => {
    if (import.meta.env.DEV) {
      console.log("[pet-image] loaded:", els.petImage.src);
    }
    scheduleNativeHitTestSync();
  });
  els.petImage.addEventListener("error", () => {
    const src = els.petImage.src || "";
    console.error("[pet-image] failed to load:", src.substring(0, 256));
    setStatus(`立绘加载失败：${src ? src.split("/").pop() : "无图片地址"}`, { durationMs: 3600 });
  });

  els.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitChatInput();
  });
  els.chatForm.addEventListener("pointermove", () => {
    scheduleChatInputAutoHide();
  });
  els.chatForm.addEventListener("pointerdown", () => {
    scheduleChatInputAutoHide();
  });

  els.chatInput.addEventListener("keydown", (event) => {
    scheduleChatInputAutoHide();
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
    scheduleChatInputAutoHide();
  });
  els.chatInput.addEventListener("focus", () => {
    scheduleChatInputAutoHide();
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
    scheduleChatInputAutoHide();
  });
  els.voiceRecordButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    void toggleVoiceRecording();
  });
  els.toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    void openPanelWindow();
  });
  els.close.addEventListener("click", () => {
    void closePetWindow();
  });
  els.quickInput.addEventListener("click", () => {
    closeMenu();
    showChatInput();
  });
  els.openSettings.addEventListener("click", () => {
    void openPanelWindow();
  });
  els.openWorkshop.addEventListener("click", () => {
    void openWorkshopWindow();
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
    void handleMusicPlaybackError();
  });
  els.musicPlayer.addEventListener("timeupdate", () => {
    if (musicTrack) scheduleMusicSnapshot();
  });

  window.addEventListener("contextmenu", (event) => {
    if (event.target.closest("#chat-form")) return;
    event.preventDefault();
    void openPanelWindow();
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
  window.addEventListener("resize", () => {
    repositionOpenMenu();
    scheduleNativeHitTestSync({ force: true });
  });

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

  els.clearMusicQueue.addEventListener("click", () => {
    clearMusicQueue({ announce: true });
  });

  els.closeMenuButton.addEventListener("click", () => {
    void closePetWindow();
  });
}

function beginManualDrag(event) {
  if (!isTauriRuntime) return;
  markPetInteraction();
  stopPetPhysics({ restore: false, reschedule: false });
  cancelLocalClick();
  setPetMotion("dragging");
  dragState = {
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    lastScreenX: event.screenX,
    lastScreenY: event.screenY,
    lastMoveAt: Date.now(),
    vx: 0,
    vy: 0,
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
  if (!dragState.moved && Math.hypot(totalDx, totalDy) < PET_DRAG_THRESHOLD_PX) return;

  dragState.moved = true;
  cancelLocalClick();
  hideBubble();
  setPetMotion("dragging");
  const dx = Math.round(event.screenX - dragState.lastScreenX);
  const dy = Math.round(event.screenY - dragState.lastScreenY);
  const now = Date.now();
  if (now - dragState.lastMoveAt > 5) {
    dragState.vx = dx * PET_DRAG_VELOCITY_FACTOR;
    dragState.vy = dy * PET_DRAG_VELOCITY_FACTOR;
    dragState.lastMoveAt = now;
  }
  if (!dx && !dy) return;

  dragState.lastScreenX = event.screenX;
  dragState.lastScreenY = event.screenY;
  event.preventDefault();

  const geometry = await tauriCall("move_window_by", { dx, dy }, { quiet: true });
  if (geometry) {
    applyWindowGeometryToState(geometry);
    scheduleSave(250);
  }
}

function handlePetPointerUp(event) {
  const result = endManualDrag(event);
  const moved = Boolean(result?.moved);
  if (moved) {
    suppressClickUntil = Date.now() + 350;
    startPetThrow(result);
  }
}

function endManualDrag(event) {
  if (!dragState) return { moved: false, vx: 0, vy: 0 };
  const ended = dragState;
  const pointerId = ended.pointerId;
  const moved = Boolean(ended.moved);
  dragState = null;
  try {
    if (event?.currentTarget?.hasPointerCapture?.(pointerId)) {
      event.currentTarget.releasePointerCapture(pointerId);
    }
  } catch {
    // Nothing to release.
  }
  if (!moved) {
    setPetMotion("idle");
    releasePetPlayEmotion({ delayMs: 0 });
  }
  return {
    moved,
    vx: Number(ended.vx) || 0,
    vy: Number(ended.vy) || 0
  };
}

function markPetInteraction() {
  lastUserPetInteractionAt = Date.now();
  scheduleIdleJump();
}

function startPetThrow({ vx = 0, vy = 0 } = {}) {
  markPetInteraction();
  playState.vx = Number(vx) || 0;
  playState.vy = Number(vy) || 0;

  if (Math.hypot(playState.vx, playState.vy) <= 1) {
    setPetMotion("idle");
    releasePetPlayEmotion({ delayMs: 0 });
    return;
  }

  playState.mode = "physics";
  const isFastThrow = Math.abs(playState.vx) > PET_THROW_THRESHOLD || Math.abs(playState.vy) > PET_THROW_THRESHOLD;
  const feedback = getProfilePlayFeedback(isFastThrow ? "throwFast" : "throwLight");
  setPetMotion(isFastThrow ? "thrown" : "drag-release", { durationMs: isFastThrow ? 0 : 420 });
  holdPetPlayEmotion(feedback.emotion);
  showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });

  startPetPhysicsLoop();
}

function startPetPhysicsLoop() {
  if (physicsTimer || !isTauriRuntime) return;
  physicsTimer = window.setInterval(() => {
    void tickPetPhysics();
  }, PET_PHYSICS_FRAME_MS);
}

function stopPetPhysics({ restore = true, reschedule = true } = {}) {
  window.clearInterval(physicsTimer);
  physicsTimer = 0;
  playState.mode = "idle";
  playState.vx = 0;
  playState.vy = 0;
  if (restore && !sending && !ttsActive) {
    setPetMotion("idle");
    releasePetPlayEmotion({ delayMs: 0 });
  }
  if (reschedule) {
    scheduleSave(250);
    scheduleIdleJump();
  }
}

async function tickPetPhysics() {
  if (!physicsTimer || dragState || !isTauriRuntime) return;
  if (physicsTickInFlight) return;
  physicsTickInFlight = true;
  try {
    const geometry = await getCachedWindowGeometry();
    if (!geometry) {
      stopPetPhysics({ restore: true });
      return;
    }

    const bounds = await getCurrentWorkAreaBounds(geometry);
    const floorY = Math.max(bounds.top, bounds.bottom - geometry.height - PET_PHYSICS_FLOOR_CLEARANCE);
    let x = geometry.x;
    let y = geometry.y;
    let vx = playState.vx;
    let vy = playState.vy;

    if (y < floorY) {
      vy += PET_PHYSICS_GRAVITY;
    }

    if (y + vy >= floorY) {
      y = floorY;
      if (Math.abs(vy) > 2) {
        vy = -vy * PET_PHYSICS_BOUNCE;
        setPetMotion("land", { durationMs: 420 });
        handlePetLand();
      } else {
        vy = 0;
      }
      vx *= PET_PHYSICS_GROUND_FRICTION;
    } else {
      vx *= PET_PHYSICS_AIR_FRICTION;
      vy *= PET_PHYSICS_AIR_FRICTION;
    }

    let nextX = x + vx;
    let nextY = y + vy;
    let hitWall = false;
    let wallImpactSpeed = 0;
    const maxX = Math.max(bounds.left, bounds.right - geometry.width);

    if (nextX <= bounds.left) {
      nextX = bounds.left;
      wallImpactSpeed = Math.abs(vx);
      vx = -vx * PET_PHYSICS_BOUNCE;
      hitWall = true;
    } else if (nextX >= maxX) {
      nextX = maxX;
      wallImpactSpeed = Math.abs(vx);
      vx = -vx * PET_PHYSICS_BOUNCE;
      hitWall = true;
    }

    const dx = Math.round(nextX - x);
    const dy = Math.round(nextY - geometry.y);
    if (dx || dy) {
      const moved = await tauriCall("move_window_by", { dx, dy }, { quiet: true });
      if (moved) {
        applyWindowGeometryToState(moved);
      } else {
        stopPetPhysics({ restore: true });
        return;
      }
    } else {
      lastWindowGeometry = { ...geometry, x: Math.round(nextX), y: Math.round(nextY) };
    }

    playState.vx = vx;
    playState.vy = vy;

    if (hitWall) {
      handlePetWallHit(wallImpactSpeed);
    }

    const settledOnFloor = Math.abs(vx) < PET_PHYSICS_MIN_SPEED && Math.abs(vy) < PET_PHYSICS_MIN_SPEED &&
      Math.abs((lastWindowGeometry?.y ?? nextY) - floorY) <= 1;
    if (settledOnFloor) {
      stopPetPhysics({ restore: true });
    }
  } finally {
    physicsTickInFlight = false;
  }
}

async function getCachedWindowGeometry() {
  if (!isTauriRuntime) return null;
  if (lastWindowGeometry?.width && lastWindowGeometry?.height) return lastWindowGeometry;
  const geometry = await tauriCall("get_window_geometry", {}, { quiet: true });
  if (geometry) applyWindowGeometryToState(geometry);
  return lastWindowGeometry;
}

async function getCurrentWorkAreaBounds(geometry = lastWindowGeometry) {
  const now = Date.now();
  if (monitorBoundsCache && now - monitorBoundsCacheAt < 3000) return monitorBoundsCache;

  const monitor = await currentMonitor().catch(() => null) || await primaryMonitor().catch(() => null);
  const workArea = monitor?.workArea;
  const position = workArea?.position || monitor?.position || {};
  const size = workArea?.size || monitor?.size || {};
  const left = Number(position.x);
  const top = Number(position.y);
  const width = Number(size.width);
  const height = Number(size.height);

  if (Number.isFinite(left) && Number.isFinite(top) && Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
    monitorBoundsCache = {
      left,
      top,
      right: left + width,
      bottom: top + height
    };
  } else {
    const x = Number(geometry?.x) || 0;
    const y = Number(geometry?.y) || 0;
    const fallbackWidth = Number(window.screen?.availWidth || window.innerWidth || 1280);
    const fallbackHeight = Number(window.screen?.availHeight || window.innerHeight || 720);
    monitorBoundsCache = {
      left: Math.min(0, x),
      top: Math.min(0, y),
      right: Math.max(fallbackWidth, x + (geometry?.width || 0)),
      bottom: Math.max(fallbackHeight, y + (geometry?.height || 0))
    };
  }
  monitorBoundsCacheAt = now;
  return monitorBoundsCache;
}

function handlePetWallHit(impactSpeed) {
  const now = Date.now();
  if (now - playState.lastWallHitAt < 300) return;
  playState.lastWallHitAt = now;
  markPetInteraction();
  setPetMotion("hit-wall", { durationMs: 520 });

  if (Math.abs(impactSpeed) > PET_WALL_PAIN_THRESHOLD) {
    const feedback = getProfilePlayFeedback("wallHit");
    holdPetPlayEmotion(feedback.emotion);
    showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });
  }
}

function handlePetLand() {
  const now = Date.now();
  if (now - playState.lastLandAt < 400) return;
  playState.lastLandAt = now;
  const feedback = getProfilePlayFeedback("land");
  holdPetPlayEmotion(feedback.emotion);
  showPetPlayBubble(feedback.bubble.text, { durationMs: feedback.bubble.durationMs });
}

function showPetPlayBubble(text, { durationMs = 1800 } = {}) {
  if (!String(text || "").trim() || durationMs <= 0) return;
  showBubbleText(text, { transient: true, durationMs, local: true, kind: "play" });
}

function holdPetPlayEmotion(emotion) {
  const value = String(emotion || "").trim();
  if (!value) return;
  playState.heldEmotion = value;
  clearTransientEmotionRestore();
  setPetEmotion(value, { persist: false });
}

function getProfilePlayFeedback(kind) {
  const feedback = getActiveCharacterProfile()?.playFeedback || {};
  const entry = feedback[kind] && typeof feedback[kind] === "object" ? feedback[kind] : {};
  const bubble = entry.bubble && typeof entry.bubble === "object" ? entry.bubble : {};
  return {
    emotion: String(entry.emotion || "").trim(),
    bubble: {
      text: String(bubble.text || "").trim(),
      durationMs: Math.max(0, Number(bubble.durationMs) || 0)
    }
  };
}

function releasePetPlayEmotion({ delayMs = PET_MOTION_RESTORE_MS } = {}) {
  const held = playState.heldEmotion;
  if (!held) return;
  window.setTimeout(() => {
    if (playState.heldEmotion !== held) return;
    if (sending || ttsActive || voiceInputState === "recording" || dragState || physicsTimer) return;
    playState.heldEmotion = "";
    setRestingPetEmotion();
    scheduleMusicEmotionRestore();
  }, Math.max(0, Number(delayMs) || 0));
}

function restorePetEmotionAfterPlay(durationMs = PET_MOTION_RESTORE_MS) {
  if (playState.heldEmotion) return;
  if (sending || ttsActive || voiceInputState === "recording") return;
  window.setTimeout(() => {
    if (sending || ttsActive || voiceInputState === "recording" || dragState || physicsTimer) return;
    setRestingPetEmotion();
    scheduleMusicEmotionRestore();
  }, durationMs);
}

function scheduleIdleJump() {
  window.clearTimeout(idleJumpTimer);
  if (!isTauriRuntime) return;
  idleJumpTimer = window.setTimeout(() => {
    if (Date.now() - lastUserPetInteractionAt < PET_IDLE_JUMP_AFTER_MS) {
      scheduleIdleJump();
      return;
    }
    maybePlayIdleJump();
    scheduleIdleJump();
  }, PET_IDLE_JUMP_AFTER_MS);
}

function maybePlayIdleJump() {
  const now = Date.now();
  if (now - playState.lastIdleJumpAt < PET_IDLE_JUMP_COOLDOWN_MS) return;
  if (sending || ttsActive || ttsQueue.length > 0 || replyDisplayActive || !els.chatForm.hidden || !els.menu.hidden) return;
  if (dragState || physicsTimer) return;

  playState.lastIdleJumpAt = now;
  setPetMotion("jump", { durationMs: 920 });
}

async function registerWindowListeners() {
  if (!appWindow) return;

  unlistenFns.push(await appWindow.onMoved(({ payload }) => {
    state.x = Math.round(payload.x);
    state.y = Math.round(payload.y);
    scheduleSave(250);
  }));

  unlistenFns.push(await appWindow.onResized(({ payload }) => {
    void payload;
    state.width = null;
    state.height = null;
    scheduleNativeHitTestSync({ force: true });
    scheduleSave(250);
  }));

  window.addEventListener("beforeunload", () => {
    unlistenFns.forEach((unlisten) => unlisten());
    window.clearTimeout(desktopContextPollTimer);
    window.clearTimeout(systemMediaPollTimer);
    window.clearTimeout(proactiveWakeTimer);
    window.clearTimeout(idleJumpTimer);
    window.clearTimeout(musicEmotionRestoreTimer);
    stopPetPhysics({ restore: false, reschedule: false });
    stopScreenVisionCapture({ clearRemote: false });
    window.clearTimeout(backendRetryTimer);
    window.clearTimeout(workspaceTaskPollTimer);
    window.clearTimeout(transientEmotionTimer);
    if (thinkController) thinkController.abort();
    if (asrController) asrController.abort();
    stopTts();
    stopMusic({ silent: true, clearQueue: true });
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
      showFileDropHint();
      return;
    }
    musicDropHover = false;
  }));
}

async function registerSettingsBridge() {
  if (!isTauriRuntime || settingsBridgeRegistered) return;

  const unlisten = await listen(SETTINGS_COMMAND_EVENT, (event) => {
    void handleSettingsCommand(event.payload).catch((error) => {
      void reportSettingsCommandFailure(event.payload, error);
    });
  });
  unlistenFns.push(unlisten);
  settingsBridgeRegistered = true;
}

async function registerCharacterActivationBridge() {
  if (!isTauriRuntime || characterActivationBridgeRegistered) return;

  const unlisten = await listen(CHARACTER_PACK_ACTIVATED_EVENT, (event) => {
    const packId = String(event?.payload?.packId || "").trim();
    if (!packId) return;
    characterActivationTask = characterActivationTask
      .catch(() => {})
      .then(() => applyPersistedCharacterActivation(packId))
      .catch((error) => {
        setStatus(`角色切换失败：${formatError(error)}`);
      });
  });
  unlistenFns.push(unlisten);
  characterActivationBridgeRegistered = true;
}

async function registerPanelBridge() {
  if (!isTauriRuntime) return;

  unlistenFns.push(await listen("panel:ready", () => {
    schedulePanelStateSync(50);
    if (recentTracksHistory.length) {
      void emitPanelEvent("panel:recent-update", recentTracksHistory.slice());
    }
    void refreshPanelCoListenSummary();
    void refreshPanelMusicController();
  }));

  unlistenFns.push(await listen("panel:action", (event) => {
    const { action, muted, value, controller } = event.payload || {};
    if (action === "new-session") {
      void startNewSession();
    } else if (action === "open-workspace") {
      void openWorkspaceWindow();
    } else if (action === "open-workshop") {
      void openWorkshopWindow();
    } else if (action === "open-shop") {
      void openShopWindow();
    } else if (action === "toggle-mute" && typeof muted === "boolean") {
      setVoiceEnabled(!muted);
    } else if (action === "stop-reply") {
      interruptReply({ announce: true });
    } else if (action === "quit") {
      void closePetWindow();
    } else if (action === "set-scale" && typeof value === "number") {
      state.scale = Math.min(SCALE_MAX, Math.max(SCALE_MIN, value));
      applyVisualState();
      schedulePanelStateSync(80);
      scheduleSave(300);
    } else if (action === "set-opacity" && typeof value === "number") {
      state.opacity = Math.min(1, Math.max(0.55, value));
      applyVisualState();
      schedulePanelStateSync(80);
      scheduleSave(300);
    } else if (action === "refresh-co-listen") {
      void refreshPanelCoListenSummary();
    } else if (action === "refresh-music-controller") {
      void refreshPanelMusicController();
    } else if (action === "set-music-controller") {
      void setPanelMusicController(controller === "user" ? "user" : "model");
    }
  }));
}

async function applyPersistedCharacterActivation(packId) {
  const pack = await loadAndApplyPersistedCharacterState({ expectedPackId: packId });
  scheduleSave(0);
  setStatus(`角色包已切换为 ${pack.profile.identity.name}。`, { durationMs: 2400 });
  await reloadCharacterResources({ userTriggered: true });
  setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
  scheduleNativeWindowStateApply({ forceHitTest: true });
  await broadcastSettingsSnapshot();
  void ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
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
    case "openShop":
      await openShopWindow();
      break;
    case "openWorkshop":
      await openWorkshopWindow();
      break;
    case "setBackendUrl":
      await updateBackendUrl(payload.value);
      break;
    case "setOutfit":
      await updateOutfit(payload.value);
      break;
    case "setCharacterPack":
      await updateCharacterPack(payload.value);
      break;
    case "refreshCharacterPacks":
      await refreshCharacterPacksFromSettings(payload.value);
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
    case "previewTts":
      await previewTts(payload.value);
      break;
    case "previousMusic":
      await playPreviousMusicTrack();
      break;
    case "nextMusic":
      await playNextMusicTrack();
      break;
    case "playMusicTrack":
      await playMusicTrackBySourceId(payload.value);
      break;
    case "playWorkspaceAudio": {
      const raw = payload.value && typeof payload.value === "object" ? payload.value : payload;
      await playWorkspaceAudioItem({ itemType: raw.itemType, handle: raw.handle, title: raw.title });
      break;
    }
    case "removeMusicTrack":
      await removeMusicTrackBySourceId(payload.value);
      break;
    case "buyShopItem":
      buyShopItem(payload.value);
      break;
    case "feedInventoryItem":
      feedInventoryItem(payload.value);
      break;
    case "startCareWork":
      startCareWork();
      break;
    case "claimCareAllowance":
      claimCareAllowance();
      break;
    case "toggleMusic":
      await toggleMusicPlayback();
      break;
    case "seekMusic":
      seekMusicPlayback(Number(payload.value));
      break;
    case "stopMusic":
      stopMusic({ announce: true });
      break;
    case "clearMusicQueue":
      clearMusicQueue({ announce: true });
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
    case "setVoiceSpeed":
      setVoiceSpeed(payload.value);
      break;
    case "setWakeWord":
      setWakeWord(payload.value);
      break;
    case "setWakeSensitivity":
      setWakeSensitivity(payload.value);
      break;
    case "setMusicPlayMode":
      setMusicPlayMode(payload.value);
      break;
    case "setMusicVolumeNormalization":
      setMusicVolumeNormalization(payload.value);
      break;
    case "smtcAction":
      if (payload.action) {
        void controlSystemMediaPlayback(payload.action);
      }
      break;
    default:
      setRuntimeStatus(`未知设置命令：${command}`);
      break;
  }

  scheduleSettingsSnapshot();
}

function reportSettingsCommandFailure(_payload, error) {
  const message = formatError(error);
  setStatus(`设置命令失败：${message}`);
}

function applyCharacterChrome() {
  const appName = getProfileIdentityText("appName", APP_DISPLAY_NAME);
  const name = getProfileIdentityText("name", CHARACTER_NAME);
  document.title = appName;
  if (els.menuTitle) els.menuTitle.textContent = appName;
  if (els.chatInput) els.chatInput.placeholder = getActiveCharacterText("inputPlaceholder");
  if (els.hitbox) els.hitbox.setAttribute("aria-label", name);
  visualRenderer.setCharacterLabel(name);
  if (els.close) els.close.title = `关闭 ${appName}`;
  schedulePanelStateSync(80);
}

function scheduleSettingsSnapshot(delay = 40) {
  if (!isTauriRuntime) return;
  window.clearTimeout(settingsSnapshotTimer);
  settingsSnapshotTimer = window.setTimeout(() => {
    void broadcastSettingsSnapshot();
  }, delay);
}

function scheduleMusicSnapshot(delay = 420) {
  if (!isTauriRuntime || musicSnapshotTimer) return;
  musicSnapshotTimer = window.setTimeout(() => {
    musicSnapshotTimer = 0;
    void broadcastSettingsSnapshot();
  }, delay);
}

async function broadcastSettingsSnapshot() {
  if (!isTauriRuntime) return;
  const payload = buildSettingsSnapshot();
  await Promise.allSettled([
    emitTo("settings", SETTINGS_SNAPSHOT_EVENT, payload),
    emitTo("workshop", SETTINGS_SNAPSHOT_EVENT, payload),
    emitTo("workspace", SETTINGS_SNAPSHOT_EVENT, payload),
    emitTo("shop", SETTINGS_SNAPSHOT_EVENT, payload)
  ]);
  try {
    await emit(SETTINGS_SNAPSHOT_EVENT, payload);
  } catch {
    // The settings window may not be open yet.
  }
}

function buildSettingsSnapshot() {
  settleCarePassiveState({ persist: false });
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
      characterPackId: state.characterPackId,
      characterRuntimeKey: getCharacterRuntimeKey(state.characterPackId),
      characters: { ...ensureCharacterRuntimeMap() },
      care: normalizeCareState(state.care, getProfileCareConfig()),
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
      hitboxOverlay: state.hitboxOverlay,
      voiceSpeed: state.voiceSpeed,
      wakeWord: state.wakeWord,
      wakeSensitivity: state.wakeSensitivity,
      musicPlayMode: state.musicPlayMode,
      musicVolumeNormalization: state.musicVolumeNormalization
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
      activeOutfit: activeOutfit.id || getProfileDefaultOutfit(),
      activeOutfitName: activeOutfit.name || activeOutfit.id || getProfileDefaultOutfit(),
      requestedOutfit: state.outfit || getProfileDefaultOutfit(),
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
    visual: visualRenderer.getStatus(),
    music: buildMusicSnapshot(),
    currentExpression: buildCurrentExpressionSnapshot(),
    webglEnabled: els.stage.classList.contains("show-webgl")
  };
}

function buildCurrentExpressionSnapshot() {
  const entry = resolveEmotionEntry(state.currentEmotion);
  if (!entry) {
    return {
      id: state.currentEmotion || "",
      name: state.currentEmotion || "",
      image: state.currentEmotion || "",
      outfitId: state.outfit || "",
      characterPackId: getCurrentCharacterPackId(),
      updatedAt: Date.now()
    };
  }
  return {
    id: entry.id || state.currentEmotion || "",
    name: entry.name || entry.id || state.currentEmotion || "",
    image: entry.image || entry.url || entry.key || entry.id || "",
    outfitId: state.outfit || "",
    characterPackId: getCurrentCharacterPackId(),
    updatedAt: Date.now()
  };
}

function normalizeState(value) {
  const incoming = value ?? {};
  const scale = clamp(Number(incoming.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX);
  const _legacySize = isLegacyWindowSize(incoming.width, incoming.height, scale);
  return {
    ...DEFAULT_STATE,
    ...incoming,
    width: null,
    height: null,
    scale,
    opacity: clamp(Number(incoming.opacity ?? DEFAULT_STATE.opacity), 0.55, 1),
    skipTaskbar: Boolean(incoming.skipTaskbar ?? DEFAULT_STATE.skipTaskbar),
    alwaysOnTop: Boolean(incoming.alwaysOnTop ?? DEFAULT_STATE.alwaysOnTop),
    clickThrough: false,
    backendUrl: normalizeBackendUrl(incoming.backendUrl),
    profileUserId: PROFILE_USER_ID,
    characterPackId: normalizeCharacterPackId(incoming.characterPackId),
    characters: normalizeCharacterRuntimeStates(incoming.characters),
    sessionId: String(incoming.sessionId || "").trim() || generateSessionId(),
    outfit: String(incoming.outfit || "").trim(),
    currentEmotion: String(incoming.currentEmotion || "").trim(),
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
    hitboxOverlay: Boolean(incoming.hitboxOverlay ?? DEFAULT_STATE.hitboxOverlay),
    care: normalizeCareState(incoming.care, getProfileCareConfig()),
    voiceSpeed: String(incoming.voiceSpeed ?? DEFAULT_STATE.voiceSpeed).trim() || DEFAULT_STATE.voiceSpeed,
    wakeWord: String(incoming.wakeWord ?? DEFAULT_STATE.wakeWord).trim() || DEFAULT_STATE.wakeWord,
    wakeSensitivity: String(incoming.wakeSensitivity ?? DEFAULT_STATE.wakeSensitivity).trim() || DEFAULT_STATE.wakeSensitivity,
    musicPlayMode: normalizeMusicPlayMode(incoming.musicPlayMode),
    musicVolumeNormalization: normalizeBooleanSetting(
      incoming.musicVolumeNormalization,
      DEFAULT_STATE.musicVolumeNormalization
    )
  };
}

function normalizeCharacterRuntimeStates(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const result = {};
  for (const [key, runtime] of Object.entries(source)) {
    const normalized = normalizeCharacterRuntimeState(runtime);
    if (normalized) {
      result[String(key)] = normalized;
    }
  }
  return result;
}

function normalizeCharacterRuntimeState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const characterPackId = normalizeCharacterPackId(value.characterPackId || value.packId || value.character_pack_id);
  return {
    version: Math.max(1, Math.round(Number(value.version || 1))),
    characterPackId,
    sessionId: String(value.sessionId || value.session_id || "").trim(),
    outfit: String(value.outfit || value.outfit_id || "").trim(),
    currentEmotion: String(value.currentEmotion || value.current_emotion || "").trim(),
    x: normalizeNullableInteger(value.x),
    y: normalizeNullableInteger(value.y),
    width: null,
    height: null,
    scale: clamp(Number(value.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX),
    opacity: clamp(Number(value.opacity ?? DEFAULT_STATE.opacity), 0.55, 1),
    care: normalizeCareState(value.care, getProfileCareConfig()),
    updatedAt: Math.max(0, Math.round(Number(value.updatedAt || value.updated_at || 0)))
  };
}

function normalizeNullableInteger(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number) : null;
}

function normalizePositiveInteger(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return null;
  return Math.round(number);
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

function normalizeMusicPlayMode(value) {
  const mode = String(value || "").trim();
  return MUSIC_PLAY_MODES.includes(mode) ? mode : DEFAULT_STATE.musicPlayMode;
}

function normalizeBooleanSetting(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  const text = String(value ?? "").trim().toLowerCase();
  if (text === "true" || text === "1") return true;
  if (text === "false" || text === "0") return false;
  return Boolean(fallback);
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
  els.outfit.value = state.outfit || getProfileDefaultOutfit();
  els.stage.classList.toggle("show-hitbox-overlay", state.hitboxOverlay);
  applyCharacterLayout();
  updateVoiceRecordButton();
  updateMenuLabels();
  scheduleNativeHitTestSync();
  autoResizeChatInput();
}

function applyCharacterLayout() {
  const profile = getActiveCharacterProfile();
  const layouts = profile?.layout?.outfits && typeof profile.layout.outfits === "object"
    ? profile.layout.outfits
    : {};
  const outfit = String(state.outfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
  const outfitLayout = layouts[outfit] || layouts[getProfileDefaultOutfit()];
  if (!outfitLayout) {
    visualRenderer.setLayout(null);
    lastAppliedLayoutSignature = "";
    return;
  }

  visualRenderer.setLayout(outfitLayout);

  /* apply calibrated window size — only when dimensions change */
  const winW = Number(outfitLayout.window?.width) || 0;
  const winH = Number(outfitLayout.window?.height) || 0;
  const sig = `${getCurrentCharacterPackId()}::${outfit}::${winW}x${winH}`;
  if (winW >= 200 && winH >= 200 && sig !== lastAppliedLayoutSignature) {
    lastAppliedLayoutSignature = sig;
    if (isTauriRuntime) {
      void invoke("resize_pet_window", { width: winW, height: winH }).catch((error) => {
        setStatus(`窗口校准尺寸应用失败：${formatError(error)}`, { durationMs: 2400 });
      });
    }
  }
}

/* Awaited resize to the active character's layout window dimensions.
   Must be called AFTER apply_window_state to guarantee layout size wins the race. */
const LAYOUT_RESIZE_MAX_WIDTH = 1200;
const LAYOUT_RESIZE_MAX_HEIGHT = 1600;

function scheduleNativeWindowStateApply({ forceHitTest = false } = {}) {
  if (!isTauriRuntime) return;
  window.setTimeout(() => {
    void applyNativeWindowState({ forceHitTest }).catch((error) => {
      setStatus(`窗口状态应用失败：${formatError(error)}`, { durationMs: 2400 });
    });
  }, 0);
}

async function applyNativeWindowState({ forceHitTest = false } = {}) {
  const geometry = await invoke("apply_window_state", { state });
  if (geometry) applyWindowGeometryToState(geometry);
  await applyCharacterLayoutResize();
  scheduleNativeHitTestSync({ force: forceHitTest });
}

async function applyCharacterLayoutResize() {
  if (!isTauriRuntime) return;
  const profile = getActiveCharacterProfile();
  const layouts = profile?.layout?.outfits && typeof profile.layout.outfits === "object"
    ? profile.layout.outfits : {};
  const outfit = String(state.outfit || getProfileDefaultOutfit()).trim() || getProfileDefaultOutfit();
  const outfitLayout = layouts[outfit] || layouts[getProfileDefaultOutfit()];
  if (!outfitLayout) return;
  let winW = Number(outfitLayout.window?.width) || 0;
  let winH = Number(outfitLayout.window?.height) || 0;
  if (winW >= 200 && winH >= 200) {
    winW = Math.min(winW, LAYOUT_RESIZE_MAX_WIDTH);
    winH = Math.min(winH, LAYOUT_RESIZE_MAX_HEIGHT);
    await invoke("resize_pet_window", { width: winW, height: winH }).catch((error) => {
      setStatus(`窗口校准尺寸应用失败：${formatError(error)}`, { durationMs: 2400 });
    });
    lastAppliedLayoutSignature = `${getCurrentCharacterPackId()}::${outfit}::${winW}x${winH}`;
  }
}

function updateMenuLabels() {
  els.taskbar.textContent = state.skipTaskbar ? "显示任务栏" : "隐藏任务栏";
  els.alwaysOnTop.textContent = state.alwaysOnTop ? "取消置顶" : "保持置顶";
  els.webgl.textContent = els.stage.classList.contains("show-webgl") ? "隐藏 WebGL" : "WebGL";
  els.hitTestToggle.textContent = state.hitTestEnabled ? "Hit-Test: on" : "Hit-Test: off";
  els.hitboxOverlayToggle.textContent = state.hitboxOverlay ? "Hitbox: on" : "Hitbox: off";
  if (els.menuSummary) {
    const outfit = getActiveOutfit();
    const source = resourceSourceLabel(resourceState.source);
    els.menuSummary.textContent = `${outfit.id || getProfileDefaultOutfit()} · ${source} · ${state.currentEmotion || getProfileDefaultEmotion()}`;
  }
  renderPresetChips();
  renderResourceDetails();
  renderEmotionGrid();
  repositionOpenMenu();
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
    cancelTtsPrewarm();
    stopTts();
  } else {
    scheduleTtsPrewarm({ force: true, delayMs: 200 });
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

function setVoiceSpeed(value) {
  state.voiceSpeed = String(value ?? DEFAULT_STATE.voiceSpeed).trim() || DEFAULT_STATE.voiceSpeed;
  scheduleSave(0);
  setRuntimeStatus(`语速已设为 ${state.voiceSpeed}`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setWakeWord(value) {
  state.wakeWord = String(value ?? DEFAULT_STATE.wakeWord).trim() || DEFAULT_STATE.wakeWord;
  scheduleSave(0);
  setRuntimeStatus(`唤醒词已设为 ${state.wakeWord}`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setWakeSensitivity(value) {
  state.wakeSensitivity = String(value ?? DEFAULT_STATE.wakeSensitivity).trim() || DEFAULT_STATE.wakeSensitivity;
  scheduleSave(0);
  setRuntimeStatus(`唤醒灵敏度已设为 ${state.wakeSensitivity}`, { mode: "idle" });
  scheduleSettingsSnapshot();
}

function setMusicPlayMode(value) {
  state.musicPlayMode = normalizeMusicPlayMode(value);
  scheduleSave(0);
  setRuntimeStatus(`播放模式：${state.musicPlayMode}`, { mode: musicPlaying ? "music" : "idle" });
  scheduleSettingsSnapshot();
}

function setMusicVolumeNormalization(value) {
  state.musicVolumeNormalization = normalizeBooleanSetting(value, DEFAULT_STATE.musicVolumeNormalization);
  scheduleSave(0);
  setRuntimeStatus(state.musicVolumeNormalization ? "音量均衡已开启" : "音量均衡已关闭", {
    mode: musicPlaying ? "music" : "idle"
  });
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
      ? `看屏幕模式：${getProfileIdentityText("name", CHARACTER_NAME)} 直看最近截图`
      : "看屏幕模式：先整理屏幕印象",
    { mode: "idle" }
  );
  scheduleSettingsSnapshot();
}

function setProactiveWakeEnabled(enabled) {
  state.proactiveWakeEnabled = Boolean(enabled);
  if (state.proactiveWakeEnabled) {
    proactiveWakeNextAllowedAt = Date.now() + getProactiveWakeIntervalMs();
    scheduleProactiveWake({ immediate: false });
    setRuntimeStatus("主动搭话已开启", { mode: "idle" });
  } else {
    window.clearTimeout(proactiveWakeTimer);
    proactiveWakeTimer = 0;
    proactiveWakeNextAllowedAt = 0;
    proactiveWakeRunning = false;
    setRuntimeStatus("主动搭话已关闭", { mode: "idle" });
  }
  scheduleSave(0);
  scheduleSettingsSnapshot();
}

function setProactiveWakeIntervalSec(value) {
  state.proactiveWakeIntervalSec = normalizeProactiveWakeIntervalSec(value);
  proactiveWakeNextAllowedAt = Date.now() + getProactiveWakeIntervalMs();
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
  els.resourceDetails.textContent = `${source} · ${activeOutfit.id || getProfileDefaultOutfit()} · ${count} 表情${requested}${outfitHint}${sessionHint}`;
}

function renderEmotionGrid() {
  if (!els.emotionGrid) return;
  const emotions = getActiveEmotions();
  const signature = emotions
    .map((emotion) => `${emotion.id}:${emotion.name || ""}`)
    .join("|");
  const active = state.currentEmotion || getProfileDefaultEmotion();
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
  scheduleChatInputAutoHide();
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
  clearChatInputAutoHide();
  els.chatForm.hidden = true;
  if (clear) els.chatInput.value = "";
  resetInputHistoryCursor();
  autoResizeChatInput();
  els.chatInput.blur();
  scheduleNativeHitTestSync({ force: true });
}

function scheduleChatInputAutoHide(delayMs = CHAT_INPUT_IDLE_HIDE_MS) {
  window.clearTimeout(chatInputIdleTimer);
  if (els.chatForm.hidden) return;
  chatInputIdleTimer = window.setTimeout(() => {
    chatInputIdleTimer = 0;
    if (els.chatForm.hidden) return;
    if (sending || voiceInputState === "recording" || voiceInputState === "processing") {
      scheduleChatInputAutoHide();
      return;
    }
    if (els.chatInput.value.trim()) return;
    if (els.chatForm.matches?.(":hover")) {
      scheduleChatInputAutoHide();
      return;
    }
    hideChatInput();
  }, delayMs);
}

function clearChatInputAutoHide() {
  window.clearTimeout(chatInputIdleTimer);
  chatInputIdleTimer = 0;
}

function setChatInputText(text, { append = false } = {}) {
  const value = String(text || "").trim();
  if (!value) return;
  const current = els.chatInput.value.trim();
  els.chatInput.value = append && current ? `${current} ${value}` : value;
  showChatInput();
  autoResizeChatInput();
  scheduleChatInputAutoHide();
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
  form.append("user_id", state.sessionId || "desktop_pet_next");
  form.append("session_id", state.sessionId || "desktop_pet_next");
  form.append("real_user_id", getProfileUserId());
  form.append("client_mode", CLIENT_MODE);
  form.append("character_pack_id", getCurrentCharacterPackId());

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
  if (!els.chatForm.hidden) scheduleChatInputAutoHide();
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
    setRestingPetEmotion();
    scheduleMusicEmotionRestore();
  }, 2700);
}

function clearLocalInteraction() {
  localInteractionToken += 1;
  localInteractionActive = false;
  window.clearTimeout(localInteractionTimer);
}

function pickLocalClickLine() {
  const lines = getProfileLocalClickLines();
  if (lines.length <= 1) {
    lastLocalClickIndex = 0;
    return lines[0];
  }
  let index = Math.floor(Math.random() * lines.length);
  if (index === lastLocalClickIndex) {
    index = (index + 1 + Math.floor(Math.random() * (lines.length - 1))) % lines.length;
  }
  lastLocalClickIndex = index;
  return lines[index];
}

function previewEmotion(emotion) {
  if (sending || !emotion) return;
  const previous = previewEmotionRestore || state.currentEmotion || getProfileDefaultEmotion();
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
  scheduleMusicEmotionRestore();
}

function openContextMenu(event) {
  event.preventDefault();
  event.stopPropagation();
  cancelLocalClick();
  showMenu({ x: event.clientX, y: event.clientY, source: "pointer" });
}

function toggleMenuNear(anchor) {
  if (!els.menu.hidden) {
    closeMenu();
    return;
  }
  const rect = anchor.getBoundingClientRect();
  showMenu({ x: rect.right - 2, y: rect.bottom + 8, anchor, source: "anchor" });
}

function showMenu(anchor) {
  menuAnchor = normalizeMenuAnchor(anchor);
  els.menu.hidden = false;
  els.menu.style.visibility = "hidden";
  updateConnectionStatus();
  repositionOpenMenu();
  els.menu.style.visibility = "";
  scheduleNativeHitTestSync({ force: true });
}

function repositionOpenMenu() {
  if (!menuAnchor || els.menu.hidden) return;
  const point = resolveMenuAnchorPoint(menuAnchor);
  placeMenuInsideViewport(point.x, point.y);
}

function normalizeMenuAnchor(anchor) {
  const source = anchor && typeof anchor === "object" ? anchor : {};
  return {
    x: Number(source.x) || MENU_VIEWPORT_MARGIN,
    y: Number(source.y) || MENU_VIEWPORT_MARGIN,
    anchor: source.anchor instanceof Element ? source.anchor : null,
    source: source.source === "anchor" ? "anchor" : "pointer"
  };
}

function resolveMenuAnchorPoint(anchor) {
  if (anchor.anchor && document.documentElement.contains(anchor.anchor)) {
    const rect = anchor.anchor.getBoundingClientRect();
    return { x: rect.right - 2, y: rect.bottom + MENU_VIEWPORT_MARGIN };
  }
  return { x: anchor.x, y: anchor.y };
}

function placeMenuInsideViewport(x, y) {
  const maxHeight = Math.max(96, Math.min(340, window.innerHeight - MENU_VIEWPORT_MARGIN * 2));
  els.menu.style.maxHeight = `${maxHeight}px`;
  const rect = els.menu.getBoundingClientRect();
  const maxX = window.innerWidth - rect.width - MENU_VIEWPORT_MARGIN;
  const maxY = window.innerHeight - rect.height - MENU_VIEWPORT_MARGIN;
  const left = clamp(Number(x) || MENU_VIEWPORT_MARGIN, MENU_VIEWPORT_MARGIN, Math.max(MENU_VIEWPORT_MARGIN, maxX));
  const top = clamp(Number(y) || MENU_VIEWPORT_MARGIN, MENU_VIEWPORT_MARGIN, Math.max(MENU_VIEWPORT_MARGIN, maxY));
  els.menu.style.left = `${Math.round(left)}px`;
  els.menu.style.top = `${Math.round(top)}px`;
}

function closeMenu() {
  menuAnchor = null;
  els.menu.hidden = true;
  els.menu.style.visibility = "";
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
  if (geometry) applyWindowGeometryToState(geometry);
  repositionOpenMenu();
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
    settleCarePassiveState({ persist: false });
    const geometry = await invoke("get_window_geometry");
    applyWindowGeometryToState(geometry);
    persistCurrentCharacterRuntimeState();
    await invoke("save_pet_state", { state });
  } catch (error) {
    setStatus(`Save failed: ${formatError(error)}`);
  }
}

function applyWindowGeometryToState(geometry) {
  if (!geometry || typeof geometry !== "object") return;
  lastWindowGeometry = {
    x: normalizeNullableInteger(geometry.x) ?? 0,
    y: normalizeNullableInteger(geometry.y) ?? 0,
    width: normalizePositiveInteger(geometry.width) ?? lastWindowGeometry?.width ?? Math.round(window.outerWidth || window.innerWidth || 340),
    height: normalizePositiveInteger(geometry.height) ?? lastWindowGeometry?.height ?? Math.round(window.outerHeight || window.innerHeight || 560)
  };
  state.x = normalizeNullableInteger(geometry.x);
  state.y = normalizeNullableInteger(geometry.y);
  state.width = null;
  state.height = null;
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

async function openPanelWindow() {
  closeMenu();
  if (!isTauriRuntime) {
    setStatus("面板仅 Tauri 可用");
    return;
  }
  await tauriCall("open_panel_window", {});
  schedulePanelStateSync(80);
}

function buildPanelStatePayload() {
  const media = systemMedia || {};
  const playing = media.playbackStatus === "playing";
  return {
    characterName: getProfileIdentityText("name", CHARACTER_NAME),
    emotion: state.currentEmotion || getProfileDefaultEmotion(),
    avatarSrc: els.petImage?.src || "",
    musicPlaying: playing,
    musicTitle: media.title || "",
    musicArtist: media.artist || "",
    musicPosition: Number(media.positionSeconds) || 0,
    musicDuration: Number(media.durationSeconds) || 0,
    musicPositionAt: playing ? Date.now() : 0,
    musicController: panelMusicController,
    muted: !state.voiceEnabled,
    scale: state.scale,
    opacity: state.opacity,
  };
}

function schedulePanelStateSync(delayMs = 500) {
  clearTimeout(panelSyncTimer);
  panelSyncTimer = setTimeout(pushPanelStateUpdate, delayMs);
}

async function pushPanelStateUpdate() {
  if (!isTauriRuntime) return;
  try {
    await emitPanelEvent("panel:state-update", buildPanelStatePayload());
  } catch {
    // Panel may not be open; silent failure is fine
  }
}

async function emitPanelEvent(eventName, payload) {
  try {
    await emitTo("panel", eventName, payload);
  } catch {
    await emit(eventName, payload);
  }
}

function getPanelProfileUserId() {
  return String(getProfileUserId() || state.profileUserId || "master").trim() || "master";
}

function panelMusicControllerFromControls(controls) {
  if (!controls || typeof controls !== "object") return panelMusicController;
  const allEnabled = PANEL_MUSIC_CONTROLS.every((name) => controls[name] !== false);
  return allEnabled ? "model" : "user";
}

function updatePanelMusicController(controller) {
  panelMusicController = controller === "user" ? "user" : "model";
  schedulePanelStateSync(0);
}

async function refreshPanelMusicController() {
  if (!isTauriRuntime) return;
  const response = await backendFetch(
    buildBackendEndpointUrl("music_control_permissions", "/capabilities/music/control_permissions", {
      user_id: state.sessionId || "desktop_pet_next",
      real_user_id: getPanelProfileUserId(),
      t: Date.now()
    }),
    {
      method: "GET",
      cache: "no-store",
      connectTimeout: 3000
    }
  ).catch(() => null);
  const payload = response?.ok ? await readJsonResponse(response) : null;
  if (payload?.ok && payload.controls && typeof payload.controls === "object") {
    updatePanelMusicController(panelMusicControllerFromControls(payload.controls));
  }
}

async function setPanelMusicController(controller) {
  if (!isTauriRuntime) return;
  const normalized = controller === "user" ? "user" : "model";
  const previous = panelMusicController;
  const enabled = normalized === "model";
  const response = await backendFetch(
    buildBackendEndpointUrl("music_control_permissions", "/capabilities/music/control_permissions", {
      user_id: state.sessionId || "desktop_pet_next",
      real_user_id: getPanelProfileUserId(),
      t: Date.now()
    }),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      connectTimeout: 3000,
      body: JSON.stringify({
        controls: Object.fromEntries(PANEL_MUSIC_CONTROLS.map((name) => [name, enabled]))
      })
    }
  ).catch(() => null);
  const payload = response?.ok ? await readJsonResponse(response) : null;
  if (payload?.ok && payload.controls && typeof payload.controls === "object") {
    updatePanelMusicController(panelMusicControllerFromControls(payload.controls));
  } else {
    updatePanelMusicController(previous);
  }
}

function formatPanelRecentTrack(item) {
  const title = String(item?.title || "").trim() || "某首歌";
  const artist = String(item?.artist || "").trim();
  const label = String(item?.last_listened_label || item?.timestamp_display || "").trim();
  const main = artist ? `${title} · ${artist}` : title;
  return label ? `${main}（${label}）` : main;
}

async function refreshPanelCoListenSummary() {
  if (!isTauriRuntime) return;
  const media = systemMedia || {};
  const response = await backendFetch(
    buildBackendEndpointUrl("music_co_listen_summary", "/capabilities/music/co_listen_summary", {
      user_id: state.sessionId || "desktop_pet_next",
      real_user_id: getPanelProfileUserId(),
      t: Date.now()
    }),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      connectTimeout: 5000,
      body: JSON.stringify({
        title: String(media.title || "").trim(),
        artist: String(media.artist || "").trim(),
        album: String(media.album || "").trim(),
        source_kind: media.ok ? "system_media" : "",
        source_app: String(media.sourceApp || "").trim(),
        system_media: Boolean(media.ok),
        recent_limit: 5
      })
    }
  ).catch(() => null);
  const payload = response?.ok ? await readJsonResponse(response) : null;
  if (!payload?.ok) return;
  const recent = Array.isArray(payload.recent) ? payload.recent : [];
  if (recent.length) {
    const items = recent.map(formatPanelRecentTrack).filter(Boolean).slice(0, 5);
    if (items.length) {
      void emitPanelEvent("panel:recent-update", items);
    }
  }
  if (Array.isArray(payload.enabled_music_controls)) {
    const enabledSet = new Set(payload.enabled_music_controls.map((name) => String(name)));
    updatePanelMusicController(
      PANEL_MUSIC_CONTROLS.every((name) => enabledSet.has(name)) ? "model" : "user"
    );
  }
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

async function openShopWindow() {
  closeMenu();
  const careConfig = getProfileCareConfig();
  if (!careConfig.enabled || !careConfig.shopItems.length) {
    setStatus("这个角色还没有配置商店。", { durationMs: 2200 });
    return;
  }
  if (!isTauriRuntime) {
    setStatus("商店窗口仅 Tauri 可用");
    return;
  }
  settleCarePassiveState();
  await saveNow();
  await tauriCall("open_shop_window", {});
  scheduleSettingsSnapshot(120);
}

async function openWorkshopWindow() {
  closeMenu();
  if (!isTauriRuntime) {
    setStatus("角色工坊窗口仅 Tauri 可用");
    return;
  }
  await saveNow();
  await tauriCall("open_workshop_window", {});
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
  state.outfit = outfit || getProfileDefaultOutfit();
  els.outfit.value = state.outfit;
  cancelEmotionPreview({ restore: true });
  scheduleSave(0);
  setStatus(`服装已设置：${state.outfit}`);
  await reloadCharacterResources({ userTriggered: true });
}

async function refreshCharacterPacksFromSettings(value) {
  const options = value && typeof value === "object" ? value : { selectPackId: value };
  const refreshed = await refreshRuntimeCharacterPacks({ userTriggered: true });
  const selectPackId = String(options.selectPackId || "").trim();
  if (refreshed && selectPackId && options.apply !== false) {
    await updateCharacterPack(selectPackId);
    return;
  }
  if (refreshed) {
    setStatus("角色包列表已刷新。", { durationMs: 1600 });
  }
}

async function refreshRuntimeCharacterPacks({
  userTriggered = false,
  silent = false,
  scheduleSnapshot = true
} = {}) {
  if (!isTauriRuntime) return false;
  const packs = await tauriCall("list_character_packs", {}, { quiet: true });
  if (!Array.isArray(packs)) {
    if (!silent && userTriggered) setStatus("角色包列表刷新失败。", { durationMs: 1800 });
    return false;
  }
  runtimeCharacterPacks = packs;
  setRuntimeCharacterPacks(packs);
  refreshLocalResourceAssets();
  if (scheduleSnapshot) scheduleSettingsSnapshot();
  return true;
}

async function updateCharacterPack(value) {
  const requestedPackId = String(value || "").trim();
  if (!requestedPackId) {
    throw new Error("角色包 ID 不能为空。");
  }
  if (isTauriRuntime) {
    await refreshRuntimeCharacterPacks({ silent: true });
  }
  const availablePack = listCharacterPacks().find((item) => item.id === requestedPackId);
  if (!availablePack) {
    throw new Error(`角色包 ${requestedPackId} 未加载，已保留当前角色。`);
  }

  const previousPackId = state.characterPackId || getActiveCharacterPackId();
  if (isTauriRuntime) {
    await saveNow();
  } else {
    persistCurrentCharacterRuntimeState(previousPackId);
  }
  const pack = selectCharacterPack(availablePack.id);
  if (pack.packId !== requestedPackId) {
    throw new Error(`角色包解析结果不一致：请求 ${requestedPackId}，得到 ${pack.packId}。`);
  }
  state.characterPackId = pack.packId;
  resourceState.manifest = null;
  resourceState.source = "character_pack";
  refreshLocalResourceAssets();
  applyCharacterRuntimeState(pack.packId, pack.profile);
  applyCharacterChrome();
  applyVisualState();
  setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });

  if (pack.packId === previousPackId) {
    scheduleSave(0);
    setStatus(`角色包已是：${pack.profile.identity.name}`);
    await reloadCharacterResources({ userTriggered: true });
    setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
    return {
      requestedPackId,
      activePackId: state.characterPackId,
      characterName: pack.profile.identity.name,
      resourceSource: resourceState.source
    };
  }

  setStatus(`角色包已切换为 ${pack.profile.identity.name}，正在应用。`, { durationMs: 2400 });
  if (isTauriRuntime) {
    await saveNow();
  }
  await reloadCharacterResources({ userTriggered: true });
  setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { persist: false, force: true });
  scheduleNativeWindowStateApply({ forceHitTest: true });
  void ensureBackendSession({ restoreLatest: state.restoreLatestOnStartup });
  return {
      requestedPackId,
    activePackId: state.characterPackId,
    characterName: pack.profile.identity.name,
    resourceSource: resourceState.source
  };
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
  persistCurrentCharacterRuntimeState();
  lastTurnSignature = "";
  lastTurnTextKey = "";
  lastActivityActionSignature = "";
  cancelEmotionPreview({ restore: false });
  closeMenu();
  setPetEmotion(getProfileDefaultEmotion());
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
    if (canRenderCurrentLocalResources()) {
      setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
    } else {
      state.currentEmotion = getProfileDefaultEmotion();
    }
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
    setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
    const count = getActiveEmotions().length;
    const message = `资源已加载：${getActiveOutfit().id} / ${count}`;
    if (!silent && userTriggered) showBubbleText(message, { transient: true });
    if (!silent || runtimeMode === "offline" || runtimeMode === "checking") {
      setRuntimeStatus(message, { mode: "idle" });
    }
    if (state.voiceEnabled) {
      scheduleTtsPrewarm({ delayMs: startup ? 900 : 300 });
    }
    return true;
  } catch (error) {
    resourceState.healthMessage = formatError(error);
    useBundledResources();
    if (canRenderCurrentLocalResources()) {
      setPetEmotion(state.currentEmotion || getProfileDefaultEmotion(), { force: true });
    } else {
      state.currentEmotion = getProfileDefaultEmotion();
    }
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
    real_user_id: getProfileUserId(),
    ...buildBackendCharacterContext(),
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
    const query = new URLSearchParams({
      ...buildBackendCharacterContext(),
      t: String(Date.now())
    });
    const response = await backendFetch(`${state.backendUrl}${LEGACY_HEALTH_PATH}?${query.toString()}`, {
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
    real_user_id: getProfileUserId(),
    client: CLIENT_MODE,
    character_pack_id: getCurrentCharacterPackId(),
    outfit: state.outfit || getProfileDefaultOutfit(),
    emotion: state.currentEmotion || getProfileDefaultEmotion(),
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
    findEntry(outfits, getProfileDefaultOutfit()) ||
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
      url: resolveAssetUrl(item.path || item.url || item.src, state.backendUrl)
    }))
    .filter((item) => item.id && item.url);

  if (emotions.length === 0) {
    throw new Error("manifest emotions have no image paths");
  }

  resourceState.manifest = manifest;
  resourceState.outfit = {
    ...outfit,
    id: String(outfit.id || getProfileDefaultOutfit()),
    name: String(outfit.name || outfit.id || getProfileDefaultOutfit()),
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
      getProfileDefaultOutfit()
  );
}

function getManifestDefaultEmotion(manifest) {
  const desktop = getDesktopManifestContract(manifest);
  return String(
    desktop?.default_emotion ||
      desktop?.defaultEmotion ||
      manifest?.defaults?.desktop_pet_emotion ||
      manifest?.defaults?.emotion ||
      getProfileDefaultEmotion()
  );
}

function useBundledResources() {
  resourceState.manifest = null;
  resourceState.outfit = findEntry(localOutfits, state.outfit) || getDefaultLocalOutfit();
  resourceState.source = getLocalResourceSource();
  resourceState.loadedAt = Date.now();
  if (!state.outfit) state.outfit = resourceState.outfit.id;
}

function canRenderCurrentLocalResources() {
  if (resourceState.source !== "bundled") return true;
  return canUseBundledEmotionFallback();
}

function canUseBundledEmotionFallback() {
  const packId = normalizeEntryKey(getCurrentCharacterPackId());
  const identityId = normalizeEntryKey(getActiveCharacterProfile()?.identity?.id);
  return packId === "akane_v1" || identityId === "akane_v1";
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
        session_id: state.sessionId,
        real_user_id: getProfileUserId(),
        display_title: getActiveCharacterText("sessionDisplayTitle"),
        ...buildBackendCharacterContext()
      })
    });

    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
    const bundle = await response.json();
    if (restoreLatest && restoreLatestReply(bundle)) {
      setRuntimeStatus("已恢复上一轮回复", { mode: "idle" });
    } else {
      setRuntimeStatus("后端已就绪", { mode: "idle" });
    }
    scheduleWorkspaceTaskWatch({ delayMs: 1200 });
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

function scheduleSystemMediaPoll({ immediate = false } = {}) {
  window.clearTimeout(systemMediaPollTimer);
  systemMediaPollTimer = 0;
  if (!isTauriRuntime) return;

  const delay = immediate ? 0 : SYSTEM_MEDIA_POLL_MS;
  systemMediaPollTimer = window.setTimeout(async () => {
    systemMediaPollTimer = 0;
    await refreshSystemMediaSnapshot();
    scheduleSystemMediaPoll();
  }, delay);
}

async function refreshSystemMediaSnapshot() {
  const previous = systemMedia;
  const snapshot = await tauriCall("get_current_system_media", {}, { quiet: true });
  systemMedia = normalizeSystemMediaSnapshot(snapshot);
  const trackChanged = systemMedia.trackKey !== previous.trackKey;
  const statusChanged =
    systemMedia.playbackStatus !== previous.playbackStatus ||
    systemMedia.status !== previous.status ||
    systemMedia.isPlaying !== previous.isPlaying;
  const progressChanged = Math.abs(safePositiveSeconds(systemMedia.positionSeconds) - safePositiveSeconds(previous.positionSeconds)) >= 1.2;
  if (trackChanged) {
    applySystemMediaLyricsForTrack(systemMedia);
    if (previous.title && previous.trackKey) {
      const entry = `${previous.title}${previous.artist ? " · " + previous.artist : ""}`;
      const idx = recentTracksHistory.indexOf(entry);
      if (idx !== -1) recentTracksHistory.splice(idx, 1);
      recentTracksHistory.unshift(entry);
      if (recentTracksHistory.length > 5) recentTracksHistory.length = 5;
      void emitPanelEvent("panel:recent-update", recentTracksHistory.slice());
    }
  }
  if (isFreshSystemMedia(systemMedia)) {
    void ensureSystemMediaLyrics(systemMedia);
  } else if (trackChanged || statusChanged) {
    systemMediaLyrics = emptySystemMediaLyricsSnapshot({
      trackKey: systemMedia.trackKey || "",
      status: systemMedia.status === "unavailable" ? "unavailable" : "not-found",
      reason: systemMedia.reason || "system_media_unavailable"
    });
  }
  if (trackChanged || statusChanged) {
    if (isMusicEmotionSourceActive()) {
      scheduleMusicEmotionRestore({ delayMs: 0 });
    } else if (!musicPlaying) {
      setMusicEmotion(false);
    }
  }
  if (
    trackChanged ||
    statusChanged ||
    (systemMedia.ok && progressChanged)
  ) {
    scheduleMusicSnapshot(120);
    schedulePanelStateSync(200);
  }
}

function emptySystemMediaSnapshot() {
  return {
    ok: false,
    status: "unavailable",
    reason: "not-polled",
    capturedAt: 0,
    platform: "",
    trackKey: "",
    title: "",
    artist: "",
    album: "",
    sourceApp: "",
    playbackStatus: "unknown",
    isPlaying: false,
    positionSeconds: 0,
    durationSeconds: 0
  };
}

function normalizeSystemMediaSnapshot(value) {
  if (!value || typeof value !== "object") return emptySystemMediaSnapshot();
  const title = cleanSystemMediaText(value.title, 120);
  const artist = cleanSystemMediaText(value.artist, 100);
  const album = cleanSystemMediaText(value.album, 120);
  const sourceApp = cleanSystemMediaText(value.sourceApp || value.source_app, 120);
  const playbackStatus = String(value.playbackStatus || value.playback_status || "unknown").trim().toLowerCase() || "unknown";
  const trackKey =
    cleanSystemMediaText(value.trackKey || value.track_key, 220) ||
    simpleHash(`${sourceApp}|${title}|${artist}|${album}`);
  const capturedAt = Number(value.capturedAt || value.captured_at || Date.now());
  return {
    ok: Boolean(value.ok) && Boolean(title || artist) && !isOwnSystemMediaSource(sourceApp),
    status: String(value.status || "").trim().toLowerCase() || (value.ok ? "ready" : "unavailable"),
    reason: cleanSystemMediaText(value.reason, 180),
    capturedAt: Number.isFinite(capturedAt) ? capturedAt : Date.now(),
    platform: cleanSystemMediaText(value.platform, 40),
    trackKey,
    title,
    artist,
    album,
    sourceApp,
    playbackStatus,
    isPlaying: Boolean(value.isPlaying || value.is_playing || playbackStatus === "playing"),
    positionSeconds: safePositiveSeconds(value.positionSeconds ?? value.position_seconds),
    durationSeconds: safePositiveSeconds(value.durationSeconds ?? value.duration_seconds)
  };
}

function cleanSystemMediaText(value, limit = 120) {
  const text = String(value || "").replace(/\x00/g, " ").replace(/\s+/g, " ").trim();
  return limit > 0 && text.length > limit ? text.slice(0, limit) : text;
}

function safePositiveSeconds(value) {
  const seconds = Number(value);
  return Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
}

function isOwnSystemMediaSource(sourceApp) {
  const source = String(sourceApp || "").toLowerCase();
  return source.includes("akane_desktop_pet_next") || source.includes("akane desktop pet");
}

function isFreshSystemMedia(snapshot = systemMedia) {
  if (!snapshot?.ok || snapshot.status !== "ready") return false;
  if (!snapshot.title && !snapshot.artist) return false;
  if (snapshot.playbackStatus === "closed" || snapshot.playbackStatus === "stopped") return false;
  const capturedAt = Number(snapshot.capturedAt || 0);
  return capturedAt > 0 && Date.now() - capturedAt <= SYSTEM_MEDIA_MAX_AGE_MS;
}

function isMusicEmotionSourceActive() {
  return Boolean(musicPlaying || (isFreshSystemMedia(systemMedia) && systemMedia.isPlaying));
}

function summarizeSystemMedia(snapshot = systemMedia) {
  return {
    ok: Boolean(snapshot?.ok),
    status: snapshot?.status || "unavailable",
    reason: snapshot?.reason || "",
    capturedAt: Number(snapshot?.capturedAt || 0),
    platform: snapshot?.platform || "",
    trackKey: snapshot?.trackKey || "",
    title: snapshot?.title || "",
    artist: snapshot?.artist || "",
    album: snapshot?.album || "",
    sourceApp: snapshot?.sourceApp || "",
    playbackStatus: snapshot?.playbackStatus || "unknown",
    isPlaying: Boolean(snapshot?.isPlaying),
    positionSeconds: safePositiveSeconds(snapshot?.positionSeconds),
    durationSeconds: safePositiveSeconds(snapshot?.durationSeconds),
    fresh: isFreshSystemMedia(snapshot)
  };
}

function emptySystemMediaLyricsSnapshot(overrides = {}) {
  return {
    ok: false,
    status: "unavailable",
    reason: "not-polled",
    trackKey: "",
    source: "",
    confidence: "",
    lineCount: 0,
    segments: [],
    cached: false,
    updatedAt: 0,
    ...overrides
  };
}

function applySystemMediaLyricsForTrack(snapshot = systemMedia) {
  systemMediaLyricsLastAttemptAt = 0;
  const trackKey = String(snapshot?.trackKey || "").trim();
  if (!trackKey) {
    systemMediaLyrics = emptySystemMediaLyricsSnapshot({ reason: "track_key_missing" });
    return;
  }
  const cached = systemMediaLyricsCache.get(trackKey);
  if (cached) {
    systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
    return;
  }
  systemMediaLyrics = emptySystemMediaLyricsSnapshot({
    status: "pending",
    reason: "lyrics_lookup_pending",
    trackKey
  });
}

async function ensureSystemMediaLyrics(snapshot = systemMedia, options = {}) {
  if (!isFreshSystemMedia(snapshot)) return;
  const trackKey = String(snapshot.trackKey || "").trim();
  if (!trackKey || !snapshot.title) return;
  const cached = systemMediaLyricsCache.get(trackKey);
  if (cached) {
    if (systemMediaLyrics.trackKey !== trackKey || systemMediaLyrics.status === "pending") {
      systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
      scheduleMusicSnapshot(120);
    }
    return;
  }
  const pendingRequest = systemMediaLyricsRequests.get(trackKey);
  if (pendingRequest) return pendingRequest;
  const now = Date.now();
  const force = Boolean(options.force);
  if (!force && systemMediaLyricsLastAttemptAt && now - systemMediaLyricsLastAttemptAt < SYSTEM_MEDIA_LYRICS_RETRY_MS) return;
  if (resourceState.health !== "online") {
    systemMediaLyrics = emptySystemMediaLyricsSnapshot({
      status: "unavailable",
      reason: "backend_offline",
      trackKey
    });
    scheduleMusicSnapshot(120);
    return;
  }

  systemMediaLyricsLoading = true;
  systemMediaLyricsLastAttemptAt = now;
  const currentTrackKey = String(systemMedia.trackKey || "").trim();
  if (currentTrackKey === trackKey || systemMediaLyrics.trackKey === trackKey) {
    systemMediaLyrics = {
      ...systemMediaLyrics,
      trackKey,
      status: systemMediaLyrics.status === "ready" ? systemMediaLyrics.status : "pending",
      reason: "lyrics_lookup_pending"
    };
  }
  scheduleMusicSnapshot(120);

  const request = (async () => {
    try {
      const sessionId = state.sessionId || "desktop_pet_next";
      const profileUserId = getProfileUserId();
      const response = await backendFetch(
        buildBackendEndpointUrl("music_lyrics", "/capabilities/music/lyrics", {
          user_id: sessionId,
          session_id: sessionId,
          real_user_id: profileUserId,
          t: Date.now()
        }),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify({
            user_id: sessionId,
            session_id: sessionId,
            real_user_id: profileUserId,
            trackKey,
            title: snapshot.title || "",
            artist: snapshot.artist || "",
            album: snapshot.album || "",
            source: "system_media",
            positionSeconds: safePositiveSeconds(snapshot.positionSeconds)
          }),
          connectTimeout: 20_000
        }
      );
      const payload = await readJsonResponse(response);
      const normalized = normalizeSystemMediaLyricsSnapshot(payload, trackKey);
      if (normalized.status !== "pending" && normalized.status !== "unavailable") {
        systemMediaLyricsCache.set(trackKey, normalized);
      }
      if (normalized.status === "disabled") {
        systemMediaLyricsCache.set(trackKey, normalized);
      }
      if (String(systemMedia.trackKey || "").trim() === trackKey || systemMediaLyrics.trackKey === trackKey) {
        systemMediaLyrics = normalized;
      }
      return normalized;
    } catch {
      const failed = emptySystemMediaLyricsSnapshot({
        status: "unavailable",
        reason: "lyrics_request_failed",
        trackKey
      });
      if (String(systemMedia.trackKey || "").trim() === trackKey || systemMediaLyrics.trackKey === trackKey) {
        systemMediaLyrics = failed;
      }
      return failed;
    } finally {
      systemMediaLyricsRequests.delete(trackKey);
      systemMediaLyricsLoading = systemMediaLyricsRequests.size > 0;
      scheduleMusicSnapshot(120);
    }
  })();
  systemMediaLyricsRequests.set(trackKey, request);
  return request;
}

function isLyricsFocusedTurnMessage(message) {
  const text = String(message || "").trim().toLowerCase();
  if (!text) return false;
  return /歌词|唱到|唱的是|哪一句|这一句|当前.*(歌|音乐)|这首|正在放|播放|听/.test(text);
}

function shouldWaitForSystemMediaLyricsForTurn(message, options = {}) {
  if (options.waitForLyricsHydration === false) return false;
  if (options.waitForLyricsHydration === true) return true;
  return isLyricsFocusedTurnMessage(message);
}

function isDesktopContextFocusedTurnMessage(message) {
  const text = String(message || "").trim().toLowerCase();
  if (!text) return false;
  return /剪贴板|复制|粘贴|当前窗口|前台|这个窗口|正在看|屏幕|网页|浏览器|页面/.test(text);
}

function getDesktopContextTurnWaitMs(message, options = {}) {
  if (options.waitDesktopContext === false) return 0;
  if (options.waitDesktopContext === true || isDesktopContextFocusedTurnMessage(message)) {
    return DESKTOP_CONTEXT_TURN_WAIT_FOCUSED_MS;
  }
  return DESKTOP_CONTEXT_TURN_WAIT_MS;
}

function waitForSystemMediaLyrics(request, timeoutMs) {
  if (!request || !Number.isFinite(timeoutMs) || timeoutMs <= 0) return Promise.resolve({ timedOut: false });
  let timeoutId = 0;
  return Promise.race([
    Promise.resolve(request).then((value) => ({ timedOut: false, value })),
    new Promise((resolve) => {
      timeoutId = window.setTimeout(() => resolve({ timedOut: true }), timeoutMs);
    })
  ]).finally(() => {
    if (timeoutId) window.clearTimeout(timeoutId);
  });
}

async function hydrateSystemMediaLyricsForTurn(message, options = {}) {
  if (!isFreshSystemMedia(systemMedia)) return { waited: false, status: "unavailable" };
  const trackKey = String(systemMedia.trackKey || "").trim();
  if (!trackKey || !systemMedia.title) return { waited: false, status: "unavailable" };
  const cached = systemMediaLyricsCache.get(trackKey);
  if (cached) {
    systemMediaLyrics = { ...cached, cached: true, segments: Array.isArray(cached.segments) ? [...cached.segments] : [] };
    return { waited: false, status: "cached" };
  }
  if (
    systemMediaLyrics.trackKey === trackKey &&
    systemMediaLyrics.status === "ready" &&
    Array.isArray(systemMediaLyrics.segments) &&
    systemMediaLyrics.segments.length > 0
  ) {
    return { waited: false, status: "ready" };
  }
  const focused = isLyricsFocusedTurnMessage(message);
  const shouldWait = shouldWaitForSystemMediaLyricsForTurn(message, options);
  const timeoutMs = shouldWait
    ? (focused ? SYSTEM_MEDIA_LYRICS_TURN_WAIT_FOCUSED_MS : SYSTEM_MEDIA_LYRICS_TURN_WAIT_MS)
    : 0;
  const force =
    focused ||
    (systemMediaLyrics.trackKey === trackKey && systemMediaLyrics.reason === "backend_offline");
  const request = ensureSystemMediaLyrics(systemMedia, { force });
  if (!timeoutMs) return { waited: false, status: "background" };
  const result = await waitForSystemMediaLyrics(request, timeoutMs);
  if (
    result.timedOut &&
    String(systemMedia.trackKey || "").trim() === trackKey &&
    systemMediaLyrics.trackKey === trackKey &&
    systemMediaLyrics.status === "pending"
  ) {
    systemMediaLyrics = {
      ...systemMediaLyrics,
      reason: "lyrics_lookup_slow",
      updatedAt: Date.now()
    };
    scheduleMusicSnapshot(120);
  }
  return {
    waited: true,
    timedOut: Boolean(result.timedOut),
    status: systemMediaLyrics.status || "unavailable"
  };
}

function normalizeSystemMediaLyricsSnapshot(payload, fallbackTrackKey = "") {
  const value = payload && typeof payload === "object" ? payload : {};
  const status = String(value.status || (value.ok ? "ready" : "unavailable")).trim().toLowerCase() || "unavailable";
  const confidence = String(value.confidence || "").trim().toLowerCase();
  const source = cleanSystemMediaText(value.source || value.provider || "", 80);
  const segments =
    status === "ready" && confidence !== "low"
      ? normalizeTimelineLyricSegments(value.segments)
      : [];
  return emptySystemMediaLyricsSnapshot({
    ok: Boolean(value.ok) && status === "ready" && segments.length > 0,
    status,
    reason: cleanSystemMediaText(value.reason, 120),
    trackKey: cleanSystemMediaText(value.trackKey || value.track_key || fallbackTrackKey, 220),
    source,
    confidence,
    lineCount: segments.length || Number(value.lineCount || value.line_count || 0) || 0,
    segments,
    cached: Boolean(value.cached),
    updatedAt: Date.now()
  });
}

function buildSystemMediaLyricSnapshot(timeSeconds = safePositiveSeconds(systemMedia.positionSeconds)) {
  const trackKey = String(systemMedia.trackKey || "").trim();
  if (!trackKey || systemMediaLyrics.trackKey !== trackKey) return null;
  const status = String(systemMediaLyrics.status || "unavailable").trim().toLowerCase();
  const lines = Array.isArray(systemMediaLyrics.segments) ? systemMediaLyrics.segments : [];
  if (status !== "ready" || !lines.length || systemMediaLyrics.confidence === "low") {
    return {
      source: systemMediaLyrics.source ? `online:${systemMediaLyrics.source}` : "online",
      status,
      reason: systemMediaLyrics.reason || "",
      confidence: systemMediaLyrics.confidence || "",
      lineCount: Number(systemMediaLyrics.lineCount || 0),
      index: -1,
      timeSeconds: 0,
      text: "",
      previousText: "",
      nextText: ""
    };
  }
  let currentIndex = -1;
  const currentTime = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
  for (let index = 0; index < lines.length; index += 1) {
    const start = safePositiveSeconds(lines[index].timeSeconds);
    const end = safePositiveSeconds(lines[index].endSeconds);
    if (start <= currentTime + 0.12) currentIndex = index;
    if (end > currentTime + 0.12) break;
  }
  const current = currentIndex >= 0 ? lines[currentIndex] : null;
  const previous = currentIndex > 0 ? lines[currentIndex - 1] : null;
  const next = lines[Math.max(0, currentIndex + 1)] || null;
  return {
    source: systemMediaLyrics.source ? `online:${systemMediaLyrics.source}` : "online",
    status,
    reason: systemMediaLyrics.reason || "",
    confidence: systemMediaLyrics.confidence || "",
    lineCount: lines.length,
    index: currentIndex,
    timeSeconds: current?.timeSeconds ?? 0,
    text: cleanSystemMediaText(current?.text || "", 120),
    previousText: cleanSystemMediaText(previous?.text || "", 100),
    nextText: cleanSystemMediaText(next?.text || "", 100)
  };
}

function summarizeSystemMediaLyrics() {
  const lyric = buildSystemMediaLyricSnapshot();
  if (!lyric) {
    return {
      ok: false,
      status: systemMediaLyrics.status || "unavailable",
      reason: systemMediaLyrics.reason || "",
      source: systemMediaLyrics.source || "",
      confidence: systemMediaLyrics.confidence || "",
      lineCount: Number(systemMediaLyrics.lineCount || 0),
      cached: Boolean(systemMediaLyrics.cached),
      index: -1,
      current: "",
      previous: "",
      next: ""
    };
  }
  return {
    ok: Boolean(lyric.text),
    status: lyric.status || "unavailable",
    reason: lyric.reason || "",
    source: lyric.source || "",
    confidence: lyric.confidence || "",
    lineCount: Number(lyric.lineCount || 0),
    cached: Boolean(systemMediaLyrics.cached),
    index: lyric.index ?? -1,
    current: lyric.text || "",
    previous: lyric.previousText || "",
    next: lyric.nextText || ""
  };
}

function waitForLatencyBudget(promise, timeoutMs, fallbackFactory, timeoutEvent) {
  const timeout = Number(timeoutMs);
  if (!Number.isFinite(timeout) || timeout <= 0) {
    return Promise.resolve(typeof fallbackFactory === "function" ? fallbackFactory() : null);
  }

  let timeoutId = 0;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((resolve) => {
      timeoutId = window.setTimeout(() => {
        markTurnLatency(timeoutEvent || "latency-budget-timeout", { timeoutMs: timeout });
        resolve(typeof fallbackFactory === "function" ? fallbackFactory() : null);
      }, timeout);
    })
  ]).finally(() => {
    if (timeoutId) window.clearTimeout(timeoutId);
  });
}

function buildCachedDesktopContextForTurn() {
  if (!state.desktopContextEnabled) return null;
  const foreground = isFreshForegroundCache(lastDesktopForeground)
    ? normalizeForegroundContext(lastDesktopForeground)
    : null;
  if (!foreground) return null;
  return {
    ok: true,
    enabled: true,
    captured_at: Date.now(),
    platform: navigator.platform || "unknown",
    foreground,
    clipboard: {
      included: false,
      reason: state.clipboardContextEnabled ? "latency_budget" : "disabled"
    }
  };
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
        real_user_id: getProfileUserId(),
        ...buildBackendCharacterContext(),
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
        real_user_id: getProfileUserId(),
        ...buildBackendCharacterContext(),
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

  cancelTtsPrewarm();
  stopTts();
  clearLocalInteraction();
  firstSpeechSegmentShown = false;
  lastTurnSignature = "";
  lastTurnTextKey = "";
  lastActivityActionSignature = "";
  resetStreamingTtsState();
  resetStreamingReplyState();
  desktopFileDeliveryHandled.clear();
  window.clearTimeout(bubbleTimer);
  window.clearTimeout(segmentTimer);
  bubbleToken += 1;
  bubbleKind = "none";
  replyDisplayActive = false;

  if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
    setRestingPetEmotion();
  }
  setPetMotion("idle");
  scheduleMusicEmotionRestore();

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

function nowForTurnLatency() {
  return window.performance?.now ? window.performance.now() : Date.now();
}

function isTurnLatencyDebugEnabled() {
  try {
    const storage = window.localStorage;
    return storage?.getItem("akane.debug.turn") === "1" || storage?.getItem("akane.debug.latency") === "1";
  } catch {
    return false;
  }
}

function createTurnLatencyTrace(kind, turnToken, details = {}) {
  if (!isTurnLatencyDebugEnabled()) return null;
  const startedAt = nowForTurnLatency();
  const seen = new Set();
  const trace = {
    turnToken,
    mark(event, eventDetails = {}) {
      if (!isTurnActive(turnToken)) return;
      const elapsedMs = Math.round((nowForTurnLatency() - startedAt) * 10) / 10;
      console.debug("[Akane Turn]", event, {
        ms: elapsedMs,
        kind,
        turnToken,
        ...details,
        ...eventDetails
      });
    },
    markOnce(event, eventDetails = {}) {
      if (seen.has(event)) return;
      seen.add(event);
      trace.mark(event, eventDetails);
    }
  };
  trace.mark("turn-created");
  return trace;
}

function markTurnLatency(event, details = {}) {
  activeTurnLatencyTrace?.mark(event, details);
}

function markTurnLatencyOnce(event, details = {}) {
  activeTurnLatencyTrace?.markOnce(event, details);
}

function finishTurnLatencyTrace(turnToken) {
  if (activeTurnLatencyTrace?.turnToken === turnToken) {
    activeTurnLatencyTrace = null;
  }
}

async function sendMessage(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return;

  rememberInputHistory(trimmed);
  interruptReply({ announce: false });
  const turnToken = ++activeTurnToken;
  activeTurnLatencyTrace = createTurnLatencyTrace("user", turnToken, { messageLength: trimmed.length });
  sending = true;
  let restoreText = "";
  cancelEmotionPreview({ restore: true });
  clearLocalInteraction();
  firstSpeechSegmentShown = false;
  lastTurnSignature = "";
  lastTurnTextKey = "";
  resetStreamingTtsState(turnToken);
  resetStreamingReplyState(turnToken);
  desktopFileDeliveryHandled.clear();
  showThinking();
  markTurnLatency("thinking-shown");
  scheduleSettingsSnapshot();

  try {
    if (resourceState.health !== "online") {
      markTurnLatency("resource-reload-start", { health: resourceState.health });
      const healthy = await reloadCharacterResources();
      markTurnLatency("resource-reload-finished", { healthy, health: resourceState.health });
      if (!healthy) throw new Error("后端未连接");
    }
    if (!isTurnActive(turnToken)) return;
    const stream = sendThinkStream(trimmed, turnToken);
    const rendered = await processThinkStream(stream, turnToken);
    if (!rendered) throw new Error("未收到回复");
  } catch (error) {
    if (!isTurnActive(turnToken)) return;
    restoreText = trimmed;
    firstSpeechSegmentShown = false;
    showError(isAbortLike(error) ? "请求超时" : formatError(error));
  } finally {
    markTurnLatency("turn-finished");
    if (isTurnActive(turnToken)) {
      sending = false;
      if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
        setRestingPetEmotion();
      }
      if (!els.bubble.classList.contains("visible")) {
        setPetMotion("idle");
      }
      scheduleMusicEmotionRestore();
      if (restoreText) {
        restoreFailedInput(restoreText);
      }
      updateActivityControls();
      scheduleSettingsSnapshot();
    }
    finishTurnLatencyTrace(turnToken);
  }
}

function scheduleProactiveWake({ immediate = false, delayMs = null } = {}) {
  window.clearTimeout(proactiveWakeTimer);
  proactiveWakeTimer = 0;
  if (!isTauriRuntime || !state.proactiveWakeEnabled) return;
  const now = Date.now();
  const remainingMs = Math.max(0, proactiveWakeNextAllowedAt - now);
  const baseDelay = Number.isFinite(Number(delayMs))
    ? Number(delayMs)
    : immediate
      ? 1000
      : Math.max(getProactiveWakeIntervalMs(), remainingMs);
  const jitter = immediate || Number.isFinite(Number(delayMs)) ? 1 : 0.85 + Math.random() * 0.3;
  proactiveWakeTimer = window.setTimeout(() => {
    proactiveWakeTimer = 0;
    void runProactiveWake();
  }, Math.max(1000, Math.round(baseDelay * jitter)));
}

async function runProactiveWake() {
  if (!state.proactiveWakeEnabled) return;
  const remainingMs = proactiveWakeNextAllowedAt - Date.now();
  if (remainingMs > 0) {
    scheduleProactiveWake({ delayMs: remainingMs });
    return;
  }
  if (!canStartProactiveWake()) {
    scheduleProactiveWake({ delayMs: Math.max(PROACTIVE_WAKE_RETRY_MS, getProactiveWakeRemainingMs()) });
    return;
  }
  await sendProactiveWake();
  scheduleProactiveWake();
}

function getProactiveWakeIntervalMs() {
  return normalizeProactiveWakeIntervalSec(state.proactiveWakeIntervalSec) * 1000;
}

function getProactiveWakeRemainingMs() {
  return Math.max(0, proactiveWakeNextAllowedAt - Date.now());
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
  activeTurnLatencyTrace = createTurnLatencyTrace("proactive", turnToken);
  const startedAt = Date.now();
  proactiveWakeLastAt = startedAt;
  proactiveWakeNextAllowedAt = startedAt + getProactiveWakeIntervalMs();
  proactiveWakeRunning = true;
  sending = true;
  firstSpeechSegmentShown = false;
  lastTurnSignature = "";
  lastTurnTextKey = "";
  resetStreamingTtsState(turnToken);
  resetStreamingReplyState(turnToken);
  desktopFileDeliveryHandled.clear();
  scheduleSettingsSnapshot();

  try {
    if (resourceState.health !== "online") {
      markTurnLatency("resource-reload-start", { health: resourceState.health });
      const healthy = await reloadCharacterResources({ silent: true });
      markTurnLatency("resource-reload-finished", { healthy, health: resourceState.health });
      if (!healthy) return;
    }
    if (!isTurnActive(turnToken)) return;
    const stream = sendThinkStream(buildProactiveWakeMessage(), turnToken, {
      turnKind: "desktop_pet_proactive",
      transientUserMessage: true,
      desktopScreenFrames: latestDesktopScreenFramesForThink()
    });
    await processThinkStream(stream, turnToken);
    proactiveWakeLastAt = startedAt;
  } catch (error) {
    if (!isTurnActive(turnToken) || isAbortLike(error)) return;
    firstSpeechSegmentShown = false;
    setRuntimeStatus(`主动搭话暂时失败：${formatError(error)}`, { mode: "error" });
  } finally {
    markTurnLatency("turn-finished");
    proactiveWakeRunning = false;
    if (isTurnActive(turnToken)) {
      sending = false;
      if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
        setRestingPetEmotion();
      }
      if (!els.bubble.classList.contains("visible")) {
        setPetMotion("idle");
      }
      scheduleMusicEmotionRestore();
      updateActivityControls();
      scheduleSettingsSnapshot();
    }
    finishTurnLatencyTrace(turnToken);
  }
}

function buildProactiveWakeMessage() {
  const prompt = getActiveCharacterText(
    "proactiveWakePrompt",
    "主人暂时没有说话。你像坐在旁边陪他一样，自然地轻声搭一句话。"
  );
  return `${prompt}\n\n${PROACTIVE_WAKE_STYLE_GUARD}`;
}

async function* readNdjsonEvents(response) {
  const reader = response.body?.getReader();
  if (!reader) {
    // Fallback: response body streaming not available.
    const raw = await response.text();
    const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const tail = !lines.length && raw.trim() ? [raw.trim()] : [];
    for (const line of [...lines, ...tail]) {
      try { yield JSON.parse(line); } catch { /* skip */ }
    }
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const segments = buffer.split(/\r?\n/);
    buffer = segments.pop() || "";

    for (const segment of segments) {
      const trimmed = segment.trim();
      if (!trimmed) continue;
      try {
        yield JSON.parse(trimmed);
      } catch {
        // Skip malformed stream lines.
      }
    }
  }

  buffer += decoder.decode();
  const tail = buffer.trim();
  if (tail) {
    try { yield JSON.parse(tail); } catch { /* skip */ }
  }
}

async function* sendThinkStream(message, turnToken, options = {}) {
  const controller = new AbortController();
  thinkController = controller;
  const timeoutId = window.setTimeout(() => controller.abort(), THINK_TIMEOUT_MS);
  markTurnLatency("turn-context-start");
  const desktopContextPromise = collectDesktopContextForTurn()
    .then((desktopContext) => {
      markTurnLatency("desktop-context-ready", { included: Boolean(desktopContext) });
      return desktopContext;
    })
    .catch((error) => {
      markTurnLatency("desktop-context-error", { error: formatError(error).slice(0, 120) });
      return null;
    });
  const lyricsHydrationPromise = hydrateSystemMediaLyricsForTurn(message, options)
    .then((result) => {
      markTurnLatency("lyrics-hydration-ready", {
        waited: Boolean(result?.waited),
        timedOut: Boolean(result?.timedOut),
        status: String(result?.status || systemMediaLyrics.status || "")
      });
      return result;
    })
    .catch((error) => {
      markTurnLatency("lyrics-hydration-error", { error: formatError(error).slice(0, 120) });
      return { waited: false, status: "error" };
    });
  const desktopContext = await waitForLatencyBudget(
    desktopContextPromise,
    getDesktopContextTurnWaitMs(message, options),
    buildCachedDesktopContextForTurn,
    "desktop-context-timeout"
  );
  if (shouldWaitForSystemMediaLyricsForTurn(message, options)) {
    await lyricsHydrationPromise;
  } else {
    markTurnLatency("lyrics-hydration-background");
  }
  if (!isTurnActive(turnToken)) return;
  settleCarePassiveState();
  applyCareTurnCost(options.turnKind);
  const requestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({
      user_id: state.sessionId,
      real_user_id: getProfileUserId(),
      message,
      turn_kind: String(options.turnKind || ""),
      transient_user_message: Boolean(options.transientUserMessage),
      client_mode: CLIENT_MODE,
      character_pack_id: getCurrentCharacterPackId(),
      client_capabilities: buildClientCapabilities(),
      current_visual: buildCurrentVisual(),
      desktop_care: buildDesktopCareContext(),
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
    markTurnLatency("think-request-start");
    response = await backendFetch(buildBackendEndpointUrl("think", "/think", { t: Date.now() }), requestInit);
    markTurnLatency("think-response-headers", { status: response.status });
  } finally {
    window.clearTimeout(timeoutId);
    if (thinkController === controller) thinkController = null;
  }

  if (!isTurnActive(turnToken)) return;
  if (!response.ok) {
    throw new Error(await readBackendErrorMessage(response, `HTTP ${response.status}`));
  }

  for await (const event of readNdjsonEvents(response)) {
    if (!isTurnActive(turnToken)) return;
    yield event;
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
      markTurnLatency("stream-turn-start");
      if (!rendered && !streamingReplyText && !firstSpeechSegmentShown) {
        showThinking();
      }
    } else if (type === "ui") {
      applyPayloadEmotion(event);
    } else if (type === "speech_chunk") {
      const chunk = String(event?.text || "");
      if (chunk) partialSpeech += chunk;
    } else if (type === "speech_segment") {
      const text = String(event?.text || "").trim();
      if (text) markTurnLatencyOnce("first-speech-segment", { chars: text.length, index: event?.index });
      if (queueStreamedReplySegment(text, turnToken, event?.index) || streamingReplyText) {
        rendered = true;
      }
      queueStreamedTtsSegment(text, turnToken, event?.index);
    } else if (type === "file_ready" || type === "generated_file_ready") {
      void handleDesktopFileDeliveryEvent(event);
    } else if (type === "browser_open_requested") {
      void handleBrowserOpenEvent(event);
    } else if (type === "assistant_working") {
      const hasShownReply = rendered || Boolean(streamingReplyText) || firstSpeechSegmentShown;
      showToolWorking(event, { hasShownReply });
    } else if (type === "final" || type === "final_ui") {
      const payload = event?.payload || event;
      if (renderPayload(payload)) {
        rendered = true;
        firstSpeechSegmentShown = false;
      }
    } else if (type === "npc_turn") {
      if (!rendered && renderPayload(event)) {
        rendered = true;
        firstSpeechSegmentShown = false;
      }
    } else if (type === "stream_error" || type === "error") {
      streamErrored = true;
      streamErrorMessage = String(event?.message || "Stream error");
      if (event?.partial && !rendered) {
        firstSpeechSegmentShown = false;
        if (renderPayload(event.partial)) rendered = true;
      }
    } else if (type === "stream_end") {
      if (streamingReplyText) {
        finalizeStreamedReplyDisplay();
        rendered = true;
      }
      if (event?.partial && !rendered) {
        firstSpeechSegmentShown = false;
        if (renderPayload(event.partial)) rendered = true;
      }
    }
  }

  if (!isTurnActive(turnToken)) return false;
  if (!rendered && partialSpeech.trim()) {
    firstSpeechSegmentShown = false;
    rendered = renderPayload({ speech: partialSpeech.trim() });
  }

  if (!rendered && streamErrored) {
    throw new Error(streamErrorMessage || "未收到完整回应");
  }
  if (!rendered && bubbleKind === "thinking") {
    hideBubble();
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
  applyPayloadFileDeliveries(payload);
  applyPayloadBrowserEvents(payload);

  const segments = normalizeSegments(payload.speech_segments || payload.segments);
  if (segments.length > 0) {
    const signature = `segments:${segments.join("\u241e")}`;
    const textKey = buildSpeechTextKey(segments.join(""));
    if (!force && (signature === lastTurnSignature || (textKey && textKey === lastTurnTextKey))) return false;
    lastTurnSignature = signature;
    lastTurnTextKey = textKey;
    applyPayloadStateRequest(payload, { source });
    if (source === "live" && streamingReplyText) {
      queueLiveReplyPayloadItems(segments, { speaking });
    } else {
      showSpeechSegments(segments, { speaking });
    }
    if (source === "live") setRuntimeStatus("回复中", { mode: "replying" });
    if (source === "live") queueLiveTtsPayloadItems(segments, signature);
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
  applyPayloadStateRequest(payload, { source });
  if (source === "live" && streamingReplyText) {
    queueLiveReplyPayloadItems(displaySegments.length ? displaySegments : [speech], { speaking });
  } else if (displaySegments.length) {
    showSpeechSegments(displaySegments, { speaking });
  } else {
    showBubbleText(speech, { transient: false, dismiss: true, speaking, kind: "reply" });
  }
  if (source === "live") {
    setRuntimeStatus("回复中", { mode: "replying" });
    queueLiveTtsPayloadItems(displaySegments.length ? displaySegments : [speech], signature);
  }
  return true;
}

function applyPayloadEmotion(payload, { persist = true } = {}) {
  const emotion = String(payload?.emotion || "").trim();
  if (emotion) setPetEmotion(emotion, { persist });
}

function applyPayloadStateRequest(payload, { source = "live" } = {}) {
  if (source !== "live") return false;
  const request = payload?.state_request || payload?.stateRequest;
  if (!request || typeof request !== "object" || Array.isArray(request)) return false;
  const rawAffinity = request.affinity ?? request.affection_delta ?? request.affectionDelta;
  if (rawAffinity === undefined || rawAffinity === null || rawAffinity === "") return false;
  const numericAffinity = Number(rawAffinity);
  if (!Number.isFinite(numericAffinity)) return false;
  const affinityDelta = Math.min(5, Math.max(-5, Math.round(numericAffinity)));
  if (!affinityDelta) return false;
  const signature = `affinity:${affinityDelta}:${lastTurnSignature || lastTurnTextKey || activeTurnToken}`;
  if (signature === lastStateRequestSignature) return false;
  const config = getProfileCareConfig();
  if (!config.enabled) return false;
  const care = normalizeCareState(state.care, config);
  const nextAffection = clampCareValue(care.affection + affinityDelta, 0, 100);
  if (nextAffection === care.affection) {
    lastStateRequestSignature = signature;
    return false;
  }
  care.affection = nextAffection;
  care.updatedAt = Date.now();
  state.care = care;
  lastStateRequestSignature = signature;
  persistCareRuntimeChange();
  return true;
}

function isSystemMediaControlAction(action) {
  return ["play", "resume", "pause", "stop", "next", "skip", "previous", "prev"].includes(String(action || "").trim().toLowerCase());
}

function normalizeSystemMediaControlAction(action) {
  const normalized = String(action || "").trim().toLowerCase();
  if (normalized === "resume") return "play";
  if (normalized === "skip") return "next";
  if (normalized === "prev") return "previous";
  return normalized;
}

function activityExplicitlyTargetsSystemMedia(activity, sourceId) {
  const source = String(sourceId || "").trim().toLowerCase();
  const handle = String(activity?.handle || activity?.target || "").trim().toLowerCase();
  return (
    source.startsWith("system_media:") ||
    source === "system_media_current" ||
    handle === "system_media" ||
    handle === "system_media_current" ||
    activity?.system_media === true ||
    String(activity?.source_kind || activity?.sourceKind || "").trim().toLowerCase() === "system_media"
  );
}

function shouldControlSystemMediaForActivity(activity, action, sourceId) {
  if (!isSystemMediaControlAction(action) || !isFreshSystemMedia(systemMedia)) return false;
  if (activityExplicitlyTargetsSystemMedia(activity, sourceId)) return true;
  if (!musicTrack) return true;
  const normalized = normalizeSystemMediaControlAction(action);
  if (!musicPlaying && systemMedia.isPlaying && ["pause", "stop", "next", "previous"].includes(normalized)) return true;
  if (!musicPlaying && !musicPaused && ["play", "next", "previous"].includes(normalized)) return true;
  return false;
}

function systemMediaControlMessage(action, result) {
  const normalized = normalizeSystemMediaControlAction(action);
  const title = [result?.title || systemMedia.title, result?.artist || systemMedia.artist].filter(Boolean).join(" - ");
  const suffix = title ? `：${title}` : "";
  if (normalized === "play") return `已请求系统播放器继续播放${suffix}`;
  if (normalized === "pause") return `已请求系统播放器暂停${suffix}`;
  if (normalized === "stop") return `已请求系统播放器停止${suffix}`;
  if (normalized === "next") return "已请求系统播放器切到下一首。";
  if (normalized === "previous") return "已请求系统播放器切到上一首。";
  return "已请求系统播放器执行操作。";
}

/**
 * 播"伸手"CSS 动画并在峰值（600ms）时 resolve。
 * 1200ms 后 class 自动移除。
 * Live2D 接入时：用 Live2D motion API 替换 classList 操作，保持 Promise 接口不变。
 */
function triggerPetReachGesture() {
  const PEAK_MS = 600;
  const TOTAL_MS = 1200;
  return new Promise((resolve) => {
    const stage = els.stage;
    if (!stage) {
      resolve();
      return;
    }
    stage.classList.remove("is-reaching");
    void stage.offsetWidth;
    stage.classList.add("is-reaching");
    window.setTimeout(resolve, PEAK_MS);
    window.setTimeout(() => stage.classList.remove("is-reaching"), TOTAL_MS);
  });
}

async function controlSystemMediaPlayback(action) {
  if (!isTauriRuntime) {
    notifyMusicActivityUnavailable("系统媒体控制只在桌面端可用。");
    return false;
  }
  const normalized = normalizeSystemMediaControlAction(action);
  if (!["play", "pause", "stop", "next", "previous"].includes(normalized)) return false;

  await triggerPetReachGesture();

  const result = await tauriCall("control_system_media", { action: normalized }, { quiet: true });
  if (result?.ok) {
    const message = systemMediaControlMessage(normalized, result);
    setRuntimeStatus(message, { mode: normalized === "pause" || normalized === "stop" ? "music-paused" : "music" });
    showBubbleText(message, { transient: true, durationMs: 2200, kind: "music" });
    window.setTimeout(() => {
      void refreshSystemMediaSnapshot();
    }, normalized === "next" || normalized === "previous" ? 600 : 180);
    window.setTimeout(() => {
      lastActivityActionSignature = "";
    }, 900);
    return true;
  }
  const reason = String(result?.reason || "unavailable").trim();
  const message = reason === "no_active_session"
    ? "现在没有可控制的系统播放器。"
    : reason === "session_rejected"
      ? "这个播放器暂时不接受系统媒体控制。"
      : "系统媒体控制暂时不可用。";
  notifyMusicActivityUnavailable(message);
  return false;
}

function applyPayloadActivity(payload) {
  const activity = payload?.activity;
  if (!activity || typeof activity !== "object") return;

  const action = String(activity.action || "").trim().toLowerCase();
  if (!action) return;
  const sourceId = String(activity.source_id || activity.sourceId || "").trim();

  const actionSignature = [
    action,
    sourceId,
    musicQueue.length,
    musicQueueIndex,
    musicTrack?.sourceId || "",
    musicPlaying ? "playing" : "not-playing",
    musicPaused ? "paused" : "not-paused",
    systemMedia?.trackKey || "",
    systemMedia?.playbackStatus || "unknown"
  ].join(":");
  if (actionSignature === lastActivityActionSignature) return;
  lastActivityActionSignature = actionSignature;

  if (shouldControlSystemMediaForActivity(activity, action, sourceId)) {
    void controlSystemMediaPlayback(action);
    updateActivityControls();
    scheduleSettingsSnapshot();
    return;
  }

  if (action === "next" || action === "skip") {
    if (!hasNextMusicTrack()) {
      notifyMusicActivityUnavailable("后面没有更多歌曲了。");
      return;
    }
    void playNextMusicTrack();
  } else if (action === "previous" || action === "prev") {
    if (!hasPreviousMusicTrack()) {
      notifyMusicActivityUnavailable("前面没有歌曲了。");
      return;
    }
    void playPreviousMusicTrack();
  } else if (action === "play") {
    if (sourceId) {
      const requestedIndex = findMusicTrackIndexBySourceId(sourceId);
      if (requestedIndex >= 0) {
        if (requestedIndex === musicQueueIndex && musicPlaying) return;
        if (requestedIndex === musicQueueIndex && !musicPlaying) {
          void toggleMusicPlayback();
          updateActivityControls();
          scheduleSettingsSnapshot();
          return;
        }
        void playMusicQueueIndex(requestedIndex, {
          message: `切到这首：《${musicQueue[requestedIndex].displayName}》。`
        });
      } else if (/^workspace:(attachment|generated):.+/.test(sourceId)) {
        const parts = sourceId.split(":");
        void playWorkspaceAudioItem({
          itemType: parts[1],
          handle: parts.slice(2).join(":"),
          title: ""
        });
      } else {
        const catalogItem = workspaceAudioCatalog.find((rec) =>
          rec.sourceId === sourceId || rec.handle === sourceId
        );
        if (catalogItem && catalogItem.handle) {
          void playWorkspaceAudioItem({
            itemType: catalogItem.itemType || "attachment",
            handle: catalogItem.handle,
            title: catalogItem.title || ""
          });
        } else {
          notifyMusicActivityUnavailable("这首还没有加入播放队列。");
          return;
        }
      }
    } else if (musicPaused) {
      void toggleMusicPlayback();
    } else if (musicPlaying) {
      return;
    } else {
      const queueIndex = getSafeMusicQueueIndex();
      if (queueIndex < 0) {
        notifyMusicActivityUnavailable("我手边还没有可播放的音乐，可以先拖入一首，或者从推荐里点一首。");
        return;
      }
      void playMusicQueueIndex(queueIndex, {
        message: `播放：《${musicQueue[queueIndex].displayName}》。`
      });
    }
  } else if (action === "pause") {
    if (musicPlaying) {
      els.musicPlayer.pause();
      musicPlaying = false;
      musicPaused = true;
      setMusicEmotion(false);
      setRuntimeStatus(`音乐已暂停：${getMusicDisplayName()}`, { mode: "music-paused" });
    } else {
      notifyMusicActivityUnavailable("现在没有正在播放的音乐。");
      return;
    }
  } else if (action === "resume") {
    if (musicPaused) {
      void toggleMusicPlayback();
    } else if (musicPlaying) {
      notifyMusicActivityUnavailable("音乐已经在播放了。");
      return;
    } else {
      const queueIndex = getSafeMusicQueueIndex();
      if (queueIndex < 0) {
        notifyMusicActivityUnavailable("我手边还没有可播放的音乐。");
        return;
      }
      void playMusicQueueIndex(queueIndex, {
        message: `播放：《${musicQueue[queueIndex].displayName}》。`
      });
    }
  } else if (action === "stop") {
    if (!musicTrack) {
      notifyMusicActivityUnavailable("现在没有正在播放的音乐。");
      return;
    }
    stopMusic({ announce: false });
  } else {
    return;
  }

  updateActivityControls();
  scheduleSettingsSnapshot();
}

function applyPayloadFileDeliveries(payload) {
  const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
  for (const event of events) {
    const type = String(event?.type || "").trim().toLowerCase();
    if (type === "file_ready" || type === "generated_file_ready") {
      void handleDesktopFileDeliveryEvent(event);
    }
  }
}

function applyPayloadBrowserEvents(payload) {
  const events = Array.isArray(payload?.tool_events) ? payload.tool_events : [];
  for (const event of events) {
    const type = String(event?.type || "").trim().toLowerCase();
    if (type === "browser_open_requested") {
      void handleBrowserOpenEvent(event);
    }
  }
}

const browserOpenHandled = new Set();

async function handleBrowserOpenEvent(event) {
  if (!event || typeof event !== "object") return;
  const url = normalizePublicBrowserUrl(event.url);
  if (!url) {
    setRuntimeStatus("浏览器打开请求被拦截：网址不安全", { mode: "error" });
    showBubbleText("这个网址看起来不适合直接打开，我先拦住了。", {
      transient: true,
      durationMs: 2600,
      kind: "error"
    });
    return;
  }
  const key = `browser:${url}`;
  if (browserOpenHandled.has(key)) return;
  browserOpenHandled.add(key);
  const label = String(event.label || event.title || "").trim() || url;
  const result = await tauriCall("open_external_url", { url }, { quiet: true });
  if (result !== null) {
    setRuntimeStatus(`已打开网页：${label}`, { mode: "idle" });
  } else {
    setRuntimeStatus("打开网页失败", { mode: "error" });
    showBubbleText("网页没有打开成功，可能是桌面端暂时接不上系统浏览器。", {
      transient: true,
      durationMs: 2600,
      kind: "error"
    });
  }
}

function normalizePublicBrowserUrl(value) {
  const raw = String(value || "").trim();
  if (!raw || raw.length > 1600 || /\s|[\u0000-\u001f]/.test(raw)) return "";
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return "";
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return "";
  if (parsed.username || parsed.password) return "";
  const host = parsed.hostname.toLowerCase();
  if (!host || host === "localhost" || host.endsWith(".local")) return "";
  if (/^(127\.|10\.|0\.|169\.254\.|192\.168\.)/.test(host)) return "";
  const private172 = host.match(/^172\.(\d+)\./);
  if (private172 && Number(private172[1]) >= 16 && Number(private172[1]) <= 31) return "";
  if (host === "::1" || host.startsWith("fe80:") || host.startsWith("fc") || host.startsWith("fd")) return "";
  return parsed.href;
}

async function handleDesktopFileDeliveryEvent(event) {
  if (!event || typeof event !== "object" || !event.send_to_user) return;
  const fileRef = resolveDesktopFileDeliveryRef(event);
  if (!fileRef) return;
  const action = normalizeDesktopDeliveryAction(
    event.delivery_action || event.desktop_delivery?.action || event.handoff_action
  );
  const key = desktopFileDeliveryEventKey(event, fileRef, action);
  if (!key || desktopFileDeliveryHandled.has(key)) return;
  desktopFileDeliveryHandled.add(key);

  await notifyWorkspaceRefresh();
  if (!action) {
    setRuntimeStatus(`文件已放到手边：${fileRef.name || fileRef.handle || "成果"}`, { mode: "idle" });
    return;
  }

  let filePath = String(event.desktop_delivery?.path || fileRef.path || "").trim();
  if (!filePath && fileRef.handle) {
    try {
      filePath = await fetchWorkspaceItemLocation({
        itemType: fileRef.itemType,
        handle: fileRef.handle
      });
    } catch {
      filePath = "";
    }
  }
  if (!filePath) {
    setRuntimeStatus("文件已生成，但暂时找不到本地路径", { mode: "error" });
    showBubbleText("文件做好了，但本地位置暂时没摸到。", { transient: true, durationMs: 2400, kind: "error" });
    return;
  }

  const displayName = fileRef.name || fileRef.handle || "文件";
  if (action === "open") {
    const result = await tauriCall("open_local_file", { path: filePath }, { quiet: true });
    announceDesktopFileDeliveryResult(result !== null, `已打开：${displayName}`, "文件做好了，我打开给你看啦。", "打开文件失败了。");
  } else if (action === "reveal") {
    const result = await tauriCall("show_item_in_folder", { path: filePath }, { quiet: true });
    announceDesktopFileDeliveryResult(result !== null, `已定位：${displayName}`, "文件位置打开啦。", "打开文件位置失败了。");
  } else if (action === "save_desktop") {
    const result = await tauriCall(
      "export_file_to_desktop",
      { path: filePath, fileName: buildDesktopDeliveryFileName(fileRef) },
      { quiet: true }
    );
    const exportedPath = String(result?.path || "").trim();
    announceDesktopFileDeliveryResult(
      result !== null,
      exportedPath ? `已保存到桌面：${exportedPath}` : `已保存到桌面：${displayName}`,
      "文件已经放到桌面 Akane Outputs 里啦。",
      "保存到桌面失败了。"
    );
  } else if (action === "copy_path") {
    try {
      await navigator.clipboard.writeText(filePath);
      announceDesktopFileDeliveryResult(true, "文件路径已复制", "文件路径复制好了。", "");
    } catch {
      announceDesktopFileDeliveryResult(false, "", "", "复制文件路径失败了。");
    }
  }
}

function resolveDesktopFileDeliveryRef(event) {
  const type = String(event?.type || "").trim().toLowerCase();
  const raw = type === "generated_file_ready"
    ? event.generated_file
    : event.file || event.generated_file;
  if (!raw || typeof raw !== "object") return null;
  const sourceType = String(raw.source_type || (type === "generated_file_ready" ? "generated" : "")).trim().toLowerCase();
  const handle = String(raw.handle || raw.generated_handle || raw.attachment_handle || "").trim();
  return {
    itemType: sourceType === "generated" || raw.generated_id || raw.generated_handle ? "generated" : "attachment",
    handle,
    path: String(raw.absolute_path || raw.path || raw.file_path || "").trim(),
    name: String(raw.name || raw.output_title || raw.title || raw.origin_name || handle || "").trim(),
    format: String(raw.file_ext || raw.output_format || raw.format || "").trim().replace(/^\.+/, "")
  };
}

function desktopFileDeliveryEventKey(event, fileRef, action) {
  const id = String(
    fileRef.path ||
      event?.file?.source_id ||
      event?.generated_file?.generated_id ||
      fileRef.handle ||
      ""
  ).trim();
  return id ? `${String(event?.type || "")}:${action || "workspace"}:${id}` : "";
}

function normalizeDesktopDeliveryAction(value) {
  const text = String(value || "").trim().toLowerCase().replace(/-/g, "_");
  if (["open", "open_file"].includes(text)) return "open";
  if (["reveal", "show", "show_in_folder", "show_folder", "folder", "location"].includes(text)) return "reveal";
  if (["save_desktop", "export_desktop", "save_to_desktop", "desktop"].includes(text)) return "save_desktop";
  if (["copy_path", "path", "clipboard"].includes(text)) return "copy_path";
  return "";
}

function buildDesktopDeliveryFileName(fileRef) {
  const name = String(fileRef?.name || fileRef?.handle || "akane-output").trim() || "akane-output";
  const format = String(fileRef?.format || "").trim().replace(/^\.+/, "");
  if (!format || name.toLowerCase().endsWith(`.${format.toLowerCase()}`)) return name;
  return `${name}.${format}`;
}

function announceDesktopFileDeliveryResult(ok, statusText, bubbleText, errorText) {
  if (ok) {
    setRuntimeStatus(statusText, { mode: "idle" });
    if (bubbleText) showBubbleText(bubbleText, { transient: true, durationMs: 2600, kind: "status" });
  } else {
    setRuntimeStatus(errorText || "文件交付失败", { mode: "error" });
    if (errorText) showBubbleText(errorText, { transient: true, durationMs: 2600, kind: "error" });
  }
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
  const capabilities = [...BASE_CAPABILITIES, AUDIO_PLAYBACK_CAPABILITY];
  if (state.desktopContextEnabled) capabilities.push("desktop_context");
  if (state.screenVisionEnabled && state.screenVisionMode === "summary") capabilities.push("screen_vision");
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

function showToolWorking(event, { hasShownReply = false } = {}) {
  // Backend emits assistant_working ("我查一下。") right before a tool runs, so
  // the pet can show it's actively working instead of looking frozen on
  // "thinking" through a multi-second tool call (web search, memory, …).
  const message = String(event?.message || "").trim() || "我查一下。";
  setPetMotion("thinking");
  setRuntimeStatus("查一下…", { mode: "thinking" });
  if (hasShownReply) {
    // The model already spoke a pre-tool line; don't clobber that expressive
    // bubble (INV-1) — the motion + status above are enough of a working hint.
    return;
  }
  // Nothing shown yet (e.g. native tool with empty pre-speech): surface the
  // working line as a thinking-kind bubble. It is replaced by the real reply
  // and, if the turn ends with nothing rendered, cleared by the stream_end
  // leftover-thinking guard.
  setPetEmotion("thinking");
  showBubbleText(message, { transient: false, kind: "thinking" });
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
    displayReplyBubbleText(text, { speaking });
    index += 1;

    if (index < items.length) {
      segmentTimer = window.setTimeout(showNext, getSegmentDisplayDelay(text));
      return;
    }

    scheduleBubbleReset(Math.max(text.length, 4), token);
  };

  showNext();
}

function displayReplyBubbleText(text, { speaking = true } = {}) {
  bubbleKind = "reply";
  replyDisplayActive = true;
  setBubbleContent(text);
  els.bubble.classList.add("visible");
  scheduleNativeHitTestSync({ force: true });
  if (speaking) setPetMotion("speaking");
  updateActivityControls();
}

function getSegmentDisplayDelay(text) {
  return Math.max(SEGMENT_MIN_MS, Math.min(SEGMENT_MAX_MS, String(text || "").length * SEGMENT_CHAR_RATE));
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
  restoreMotionAfterBubble();
  scheduleMusicEmotionRestore();
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
  visualRenderer.setMotion(next, { restart: next === "click" });
  if (durationMs > 0) {
    motionTimer = window.setTimeout(() => {
      if (sending) return;
      visualRenderer.setMotion(physicsTimer ? "thrown" : "idle");
    }, durationMs);
  }
}

function restoreMotionAfterBubble() {
  if (dragState) {
    visualRenderer.setMotion("dragging");
    return;
  }
  if (physicsTimer) {
    visualRenderer.setMotion("thrown");
    return;
  }
  visualRenderer.setMotion(ttsActive ? "speaking" : "idle");
}

function showFileDropHint() {
  if (musicDropHover) return;
  musicDropHover = true;
  setRuntimeStatus(`把文件拖给 ${getProfileIdentityText("name", CHARACTER_NAME)}，会先放进手边工作台`, { mode: "idle" });
  if (!sending && !replyDisplayActive) {
    showBubbleText("递给我就行。", { transient: true, durationMs: 1400, kind: "status" });
  }
}

async function handleDroppedFiles(paths) {
  const files = Array.isArray(paths) ? paths.map((item) => String(item || "")).filter(Boolean) : [];
  if (!files.length) return;
  const items = buildDroppedAudioItems(files);

  if (items.length) {
    runDroppedWorkspaceImportInBackground(files, { audioCount: items.length, totalCount: files.length });
    setRuntimeStatus(items.length > 1 ? `收到 ${items.length} 首音乐，正在准备播放` : "音乐收到，正在准备播放", {
      mode: "music"
    });
    if (!sending && !replyDisplayActive) {
      showBubbleText(files.length > items.length ? "音乐先放起来，其他文件我后台收进手边。" : "音乐先放起来，手边我后台整理。", {
        transient: true,
        durationMs: 1800,
        kind: "music"
      });
    }
    await yieldToUiForDrop();
    await addDroppedAudioFiles(items);
    return;
  }

  try {
    const importResult = await importDroppedFilesToWorkspace(files);
    const imported = Number(importResult?.imported || 0);
    if (imported > 0) {
      const skipped = Number(importResult?.skipped_count || 0);
      setRuntimeStatus(
        skipped > 0 ? `已放进手边：${imported} 个文件，跳过 ${skipped} 个` : `已放进手边：${imported} 个文件`,
        { mode: "idle" }
      );
      showBubbleText(imported > 1 ? `收到 ${imported} 个文件，放到手边了。` : "文件收到啦，放到手边了。", {
        transient: true,
        durationMs: 2400,
        kind: "status"
      });
      return;
    }

    showBubbleText("这个文件暂时还不能放进手边。", {
      transient: true,
      durationMs: 2400,
      kind: "status"
    });
    setRuntimeStatus("拖入的文件不是当前支持的类型", { mode: "error" });
  } catch (error) {
    const importError = friendlyErrorMessage(formatError(error));
    showBubbleText(importError || "这个文件暂时还不能放进手边。", {
      transient: true,
      durationMs: 2400,
      kind: "status"
    });
    setRuntimeStatus(importError || "拖入的文件不是当前支持的类型", { mode: "error" });
  }
}

function runDroppedWorkspaceImportInBackground(files, { audioCount = 0, totalCount = 0 } = {}) {
  const task = importDroppedFilesToWorkspace(files);
  void task.then((importResult) => {
    const imported = Number(importResult?.imported || 0);
    if (!imported) return;
    const skipped = Number(importResult?.skipped_count || 0);
    const hasExtraFiles = Number(totalCount || 0) > Number(audioCount || 0);
    const suffix = skipped > 0 ? `，跳过 ${skipped} 个` : "";
    setRuntimeStatus(
      hasExtraFiles
        ? `音乐播放中，另外 ${imported} 个文件已放进手边${suffix}`
        : `音乐播放中，文件已后台放进手边${suffix}`,
      { mode: "music" }
    );
  }).catch((error) => {
    setRuntimeStatus(`手边后台导入失败，音乐播放不受影响：${friendlyErrorMessage(formatError(error))}`, { mode: "music" });
  });
}

function yieldToUiForDrop() {
  return new Promise((resolve) => {
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(() => window.setTimeout(resolve, 0));
      return;
    }
    window.setTimeout(resolve, 0);
  });
}

async function importDroppedFilesToWorkspace(paths) {
  const normalizedPaths = Array.isArray(paths)
    ? paths.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!normalizedPaths.length) return null;
  if (workspaceImporting) return null;
  workspaceImporting = true;
  try {
    const sessionId = state.sessionId || generateSessionId();
    if (!state.sessionId) {
      state.sessionId = sessionId;
      scheduleSave(0);
      scheduleSettingsSnapshot();
      void ensureBackendSession();
    }
    const response = await backendFetch(
      buildBackendEndpointUrl("desktop_workspace_import_local", "/desktop-pet/workspace/import-local", { t: Date.now() }),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          user_id: sessionId,
          session_id: sessionId,
          real_user_id: getProfileUserId(),
          ...buildBackendCharacterContext(),
          paths: normalizedPaths,
          recursive: false,
          max_files: 40
        }),
        connectTimeout: 60_000
      }
    );
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(extractBackendErrorMessage(payload) || `手边导入失败：HTTP ${response.status}`);
    }
    if (!payload?.ok && !Number(payload?.imported || 0)) {
      throw new Error(extractBackendErrorMessage(payload) || summarizeWorkspaceImportSkipped(payload) || "没有可导入的文件");
    }
    await notifyWorkspaceRefresh();
    return payload;
  } finally {
    workspaceImporting = false;
  }
}

function buildWorkspaceAudioSourceId(itemType, handle) {
  const normalizedType = itemType === "generated" ? "generated" : "attachment";
  const normalizedHandle = String(handle || "").trim();
  return normalizedHandle ? `workspace:${normalizedType}:${normalizedHandle}` : "";
}

function findMusicQueueIndexByWorkspaceAudio(itemType, handle) {
  const sourceId = buildWorkspaceAudioSourceId(itemType, handle);
  if (!sourceId) return -1;
  return musicQueue.findIndex((track) =>
    track?.sourceId === sourceId ||
    track?.queueDedupeKey === sourceId ||
    (
      String(track?.workspaceItemType || "") === (itemType === "generated" ? "generated" : "attachment") &&
      String(track?.workspaceHandle || "") === String(handle || "").trim()
    )
  );
}

async function playWorkspaceAudioItem(item) {
  const value = item && typeof item === "object" ? item : {};
  const itemType = value.itemType === "generated" ? "generated" : "attachment";
  const handle = String(value.handle || "").trim();
  const title = String(value.title || "").trim();
  if (!handle) {
    showBubbleText("这首没有可播放的编号。", { transient: true, durationMs: 1800, kind: "music" });
    return;
  }

  const existingIndex = findMusicQueueIndexByWorkspaceAudio(itemType, handle);
  if (existingIndex >= 0) {
    await playMusicQueueIndex(existingIndex, { message: `播放《${musicQueue[existingIndex]?.displayName || title}》。` });
    scheduleSettingsSnapshot();
    return;
  }

  try {
    setRuntimeStatus("正在从手边取音乐", { mode: "music" });
    const path = await fetchWorkspaceItemLocation({ itemType, handle });
    const workspaceSourceId = buildWorkspaceAudioSourceId(itemType, handle);
    await addDroppedAudioFiles(
      [{ path, lyricPath: "", workspaceMetadata: { sourceId: workspaceSourceId, queueDedupeKey: workspaceSourceId, workspaceItemType: itemType, workspaceHandle: handle, displayName: title } }],
      { playSourceIdAfterAdd: workspaceSourceId, clearQueueOnError: false }
    );
  } catch (error) {
    const message = friendlyErrorMessage(formatError(error));
    setRuntimeStatus(`手边音乐播放失败：${message}`, { mode: "error" });
    showBubbleText("这首手边音乐暂时放不了。", { transient: true, durationMs: 2200, kind: "music" });
  }
}

async function fetchWorkspaceItemLocation({ itemType, handle }) {
  const normalizedType = String(itemType || "").trim().toLowerCase();
  const normalizedHandle = String(handle || "").trim();
  if (!normalizedHandle) throw new Error("missing workspace handle");
  const sessionId = state.sessionId || "";
  if (!sessionId) throw new Error("会话还没准备好");

  const routeType = normalizedType === "generated" || normalizedType === "output" ? "generated" : "attachments";
  const endpointName = routeType === "generated" ? "desktop_workspace_generated_location" : "desktop_workspace_attachment_location";
  const response = await backendFetch(
    buildBackendEndpointUrl(
      endpointName,
      `/desktop-pet/workspace/${routeType}/${encodeURIComponent(normalizedHandle)}/location`,
      {
        user_id: sessionId,
        real_user_id: getProfileUserId(),
        ...buildBackendCharacterContext(),
        t: Date.now()
      }
    ),
    {
      method: "GET",
      cache: "no-store",
      connectTimeout: 10000
    }
  );
  const payload = await readJsonResponse(response);
  if (!response.ok || !payload?.ok || !payload.path) {
    throw new Error(extractBackendErrorMessage(payload) || `HTTP ${response.status}`);
  }
  return String(payload.path || "");
}

function summarizeWorkspaceImportSkipped(payload) {
  const first = Array.isArray(payload?.skipped) ? payload.skipped[0] : null;
  const reason = String(first?.reason || "").trim();
  if (!reason) return "";
  const labels = {
    unsupported_type: "这个文件类型暂时还不支持",
    empty_file: "文件是空的",
    file_too_large: "文件太大了",
    not_found: "没有找到这个路径",
    directory_scan_limit: "这个文件夹太大了，先挑具体文件给我",
    max_files_reached: "一次给的文件有点多，已经达到上限"
  };
  return labels[reason] || "文件暂时不能导入手边";
}

async function notifyWorkspaceRefresh() {
  if (!isTauriRuntime) return;
  try {
    await emit(WORKSPACE_REFRESH_EVENT, { t: Date.now() });
  } catch {
    // The workspace window may not be open yet.
  }
  scheduleWorkspaceTaskWatch({ immediate: true });
  scheduleWorkspaceMusicRecommendationsRefresh();
}

function scheduleWorkspaceTaskWatch({ immediate = false, delayMs = null } = {}) {
  if (!isTauriRuntime) return;
  window.clearTimeout(workspaceTaskPollTimer);
  const delay = immediate ? 0 : Number.isFinite(Number(delayMs)) ? Number(delayMs) : WORKSPACE_TASK_POLL_MS;
  workspaceTaskPollTimer = window.setTimeout(() => {
    workspaceTaskPollTimer = 0;
    void refreshWorkspaceTaskWatch();
  }, Math.max(0, delay));
}

async function refreshWorkspaceTaskWatch() {
  if (!isTauriRuntime) return;
  const sessionId = String(state.sessionId || "").trim();
  if (!sessionId || resourceState.health !== "online") {
    scheduleWorkspaceTaskWatch({ delayMs: WORKSPACE_TASK_IDLE_POLL_MS });
    return;
  }
  const watchKey = `${state.profileUserId || PROFILE_USER_ID}|${sessionId}`;
  if (workspaceTaskWatchKey !== watchKey) {
    workspaceTaskWatchKey = watchKey;
    workspaceTaskWatchPrimed = false;
    workspaceTaskStatusCache = new Map();
    workspaceTaskAnnounced.clear();
  }

  let hasActiveTasks = false;
  try {
    const query = {
      user_id: sessionId,
      real_user_id: getProfileUserId(),
      limit: 12,
      t: Date.now()
    };
    const response = await backendFetch(
      buildBackendEndpointUrl("desktop_workspace_summary", "/desktop-pet/workspace/summary", query),
      {
        method: "GET",
        cache: "no-store",
        connectTimeout: 5000
      }
    );
    const payload = await readJsonResponse(response);
    if (!response.ok) throw new Error(extractBackendErrorMessage(payload) || `HTTP ${response.status}`);
    const tasks = normalizeWorkspaceSummaryTasks(payload);
    hasActiveTasks = tasks.some(isActiveWorkspaceTask);
    announceWorkspaceTaskChanges(tasks);
  } catch {
    scheduleWorkspaceTaskWatch({ delayMs: WORKSPACE_TASK_IDLE_POLL_MS });
    return;
  }
  scheduleWorkspaceTaskWatch({ delayMs: hasActiveTasks ? WORKSPACE_TASK_POLL_MS : WORKSPACE_TASK_IDLE_POLL_MS });
}

function normalizeWorkspaceSummaryTasks(payload) {
  const sections = payload?.sections && typeof payload.sections === "object" ? payload.sections : {};
  return (Array.isArray(sections.tasks) ? sections.tasks : [])
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      id: String(item.id || item.handle || "").trim(),
      title: String(item.title || "后台任务").trim(),
      status: String(item.status || "").trim().toLowerCase(),
      statusGroup: String(item.status_group || item.statusGroup || "").trim().toLowerCase(),
      updatedAt: Number(item.updated_at || item.updatedAt || 0),
      artifactCount: Number(item.artifact_count || item.artifactCount || 0)
    }))
    .filter((item) => item.id);
}

function isActiveWorkspaceTask(task) {
  return task.statusGroup === "active" || ["queued", "running"].includes(task.status);
}

function announceWorkspaceTaskChanges(tasks) {
  const nextCache = new Map();
  for (const task of tasks) {
    const signature = `${task.status}:${task.updatedAt}:${task.artifactCount}`;
    nextCache.set(task.id, { status: task.status, signature });
    const previous = workspaceTaskStatusCache.get(task.id);
    if (shouldAnnounceWorkspaceTask(task, previous)) {
      announceWorkspaceTask(task);
      workspaceTaskAnnounced.add(workspaceTaskAnnouncementKey(task));
    }
  }
  workspaceTaskStatusCache = nextCache;
  workspaceTaskWatchPrimed = true;
}

function shouldAnnounceWorkspaceTask(task, previous) {
  if (!["completed", "failed", "blocked", "waiting_user", "partial"].includes(task.status)) return false;
  const key = workspaceTaskAnnouncementKey(task);
  if (workspaceTaskAnnounced.has(key)) return false;
  if (previous && previous.status !== task.status) return true;
  const updatedAtMs = Number(task.updatedAt || 0) * 1000;
  return workspaceTaskWatchPrimed && updatedAtMs > 0 && Date.now() - updatedAtMs < WORKSPACE_TASK_RECENT_UPDATE_MS;
}

function workspaceTaskAnnouncementKey(task) {
  return `${task.id}:${task.status}:${task.updatedAt || 0}`;
}

function announceWorkspaceTask(task) {
  const title = task.title ? `：${task.title}` : "";
  let message = `后台任务有新状态${title}`;
  let bubble = "后台任务有新进展。";
  let mode = "idle";
  if (task.status === "completed") {
    message = `后台任务已完成${title}`;
    bubble = task.artifactCount > 0 ? "我处理好了，结果放到手边了。" : "我处理好了。";
  } else if (task.status === "failed") {
    message = `后台任务失败${title}`;
    bubble = "后台任务失败了，我把状态放在手边了。";
    mode = "error";
  } else if (["blocked", "waiting_user", "partial"].includes(task.status)) {
    message = `后台任务需要确认${title}`;
    bubble = "后台任务等你确认一下。";
  }
  setRuntimeStatus(message, { mode });
  if (!sending && !replyDisplayActive) {
    showBubbleText(bubble, { transient: true, durationMs: 2600, kind: "status" });
  }
  void emit(WORKSPACE_REFRESH_EVENT, { t: Date.now() }).catch(() => {});
}

function isSupportedMusicPath(path) {
  const extension = String(path || "").split(/[\\/]/u).pop()?.split(".").pop()?.toLowerCase() || "";
  return MUSIC_FILE_EXTENSIONS.has(extension);
}

function isSupportedLyricPath(path) {
  const extension = String(path || "").split(/[\\/]/u).pop()?.split(".").pop()?.toLowerCase() || "";
  return MUSIC_LYRIC_EXTENSIONS.has(extension);
}

function buildDroppedAudioItems(files) {
  const lyricMap = new Map();
  for (const path of files.filter(isSupportedLyricPath)) {
    const key = pathStemKey(path);
    if (key && !lyricMap.has(key)) lyricMap.set(key, path);
  }
  return files
    .filter(isSupportedMusicPath)
    .map((path) => ({
      path,
      lyricPath: lyricMap.get(pathStemKey(path)) || ""
    }));
}

function pathStemKey(path) {
  const name = String(path || "").split(/[\\/]/u).pop() || "";
  const dotIndex = name.lastIndexOf(".");
  return (dotIndex > 0 ? name.slice(0, dotIndex) : name).trim().toLowerCase();
}

async function addDroppedAudioFiles(items, options = {}) {
  if (!isTauriRuntime) return;
  const audioItems = Array.isArray(items)
    ? items
        .map((item) => {
          if (typeof item === "string") return { path: item, lyricPath: "" };
          return {
            path: String(item?.path || ""),
            lyricPath: String(item?.lyricPath || ""),
            workspaceMetadata: item?.workspaceMetadata || null
          };
        })
        .filter((item) => item.path)
    : [];
  if (!audioItems.length) return;
  musicLoading = true;
  updateActivityControls();
  scheduleSettingsSnapshot();
  setRuntimeStatus(audioItems.length > 1 ? `正在准备 ${audioItems.length} 首音乐` : "正在准备音乐", { mode: "music" });
  try {
    const tracks = [];
    const errors = [];
    for (const item of audioItems) {
      try {
        const asset = await invoke("prepare_audio_asset", {
          path: item.path,
          lyricPath: item.lyricPath || null
        });
        tracks.push(normalizeMusicTrack(asset, item.workspaceMetadata || undefined));
      } catch (error) {
        errors.push(formatError(error));
      }
    }
    if (!tracks.length) {
      throw new Error(errors[0] || "没有可播放的音频文件");
    }
    await enqueueMusicTracks(tracks, options);
  } catch (error) {
    if (options.clearQueueOnError !== false) {
      stopMusic({ silent: true, clearQueue: true });
    }
    setRuntimeStatus(`音乐准备失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    showBubbleText("这首好像暂时放不了。", { transient: true, durationMs: 2200, kind: "music" });
  } finally {
    musicLoading = false;
    updateActivityControls();
    scheduleSettingsSnapshot();
  }
}

async function enqueueMusicTracks(tracks, options = {}) {
  const items = Array.isArray(tracks) ? tracks.filter((track) => track?.cachedPath) : [];
  if (!items.length) return;

  const playSourceId = options.playSourceIdAfterAdd ? String(options.playSourceIdAfterAdd).trim() : "";
  const findTargetIndex = () => playSourceId
    ? musicQueue.findIndex((track) => track.sourceId === playSourceId || track.queueDedupeKey === playSourceId)
    : -1;

  const uniqueItems = items.filter((track) => {
    const key = track.queueDedupeKey || track.sourceId || "";
    return !key || !musicQueue.some((existing) => existing.queueDedupeKey === key || existing.sourceId === key);
  });

  if (!uniqueItems.length) {
    const existingIndex = findTargetIndex();
    if (existingIndex >= 0) {
      await playMusicQueueIndex(existingIndex);
      scheduleSettingsSnapshot();
    }
    return;
  }

  const shouldStart = !musicTrack || musicQueueIndex < 0 || !musicQueue.length || (!musicPlaying && !musicPaused);

  if (shouldStart && !playSourceId) {
    musicQueue = uniqueItems;
    musicQueueIndex = 0;
    await playMusicQueueIndex(0, {
      message: uniqueItems.length > 1 ? `收到，先放《${uniqueItems[0].displayName}》。` : `收到，放《${uniqueItems[0].displayName}》。`
    });
    return;
  }

  const startIndex = musicQueue.length;
  musicQueue.push(...uniqueItems);

  if (playSourceId) {
    const targetIndex = findTargetIndex();
    if (targetIndex >= 0) {
      await playMusicQueueIndex(targetIndex);
      scheduleSettingsSnapshot();
      return;
    }
    if (shouldStart) {
      await playMusicQueueIndex(startIndex);
      scheduleSettingsSnapshot();
      return;
    }
  }

  const text = uniqueItems.length > 1 ? `已加入 ${uniqueItems.length} 首，队列现在 ${musicQueue.length} 首。` : `已加入队列：《${uniqueItems[0].displayName}》。`;
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
  scheduleBackendMusicTimeline(musicTrack, { immediate: true });
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

async function playMusicTrackBySourceId(sourceId) {
  const index = findMusicTrackIndexBySourceId(sourceId);
  if (index < 0) {
    showBubbleText("这首不在当前队列里。", { transient: true, durationMs: 1800, kind: "music" });
    return false;
  }
  if (index === musicQueueIndex) {
    if (!musicPlaying) await toggleMusicPlayback();
    return true;
  }
  return playMusicQueueIndex(index, {
    message: `切到这首：《${musicQueue[index].displayName}》。`
  });
}

async function removeMusicTrackBySourceId(sourceId) {
  const index = findMusicTrackIndexBySourceId(sourceId);
  if (index < 0) {
    showBubbleText("队列里找不到这首啦。", { transient: true, durationMs: 1800, kind: "music" });
    return false;
  }

  const removed = musicQueue[index];
  const wasCurrent = index === musicQueueIndex;
  musicQueue.splice(index, 1);

  if (!musicQueue.length) {
    stopMusic({ announce: true, silent: false, clearQueue: true });
    return true;
  }

  if (wasCurrent) {
    const nextIndex = Math.min(index, musicQueue.length - 1);
    await playMusicQueueIndex(nextIndex, {
      message: `已移除《${removed.displayName}》，接着放《${musicQueue[nextIndex].displayName}》。`
    });
    return true;
  }

  if (index < musicQueueIndex) musicQueueIndex -= 1;
  const text = `已从队列移除：《${removed.displayName}》。`;
  setRuntimeStatus(text, { mode: musicPlaying ? "music" : "music-paused" });
  showBubbleText(text, { transient: true, durationMs: 2000, kind: "music" });
  updateActivityControls();
  scheduleSettingsSnapshot();
  return true;
}

async function handleMusicEnded() {
  if (!musicTrack) return;
  if (state.musicPlayMode === "单曲循环" && musicQueueIndex >= 0) {
    await playMusicQueueIndex(musicQueueIndex, {
      message: `单曲循环：《${musicTrack.displayName}》。`
    });
    return;
  }
  if (state.musicPlayMode === "随机播放" && musicQueue.length > 1) {
    let nextIndex = musicQueueIndex;
    while (nextIndex === musicQueueIndex) {
      nextIndex = Math.floor(Math.random() * musicQueue.length);
    }
    await playMusicQueueIndex(nextIndex, {
      message: `随机播放：《${musicQueue[nextIndex].displayName}》。`
    });
    return;
  }
  if (await playNextMusicTrack({ auto: true })) return;
  if (state.musicPlayMode === "列表循环" && musicQueue.length > 1) {
    await playMusicQueueIndex(0, {
      message: `列表循环：《${musicQueue[0].displayName}》。`
    });
    return;
  }
  stopMusic({ ended: true });
}

async function handleMusicPlaybackError() {
  if (!musicTrack) return;
  const failed = musicTrack;
  const failedIndex = musicQueueIndex;
  const name = getMusicDisplayName();
  if (musicQueue.length > 1 && failedIndex >= 0) {
    musicQueue.splice(failedIndex, 1);
    const nextIndex = Math.min(failedIndex, musicQueue.length - 1);
    try {
      await playMusicQueueIndex(nextIndex, {
        message: `《${name}》暂时放不了，先跳到《${musicQueue[nextIndex].displayName}》。`
      });
      return;
    } catch (error) {
      setRuntimeStatus(`音乐播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    }
  }
  stopMusic({ silent: true, clearQueue: true });
  setRuntimeStatus(`音乐播放失败${name ? `：${name}` : ""}`, { mode: "error" });
  showBubbleText("这首歌好像没放出来……", { transient: true, durationMs: 2200, kind: "music" });
  if (failed?.displayName) scheduleSettingsSnapshot();
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
      scheduleBackendMusicTimeline(musicTrack, { immediate: true });
    } catch (error) {
      setRuntimeStatus(`音乐继续失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
    }
  }
  updateActivityControls();
  scheduleSettingsSnapshot();
}

function seekMusicPlayback(seconds) {
  if (!musicTrack || !els.musicPlayer) {
    setRuntimeStatus("暂无可跳转的音乐", { mode: "idle" });
    return;
  }
  const duration = Number(els.musicPlayer.duration || 0);
  if (!Number.isFinite(duration) || duration <= 0) {
    setRuntimeStatus("音乐时长仍在读取中", { mode: "idle" });
    return;
  }
  const nextTime = clamp(Number(seconds || 0), 0, duration);
  try {
    els.musicPlayer.currentTime = nextTime;
    setRuntimeStatus(`已跳转音乐进度：${getMusicDisplayName()}`, {
      mode: musicPlaying ? "music" : musicPaused ? "music-paused" : "idle"
    });
    scheduleMusicSnapshot(80);
  } catch {
    setRuntimeStatus("音乐进度跳转失败", { mode: "error" });
  }
}

function stopMusic({ announce = false, ended = false, silent = false, clearQueue = false } = {}) {
  const hadTrack = Boolean(musicTrack);
  const name = getMusicDisplayName();
  musicPlaying = false;
  musicPaused = false;
  musicLoading = false;
  clearBackendMusicTimelineTimer();
  if (els.musicPlayer) {
    els.musicPlayer.pause();
    try {
      els.musicPlayer.currentTime = 0;
    } catch {
      // Some media backends reject currentTime until metadata is ready.
    }
    if (clearQueue) {
      resetMusicElement();
    }
  }
  if (clearQueue) {
    musicTrack = null;
    musicQueue = [];
    musicQueueIndex = -1;
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

function clearMusicQueue({ announce = false } = {}) {
  if (!musicTrack && !musicQueue.length) {
    showBubbleText("队列现在是空的。", { transient: true, durationMs: 1600, kind: "music" });
    return;
  }
  stopMusic({ announce, clearQueue: true });
}

function setMusicEmotion(active) {
  window.clearTimeout(musicEmotionRestoreTimer);
  musicEmotionRestoreTimer = 0;
  if (active) {
    musicEmotionActive = true;
    scheduleMusicEmotionRestore({ delayMs: 0 });
    return;
  }
  if (isMusicEmotionSourceActive()) {
    musicEmotionActive = true;
    scheduleMusicEmotionRestore();
    return;
  }
  if (musicEmotionActive && state.currentEmotion === resolveEmotionEntry(getProfileMusicEmotion()).id) {
    setPetEmotion(getProfileDefaultEmotion(), { persist: false });
  }
  musicEmotionActive = false;
}

function scheduleMusicEmotionRestore({ delayMs = MUSIC_EMOTION_RESTORE_DELAY_MS } = {}) {
  window.clearTimeout(musicEmotionRestoreTimer);
  musicEmotionRestoreTimer = 0;
  if (!isMusicEmotionSourceActive()) return;
  musicEmotionActive = true;
  const delay = Math.max(0, Number(delayMs) || 0);
  musicEmotionRestoreTimer = window.setTimeout(() => {
    musicEmotionRestoreTimer = 0;
    applyMusicEmotionWhenIdle();
  }, delay);
}

function applyMusicEmotionWhenIdle() {
  if (!isMusicEmotionSourceActive()) return;
  if (!canApplyMusicEmotionNow()) {
    scheduleMusicEmotionRestore();
    return;
  }
  setPetEmotion(getProfileMusicEmotion(), { persist: false });
}

function canApplyMusicEmotionNow() {
  if (sending || ttsActive || ttsQueue.length > 0) return false;
  if (voiceInputState === "recording" || voiceInputState === "processing") return false;
  if (dragState || physicsTimer || playState.heldEmotion) return false;
  if (localInteractionActive || previewEmotionRestore) return false;
  if (!els.chatForm.hidden || !els.menu.hidden) return false;
  return true;
}

function setRestingPetEmotion({ persist = false } = {}) {
  return setPetEmotion(getRestingPetEmotion(), { persist });
}

function getRestingPetEmotion() {
  return isMusicEmotionSourceActive() ? getProfileMusicEmotion() : getProfileDefaultEmotion();
}

function normalizeMusicTrack(asset, metadata) {
  const value = asset && typeof asset === "object" ? asset : {};
  const fileName = String(value.fileName || "audio");
  const cachedPath = String(value.cachedPath || "");
  const lyricFileName = String(value.lyricFileName || "").trim();
  const lyrics = parseLrcText(value.lyricText || "");
  const sizeBytes = Number(value.sizeBytes || value.size_bytes || 0);
  let metaSourceId = "", metaDisplayName = "", metaQueueDedupeKey = "", metaWorkspaceItemType = "", metaWorkspaceHandle = "";
  if (metadata && typeof metadata === "object") {
    metaSourceId = String(metadata.sourceId || "").trim();
    metaDisplayName = String(metadata.displayName || "").trim();
    metaQueueDedupeKey = String(metadata.queueDedupeKey || "").trim();
    metaWorkspaceItemType = metadata.workspaceItemType === "generated" ? "generated" : metadata.workspaceItemType === "attachment" ? "attachment" : "";
    metaWorkspaceHandle = String(metadata.workspaceHandle || "").trim();
  }
  return {
    originalPath: String(value.originalPath || ""),
    cachedPath,
    sourceId: metaSourceId || `local:${fileName}:${simpleHash(cachedPath || fileName)}`,
    queueDedupeKey: metaQueueDedupeKey || metaSourceId || "",
    workspaceItemType: metaWorkspaceItemType || "",
    workspaceHandle: metaWorkspaceHandle || "",
    fileName,
    displayName: metaDisplayName || String(value.displayName || value.fileName || "未命名音乐"),
    extension: String(value.extension || "").toLowerCase(),
    sizeBytes,
    lyricFileName,
    lyricLineCount: lyrics.length,
    lyrics,
    backendAttachment: null,
    timeline: null,
    timelineStatus: lyrics.length ? "skipped_lrc" : "idle",
    timelineQuality: "",
    timelineError: "",
    timelineLyrics: [],
    timelineLyricLineCount: 0,
    timelineUpdatedAt: 0,
    timelineLoading: false
  };
}

function shouldUseBackendMusicTimeline(track) {
  if (!isTauriRuntime || !track?.cachedPath) return false;
  return !hasLocalMusicLyrics(track);
}

function hasLocalMusicLyrics(track) {
  return Number(track?.lyricLineCount || 0) > 0 || (Array.isArray(track?.lyrics) && track.lyrics.length > 0);
}

function clearBackendMusicTimelineTimer() {
  window.clearTimeout(musicTimelineTimer);
  musicTimelineTimer = 0;
  musicTimelineSourceId = "";
}

function scheduleBackendMusicTimeline(track, { immediate = false, delayMs = null } = {}) {
  window.clearTimeout(musicTimelineTimer);
  musicTimelineTimer = 0;
  musicTimelineSourceId = "";
  if (!shouldUseBackendMusicTimeline(track)) return;
  if (track.timelineStatus === "ready" && Array.isArray(track.timelineLyrics) && track.timelineLyrics.length) return;

  const sourceId = String(track.sourceId || "").trim();
  if (!sourceId) return;
  musicTimelineSourceId = sourceId;
  const delay = immediate
    ? MUSIC_TIMELINE_INITIAL_DELAY_MS
    : Number.isFinite(delayMs)
      ? Math.max(1200, delayMs)
      : MUSIC_TIMELINE_POLL_MS;
  musicTimelineTimer = window.setTimeout(() => {
    musicTimelineTimer = 0;
    if (!musicTrack || musicTrack.sourceId !== sourceId || musicTimelineSourceId !== sourceId) return;
    void ensureBackendMusicTimeline(musicTrack);
  }, delay);
}

async function ensureBackendMusicTimeline(track, { force = false } = {}) {
  if (!track || musicTrack?.sourceId !== track.sourceId || !shouldUseBackendMusicTimeline(track)) return;
  if (track.timelineLoading) return;
  if (!force && track.timelineStatus === "ready" && Array.isArray(track.timelineLyrics) && track.timelineLyrics.length) return;

  track.timelineLoading = true;
  if (!track.timelineStatus || track.timelineStatus === "idle") track.timelineStatus = "uploading";
  track.timelineError = "";
  scheduleSettingsSnapshot();

  try {
    if (!track.backendAttachment?.handle) {
      track.backendAttachment = await uploadMusicTrackForTimeline(track);
      track.timelineStatus = "pending";
      scheduleSettingsSnapshot();
    }

    const result = await prepareBackendMusicTimeline(track);
    applyBackendMusicTimeline(track, result);

    if (musicTrack?.sourceId !== track.sourceId) return;
    if (track.timelineStatus === "ready") {
      setRuntimeStatus(`后端歌词线索已准备好：${track.timelineLyricLineCount || 0} 行`, {
        mode: musicPlaying ? "music" : musicPaused ? "music-paused" : null
      });
      return;
    }
    if (track.timelineStatus === "pending" || track.timelineStatus === "processing") {
      scheduleBackendMusicTimeline(track, { delayMs: MUSIC_TIMELINE_POLL_MS });
      return;
    }
    scheduleBackendMusicTimeline(track, { delayMs: MUSIC_TIMELINE_RETRY_MS });
  } catch (error) {
    if (musicTrack?.sourceId === track.sourceId) {
      track.timelineStatus = "failed";
      track.timelineError = friendlyErrorMessage(formatError(error));
      scheduleBackendMusicTimeline(track, { delayMs: MUSIC_TIMELINE_RETRY_MS });
    }
  } finally {
    track.timelineLoading = false;
    scheduleSettingsSnapshot();
  }
}

async function uploadMusicTrackForTimeline(track) {
  const assetUrl = convertFileSrc(track.cachedPath);
  const assetResponse = await window.fetch(assetUrl);
  if (!assetResponse.ok) {
    throw new Error(`读取本地音频失败：HTTP ${assetResponse.status}`);
  }

  let blob = await assetResponse.blob();
  const mimeType = blob.type || inferMusicMimeType(track);
  if (mimeType && blob.type !== mimeType) {
    blob = new Blob([blob], { type: mimeType });
  }

  const form = new FormData();
  form.append("file", blob, track.fileName || "akane_music.audio");
  form.append("user_id", state.sessionId || "desktop_pet_next");
  form.append("session_id", state.sessionId || "desktop_pet_next");
  form.append("real_user_id", state.profileUserId || PROFILE_USER_ID);
  form.append("client_mode", CLIENT_MODE);
  form.append("character_pack_id", getCurrentCharacterPackId());

  const response = await backendFetch(
    buildBackendEndpointUrl("desktop_audio_upload", "/desktop-pet/attachments/audio", { t: Date.now() }),
    {
      method: "POST",
      cache: "no-store",
      body: form,
      connectTimeout: 60_000
    }
  );
  const payload = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(extractBackendErrorMessage(payload) || `音频上传失败：HTTP ${response.status}`);
  }
  const attachment = normalizeBackendAttachment(payload?.attachment);
  if (!payload?.ok || !attachment.handle) {
    throw new Error(extractBackendErrorMessage(payload) || "后端没有返回可用音频附件");
  }
  return attachment;
}

async function prepareBackendMusicTimeline(track) {
  const activity = buildDesktopMusicActivity();
  if (!activity) throw new Error("当前没有可分析的音乐");
  const attachment = track.backendAttachment || {};
  const payload = {
    user_id: state.sessionId || "desktop_pet_next",
    session_id: state.sessionId || "desktop_pet_next",
    real_user_id: getProfileUserId(),
    ...buildBackendCharacterContext(),
    activity: {
      ...activity,
      attachment_handle: attachment.handle || activity.attachment_handle || "",
      attachment_id: attachment.attachmentId || activity.attachment_id || "",
      handle: attachment.handle || activity.handle || "current"
    }
  };
  const response = await backendFetch(
    buildBackendEndpointUrl("desktop_music_timeline_prepare", "/desktop-pet/music-timeline/prepare", { t: Date.now() }),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify(payload),
      connectTimeout: 60_000
    }
  );
  const result = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(extractBackendErrorMessage(result) || `音乐歌词线索准备失败：HTTP ${response.status}`);
  }
  if (!result?.ok) {
    throw new Error(extractBackendErrorMessage(result) || "后端暂时找不到这首音乐");
  }
  return result;
}

function applyBackendMusicTimeline(track, result) {
  const timeline = result?.timeline && typeof result.timeline === "object" ? result.timeline : null;
  if (!timeline) {
    track.timelineStatus = result?.ok ? "pending" : "failed";
    track.timelineError = extractBackendErrorMessage(result);
    return;
  }
  const status = String(timeline.status || "pending").trim().toLowerCase() || "pending";
  track.timeline = timeline;
  track.timelineStatus = status;
  track.timelineQuality = String(timeline.quality || "").trim();
  track.timelineUpdatedAt = Number(timeline.updated_at || Date.now() / 1000) || 0;
  track.timelineError = extractBackendErrorMessage(timeline) || extractBackendErrorMessage(result);
  const lines = status === "ready" ? normalizeTimelineLyricSegments(timeline.segments) : [];
  track.timelineLyrics = lines;
  track.timelineLyricLineCount = lines.length || Number(timeline.segment_count || 0) || 0;
}

function normalizeBackendAttachment(payload) {
  const value = payload && typeof payload === "object" ? payload : {};
  const handle = String(value.handle || value.attachment_handle || value.source_id || value.attachment_id || "").trim();
  return {
    attachmentId: String(value.attachment_id || value.attachmentId || "").trim(),
    handle,
    sourceId: String(value.source_id || handle).trim(),
    title: String(value.title || value.origin_name || "").trim(),
    url: String(value.url || "").trim(),
    mimeType: String(value.mime_type || value.mimeType || "").trim(),
    sizeBytes: Number(value.size_bytes || value.sizeBytes || 0)
  };
}

function normalizeTimelineLyricSegments(segments) {
  return (Array.isArray(segments) ? segments : [])
    .map((segment) => {
      const text = String(segment?.text || "").replace(/\s+/gu, " ").trim();
      if (!text) return null;
      const start = Number(segment.start ?? segment.start_seconds ?? segment.timeSeconds ?? 0);
      const end = Number(segment.end ?? segment.end_seconds ?? start);
      return {
        timeSeconds: Number.isFinite(start) ? Math.max(0, start) : 0,
        endSeconds: Number.isFinite(end) ? Math.max(0, end) : Number.isFinite(start) ? Math.max(0, start) : 0,
        text
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.timeSeconds - right.timeSeconds)
    .filter((line, index, source) => {
      const previous = source[index - 1];
      return !previous || previous.timeSeconds !== line.timeSeconds || previous.text !== line.text;
    });
}

function inferMusicMimeType(track) {
  const extension = String(track?.extension || "").toLowerCase();
  return (
    {
      mp3: "audio/mpeg",
      wav: "audio/wav",
      flac: "audio/flac",
      ogg: "audio/ogg",
      oga: "audio/ogg",
      opus: "audio/ogg",
      m4a: "audio/mp4",
      aac: "audio/aac",
      webm: "audio/webm"
    }[extension] || "application/octet-stream"
  );
}

function getMusicDisplayName() {
  return String(musicTrack?.displayName || musicTrack?.fileName || "").trim();
}

function parseLrcText(value) {
  const text = String(value || "").replace(/\r\n?/gu, "\n");
  if (!text.trim()) return [];
  const lines = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const matches = [...line.matchAll(/\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/gu)];
    if (!matches.length) continue;
    const lyricText = line.replace(/\[[^\]]+\]/gu, "").trim();
    if (!lyricText) continue;
    for (const match of matches) {
      const minutes = Number(match[1] || 0);
      const seconds = Number(match[2] || 0);
      const fraction = String(match[3] || "");
      const millis = fraction ? Number(fraction.padEnd(3, "0").slice(0, 3)) : 0;
      const timeSeconds = minutes * 60 + seconds + millis / 1000;
      if (Number.isFinite(timeSeconds)) {
        lines.push({ timeSeconds, text: lyricText });
      }
    }
  }
  return lines
    .sort((left, right) => left.timeSeconds - right.timeSeconds)
    .filter((line, index, source) => {
      const previous = source[index - 1];
      return !previous || previous.timeSeconds !== line.timeSeconds || previous.text !== line.text;
    });
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
    sizeBytes: track.sizeBytes,
    lyricFileName: track.lyricFileName || "",
    lyricLineCount: Number(track.lyricLineCount || 0),
    timelineStatus: track.timelineStatus || "",
    timelineQuality: track.timelineQuality || "",
    timelineLyricLineCount: Number(track.timelineLyricLineCount || 0),
    timelineLoading: Boolean(track.timelineLoading),
    backendAttachmentHandle: track.backendAttachment?.handle || ""
  };
}

function findMusicTrackIndexBySourceId(sourceId) {
  const normalized = String(sourceId || "").trim();
  if (!normalized) return -1;
  return musicQueue.findIndex((track) =>
    track?.sourceId === normalized || track?.queueDedupeKey === normalized
  );
}

function getSafeMusicQueueIndex() {
  if (!musicQueue.length) return -1;
  if (musicQueueIndex >= 0 && musicQueueIndex < musicQueue.length) return musicQueueIndex;
  return 0;
}

function notifyMusicActivityUnavailable(message) {
  setRuntimeStatus(message, { mode: "music" });
  showBubbleText(message, { transient: true, durationMs: 2200, kind: "music" });
}

function getProfileCareConfig() {
  const care = getActiveCharacterProfile()?.care || {};
  return {
    enabled: Boolean(care.enabled),
    initialCoins: clampCareValue(care.initialCoins, 0, 999999),
    initialHunger: clampCareValue(care.initialHunger, 0, 100),
    initialEnergy: clampCareValue(care.initialEnergy, 0, 100),
    initialAffection: clampCareValue(care.initialAffection, 0, 100),
    work: normalizeCareWorkConfig(care.work),
    allowance: normalizeCareAllowanceConfig(care.allowance),
    decay: normalizeCareDecayConfig(care.decay),
    shopItems: Array.isArray(care.shopItems) ? care.shopItems : []
  };
}

function normalizeCareState(value, config = getProfileCareConfig()) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const inventory = source.inventory && typeof source.inventory === "object" && !Array.isArray(source.inventory)
    ? source.inventory
    : {};
  const normalizedInventory = {};
  for (const [id, count] of Object.entries(inventory)) {
    const itemId = String(id || "").trim();
    const amount = Math.max(0, Math.round(Number(count) || 0));
    if (itemId && amount > 0) normalizedInventory[itemId] = amount;
  }
  return {
    enabled: Boolean(config.enabled),
    coins: clampCareValue(source.coins ?? config.initialCoins, 0, 999999),
    hunger: clampCareValue(source.hunger ?? config.initialHunger, 0, 100),
    energy: clampCareValue(source.energy ?? config.initialEnergy, 0, 100),
    affection: clampCareValue(source.affection ?? config.initialAffection, 0, 100),
    inventory: normalizedInventory,
    workTask: normalizeCareWorkTask(source.workTask || source.work_task),
    lastAllowanceAt: Math.max(0, Math.round(Number(source.lastAllowanceAt || source.last_allowance_at || 0))),
    lastDecayAt: Math.max(0, Math.round(Number(source.lastDecayAt || source.last_decay_at || 0))),
    updatedAt: Math.max(0, Math.round(Number(source.updatedAt || source.updated_at || 0)))
  };
}

function normalizeCareDecayConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    hungerPerHour: clampCareValue(
      source.hungerPerHour ?? source.hunger_per_hour ?? CARE_DEFAULT_HUNGER_DECAY_PER_HOUR,
      0,
      100
    ),
    energyPerReply: clampCareValue(
      source.energyPerReply ?? source.energy_per_reply ?? CARE_DEFAULT_ENERGY_COST_PER_REPLY,
      0,
      20
    ),
    energyPerProactive: clampCareValue(
      source.energyPerProactive ?? source.energy_per_proactive ?? CARE_DEFAULT_ENERGY_COST_PER_PROACTIVE,
      0,
      20
    )
  };
}

function normalizeCareWorkConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const minReward = clampCareValue(source.rewardCoinsMin ?? source.reward_coins_min ?? 5, 0, 999999);
  const maxReward = clampCareValue(source.rewardCoinsMax ?? source.reward_coins_max ?? minReward, 0, 999999);
  return {
    enabled: Boolean(source.enabled),
    durationSeconds: clampCareValue(source.durationSeconds ?? source.duration_seconds ?? 20, 1, 3600),
    rewardCoinsMin: Math.min(minReward, maxReward),
    rewardCoinsMax: Math.max(minReward, maxReward),
    minHunger: clampCareValue(source.minHunger ?? source.min_hunger ?? 20, 0, 100),
    minEnergy: clampCareValue(source.minEnergy ?? source.min_energy ?? 25, 0, 100),
    hungerCost: clampCareValue(source.hungerCost ?? source.hunger_cost ?? 12, 0, 100),
    energyCost: clampCareValue(source.energyCost ?? source.energy_cost ?? 25, 0, 100),
    startFeedback: normalizeCareFeedback(source.startFeedback || source.start_feedback),
    completeFeedback: normalizeCareFeedback(source.completeFeedback || source.complete_feedback)
  };
}

function normalizeCareAllowanceConfig(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    enabled: Boolean(source.enabled),
    coins: clampCareValue(source.coins ?? 4, 1, 999999),
    cooldownSeconds: clampCareValue(source.cooldownSeconds ?? source.cooldown_seconds ?? 300, 0, 86400),
    maxCoins: clampCareValue(source.maxCoins ?? source.max_coins ?? 6, 1, 999999),
    feedback: normalizeCareFeedback(source.feedback)
  };
}

function normalizeCareFeedback(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const bubble = source.bubble && typeof source.bubble === "object" && !Array.isArray(source.bubble) ? source.bubble : {};
  return {
    emotion: String(source.emotion || "").trim(),
    bubble: {
      text: String(bubble.text || "").trim(),
      durationMs: clampCareValue(bubble.durationMs ?? bubble.duration_ms ?? 0, 0, 60000)
    }
  };
}

function normalizeCareWorkTask(value) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const completeAt = Math.max(0, Math.round(Number(source.completeAt || source.complete_at || 0)));
  if (!completeAt) return null;
  return {
    status: "active",
    startedAt: Math.max(0, Math.round(Number(source.startedAt || source.started_at || 0))),
    completeAt,
    rewardCoins: clampCareValue(source.rewardCoins ?? source.reward_coins ?? 0, 0, 999999)
  };
}

function createCareState(config = getProfileCareConfig()) {
  const care = normalizeCareState({}, config);
  const now = Date.now();
  care.lastDecayAt = now;
  care.updatedAt = now;
  return care;
}

function settleCarePassiveState({ persist = true, now = Date.now() } = {}) {
  const config = getProfileCareConfig();
  if (!config.enabled) return normalizeCareState(state.care, config);
  const care = normalizeCareState(state.care, config);
  const previousDecayAt = care.lastDecayAt || care.updatedAt || now;
  const elapsedMs = Math.max(0, now - previousDecayAt);
  const hungerDecay = Math.floor((elapsedMs / 3600000) * config.decay.hungerPerHour);
  if (hungerDecay <= 0) {
    if (!care.lastDecayAt) {
      care.lastDecayAt = now;
      state.care = care;
      if (persist) persistCareRuntimeChange();
    }
    scheduleCarePassiveTick();
    return care;
  }

  care.hunger = clampCareValue(care.hunger - hungerDecay, 0, 100);
  care.lastDecayAt = now;
  care.updatedAt = now;
  state.care = care;
  scheduleCarePassiveTick();
  if (persist) persistCareRuntimeChange();
  return care;
}

function applyCareTurnCost(turnKind = "") {
  const config = getProfileCareConfig();
  if (!config.enabled) return normalizeCareState(state.care, config);
  const kind = String(turnKind || "").trim().toLowerCase();
  if (kind === "desktop_pet_care_feed") return normalizeCareState(state.care, config);
  const energyCost = kind === "desktop_pet_proactive"
    ? config.decay.energyPerProactive
    : config.decay.energyPerReply;
  if (energyCost <= 0) return normalizeCareState(state.care, config);
  const care = normalizeCareState(state.care, config);
  care.energy = clampCareValue(care.energy - energyCost, 0, 100);
  care.updatedAt = Date.now();
  state.care = care;
  persistCareRuntimeChange();
  return care;
}

function scheduleCarePassiveTick() {
  window.clearTimeout(carePassiveTimer);
  const config = getProfileCareConfig();
  if (!config.enabled) return;
  carePassiveTimer = window.setTimeout(() => {
    settleCarePassiveState();
  }, CARE_PASSIVE_TICK_MS);
}

function buildDesktopCareContext() {
  const config = getProfileCareConfig();
  if (!config.enabled) return null;
  const care = normalizeCareState(state.care, config);
  return {
    enabled: true,
    now: Date.now(),
    hunger: care.hunger,
    energy: care.energy,
    affection: care.affection,
    coins: care.coins,
    work_task_active: Boolean(care.workTask),
    thresholds: {
      hunger_low: 25,
      hunger_critical: 12,
      energy_low: 25,
      energy_critical: 12,
      affection_warm: 45,
      affection_close: 75
    }
  };
}

function buyShopItem(itemId) {
  const item = findCareShopItem(itemId);
  if (!item) {
    notifyShopStatus("这个商品暂时买不了。", "error");
    return false;
  }
  const care = normalizeCareState(state.care, getProfileCareConfig());
  if (care.coins < item.price) {
    notifyShopStatus("钱不够啦。", "warn");
    showBubbleText("钱不够啦。", { transient: true, durationMs: 1600, kind: "shop" });
    return false;
  }
  care.coins -= item.price;
  care.inventory[item.id] = (care.inventory[item.id] || 0) + 1;
  care.updatedAt = Date.now();
  state.care = care;
  persistCareRuntimeChange();
  notifyShopStatus(`买到了：${item.name}`, "ok");
  showBubbleText(`买到了 ${item.name}。`, { transient: true, durationMs: 1600, kind: "shop" });
  return true;
}

function feedInventoryItem(itemId) {
  const item = findCareShopItem(itemId);
  const care = normalizeCareState(state.care, getProfileCareConfig());
  const count = Math.max(0, Math.round(Number(care.inventory[item?.id || itemId]) || 0));
  if (!item || count <= 0) {
    notifyShopStatus("背包里没有这个。", "error");
    return false;
  }
  care.inventory[item.id] = count - 1;
  if (care.inventory[item.id] <= 0) delete care.inventory[item.id];
  const hungerDelta = Number(item.effects?.hunger || 0);
  const affectionDelta = Number(item.effects?.affection || 0);
  const energyDelta = Number(item.effects?.energy || 0);
  care.hunger = clampCareValue(care.hunger + hungerDelta, 0, 100);
  care.affection = clampCareValue(care.affection + affectionDelta, 0, 100);
  care.energy = clampCareValue(care.energy + energyDelta, 0, 100);
  care.updatedAt = Date.now();
  state.care = care;
  applyCareFeedback(item);
  persistCareRuntimeChange();
  notifyShopStatus(`投喂了：${item.name}`, "ok");
  void sendCareFeedReply({ item, care, hungerDelta, energyDelta, affectionDelta });
  return true;
}

async function sendCareFeedReply({ item, care, hungerDelta, energyDelta, affectionDelta }) {
  if (sending || ttsActive || replyDisplayActive) return;
  const itemName = String(item?.name || "").trim();
  if (!itemName) return;
  const turnToken = ++activeTurnToken;
  activeTurnLatencyTrace = createTurnLatencyTrace("care_feed", turnToken, { itemName });
  sending = true;
  firstSpeechSegmentShown = false;
  lastTurnSignature = "";
  lastTurnTextKey = "";
  resetStreamingTtsState(turnToken);
  resetStreamingReplyState(turnToken);
  desktopFileDeliveryHandled.clear();
  scheduleSettingsSnapshot();

  try {
    if (resourceState.health !== "online") {
      const healthy = await reloadCharacterResources({ silent: true });
      if (!healthy) return;
    }
    if (!isTurnActive(turnToken)) return;
    const message = [
      `刚才发生的互动：用户投喂了你${itemName}。`,
      `状态变化：饥饿 ${formatSignedCareDelta(hungerDelta)}，精力 ${formatSignedCareDelta(energyDelta)}，好感 ${formatSignedCareDelta(affectionDelta)}。`,
      `当前状态：饥饿 ${care.hunger}/100，精力 ${care.energy}/100，好感 ${care.affection}/100。`
    ].join("\n");
    const stream = sendThinkStream(message, turnToken, {
      turnKind: "desktop_pet_care_feed"
    });
    await processThinkStream(stream, turnToken);
  } catch (error) {
    if (!isTurnActive(turnToken) || isAbortLike(error)) return;
    setRuntimeStatus(`投喂回复暂时失败：${formatError(error)}`, { mode: "error" });
  } finally {
    markTurnLatency("turn-finished");
    if (isTurnActive(turnToken)) {
      sending = false;
      if (state.currentEmotion === resolveEmotionEntry("thinking").id) {
        setRestingPetEmotion();
      }
      if (!els.bubble.classList.contains("visible")) {
        setPetMotion("idle");
      }
      scheduleMusicEmotionRestore();
      updateActivityControls();
      scheduleSettingsSnapshot();
    }
    finishTurnLatencyTrace(turnToken);
  }
}

function formatSignedCareDelta(value) {
  const number = Math.round(Number(value) || 0);
  return number > 0 ? `+${number}` : String(number);
}

function startCareWork() {
  const config = getProfileCareConfig();
  if (!config.enabled || !config.work.enabled) {
    notifyShopStatus("这个角色还没有配置外出。", "error");
    return false;
  }
  const care = normalizeCareState(state.care, config);
  if (care.workTask) {
    settleCareWorkIfDue();
    if (normalizeCareState(state.care, config).workTask) {
      notifyShopStatus("她已经出门啦。", "warn");
      return false;
    }
  }
  if (care.hunger < config.work.minHunger) {
    const message = "她有点饿，先喂点东西吧。";
    notifyShopStatus(message, "warn");
    showBubbleText(message, { transient: true, durationMs: 1800, kind: "work" });
    return false;
  }
  if (care.energy < config.work.minEnergy) {
    const message = "她现在没什么精神，先休息或投喂一下吧。";
    notifyShopStatus(message, "warn");
    showBubbleText(message, { transient: true, durationMs: 2000, kind: "work" });
    return false;
  }
  const startedAt = Date.now();
  const rewardCoins = randomCareReward(config.work.rewardCoinsMin, config.work.rewardCoinsMax);
  care.hunger = clampCareValue(care.hunger - config.work.hungerCost, 0, 100);
  care.energy = clampCareValue(care.energy - config.work.energyCost, 0, 100);
  care.workTask = {
    status: "active",
    startedAt,
    completeAt: startedAt + config.work.durationSeconds * 1000,
    rewardCoins
  };
  care.updatedAt = startedAt;
  state.care = care;
  applyCareFeedback(config.work.startFeedback, {
    fallbackText: "我出去转一圈，很快回来。",
    kind: "work",
    durationMs: 1800
  });
  persistCareRuntimeChange();
  syncCareAwayVisualState();
  scheduleCareWorkCompletion();
  notifyShopStatus("她出门啦，等一会儿就回来。", "ok");
  return true;
}

function claimCareAllowance() {
  const config = getProfileCareConfig();
  const allowance = config.allowance;
  if (!config.enabled || !allowance.enabled) {
    notifyShopStatus("这个角色还没有配置补给。", "error");
    return false;
  }
  const care = normalizeCareState(state.care, config);
  const now = Date.now();
  const cooldownMs = allowance.cooldownSeconds * 1000;
  const nextAt = care.lastAllowanceAt + cooldownMs;
  if (care.coins >= allowance.maxCoins) {
    const message = `金币低于 ${allowance.maxCoins} 时才能领取补给。`;
    notifyShopStatus(message, "warn");
    showBubbleText(message, { transient: true, durationMs: 1800, kind: "shop" });
    return false;
  }
  if (now < nextAt) {
    const remainSeconds = Math.ceil((nextAt - now) / 1000);
    const message = `补给还在冷却，约 ${remainSeconds} 秒后可以领取。`;
    notifyShopStatus(message, "warn");
    showBubbleText(message, { transient: true, durationMs: 1800, kind: "shop" });
    return false;
  }

  const grant = Math.min(allowance.coins, allowance.maxCoins - care.coins);
  if (grant <= 0) return false;
  care.coins = clampCareValue(care.coins + grant, 0, 999999);
  care.lastAllowanceAt = now;
  care.updatedAt = now;
  state.care = care;
  applyCareFeedback(allowance.feedback, {
    fallbackText: `拿到 ${grant} 枚应急金币。`,
    replacements: { coins: grant },
    kind: "shop",
    durationMs: 1800
  });
  persistCareRuntimeChange();
  notifyShopStatus(`领取补给：+${grant} 金币`, "ok");
  return true;
}

function settleCareWorkIfDue({ force = false } = {}) {
  const config = getProfileCareConfig();
  const care = normalizeCareState(state.care, config);
  const task = care.workTask;
  if (!task) {
    scheduleCareWorkCompletion();
    return false;
  }
  const now = Date.now();
  if (!force && now < task.completeAt) {
    scheduleCareWorkCompletion();
    return false;
  }
  const reward = clampCareValue(task.rewardCoins, config.work.rewardCoinsMin, config.work.rewardCoinsMax);
  care.coins = clampCareValue(care.coins + reward, 0, 999999);
  care.workTask = null;
  care.updatedAt = now;
  state.care = care;
  syncCareAwayVisualState();
  applyCareFeedback(config.work.completeFeedback, {
    fallbackText: `我回来啦，带回 ${reward} 枚金币。`,
    replacements: { reward },
    kind: "work",
    durationMs: 2200
  });
  persistCareRuntimeChange();
  notifyShopStatus(`外出完成：+${reward} 金币`, "ok");
  return true;
}

function scheduleCareWorkCompletion() {
  window.clearTimeout(careWorkTimer);
  const care = normalizeCareState(state.care, getProfileCareConfig());
  const task = care.workTask;
  syncCareAwayVisualState(care);
  if (!task) return;
  const delay = Math.max(0, task.completeAt - Date.now());
  careWorkTimer = window.setTimeout(() => {
    settleCareWorkIfDue({ force: true });
  }, Math.min(delay, 2147483647));
}

function syncCareAwayVisualState(care = normalizeCareState(state.care, getProfileCareConfig())) {
  const away = Boolean(care.workTask);
  els.stage.classList.toggle("is-away", away);
  void setCareAwayClickThrough(away);
  if (away) {
    hideChatInput();
    els.bubble.classList.remove("visible");
  }
}

async function setCareAwayClickThrough(enabled) {
  const next = Boolean(enabled);
  if (careAwayClickThrough === next) return;
  careAwayClickThrough = next;
  state.clickThrough = next;
  if (!isTauriRuntime) return;
  try {
    await invoke("set_click_through", { enabled: next });
  } catch {
    careAwayClickThrough = !next;
    state.clickThrough = !next;
  }
}

function randomCareReward(min, max) {
  const low = clampCareValue(min, 0, 999999);
  const high = clampCareValue(max, low, 999999);
  return low + Math.floor(Math.random() * (high - low + 1));
}

function applyCareFeedback(item) {
  const feedback = item?.feedback || item || {};
  if (feedback.emotion) {
    setTransientEmotion(feedback.emotion, { durationMs: 2200 });
  }
  const fallbackText = arguments[1]?.fallbackText || `${item.name}，收下啦。`;
  const replacements = arguments[1]?.replacements || {};
  const text = formatCareFeedbackText(String(feedback.bubble?.text || fallbackText).trim(), replacements);
  const durationMs = Math.max(1000, Number(feedback.bubble?.durationMs || arguments[1]?.durationMs || 1800));
  showBubbleText(text, { transient: true, durationMs, local: true, kind: arguments[1]?.kind || "feed" });
}

function formatCareFeedbackText(text, replacements = {}) {
  return String(text || "")
    .replace(/\{reward\}/g, String(replacements.reward ?? ""))
    .replace(/\{coins\}/g, String(replacements.coins ?? ""));
}

function persistCareRuntimeChange() {
  persistCurrentCharacterRuntimeState();
  scheduleSave(0);
  scheduleSettingsSnapshot(0);
}

function notifyShopStatus(message, tone = "info") {
  void emitTo("shop", SHOP_STATUS_EVENT, { message, tone, t: Date.now() }).catch(() => {});
}

function findCareShopItem(itemId) {
  const id = String(itemId || "").trim();
  return getProfileCareConfig().shopItems.find((item) => item.id === id) || null;
}

function clampCareValue(value, min, max) {
  const number = Math.round(Number(value));
  return Math.min(max, Math.max(min, Number.isFinite(number) ? number : min));
}

function buildCurrentLyricSnapshot(timeSeconds = Number(els.musicPlayer?.currentTime || 0)) {
  const lrcLines = Array.isArray(musicTrack?.lyrics) ? musicTrack.lyrics : [];
  const timelineLines = Array.isArray(musicTrack?.timelineLyrics) ? musicTrack.timelineLyrics : [];
  const source = lrcLines.length ? "lrc" : timelineLines.length ? "timeline" : "";
  const lines = source === "lrc" ? lrcLines : timelineLines;
  if (!lines.length) return null;
  let currentIndex = -1;
  const currentTime = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].timeSeconds <= currentTime + 0.12) currentIndex = index;
    else break;
  }
  const current = currentIndex >= 0 ? lines[currentIndex] : null;
  const previous = currentIndex > 0 ? lines[currentIndex - 1] : null;
  const next = lines[Math.max(0, currentIndex + 1)] || null;
  return {
    source,
    fileName: source === "lrc" ? musicTrack.lyricFileName || "" : musicTrack.timeline?.transcript_generated_handle || "",
    quality: source === "timeline" ? musicTrack.timelineQuality || "" : "",
    lineCount: lines.length,
    index: currentIndex,
    timeSeconds: current?.timeSeconds ?? 0,
    text: current?.text || "",
    previousText: previous?.text || "",
    nextText: next?.text || ""
  };
}

function isWorkspaceAudioItem(item) {
  if (!item || typeof item !== "object") return false;
  if (String(item.kind || "").trim().toLowerCase() === "audio") return true;
  const subtitle = String(item.subtitle || "").toLowerCase();
  if (subtitle.includes("音频")) return true;
  const format = String(item.format || "").toLowerCase().replace(/^\.+/, "");
  if (MUSIC_FILE_EXTENSIONS.has(format)) return true;
  const name = String(item.title || item.name || "").toLowerCase();
  const ext = name.split(".").pop();
  if (ext && MUSIC_FILE_EXTENSIONS.has(ext)) return true;
  return false;
}

function buildWorkspaceMusicRecommendationFromItem(item, section) {
  const handle = String(item.handle || item.id || "").trim();
  const title = String(item.title || item.name || item.fileName || "").trim();
  const format = String(item.format || "").trim();
  const prefix = section === "outputs" ? "workspace:generated" : "workspace:attachment";
  const durationSeconds = Number(item.durationSeconds || item.duration_seconds || 0);
  return {
    id: handle ? `${prefix}:${handle}` : `${section}_${title}_${format}`,
    sourceId: handle ? `${prefix}:${handle}` : "",
    itemType: section === "outputs" ? "generated" : "attachment",
    handle,
    title,
    format,
    sizeBytes: Number(item.sizeBytes || item.size_bytes || 0),
    durationSeconds: Number.isFinite(durationSeconds) ? Math.max(0, durationSeconds) : 0,
    durationLabel: formatPlaylistDuration(durationSeconds),
    reason: section === "outputs" ? "生成音频" : "手边音频",
    playable: true
  };
}

function dedupeMusicRecommendations(items) {
  const seenHandles = new Set();
  const seenSoftKeys = new Set();
  return items.filter((item) => {
    const itemType = String(item.itemType || "").trim().toLowerCase();
    const handle = String(item.handle || "").trim();
    const handleKey = handle ? `${itemType}:${handle}` : "";
    if (handleKey && seenHandles.has(handleKey)) return false;
    const title = String(item.title || "").trim().toLowerCase();
    const format = String(item.format || "").trim().toLowerCase().replace(/^\.+/, "");
    const sizeBytes = Number(item.sizeBytes || item.size_bytes || 0);
    const hasSoftKey = Boolean(title) && Number.isFinite(sizeBytes) && sizeBytes > 0;
    const softKey = hasSoftKey ? `${title}|${format}|${sizeBytes}` : "";
    if (softKey && seenSoftKeys.has(softKey)) return false;
    if (handleKey) seenHandles.add(handleKey);
    if (softKey) seenSoftKeys.add(softKey);
    return true;
  });
}

function setWorkspaceMusicRecommendations(nextRecommendations) {
  const next = Array.isArray(nextRecommendations) ? nextRecommendations : [];
  if (JSON.stringify(workspaceMusicRecommendations) === JSON.stringify(next)) return;
  workspaceMusicRecommendations = next;
  scheduleSettingsSnapshot();
}

async function refreshWorkspaceMusicRecommendations() {
  if (workspaceMusicRecommendationsLoading) return;
  workspaceMusicRecommendationsLoading = true;
  try {
    const profileUserId = String(state.profileUserId || PROFILE_USER_ID);
    const sessionId = String(state.sessionId || "");
    if (!sessionId || resourceState.health !== "online") {
      setWorkspaceMusicRecommendations([]);
      workspaceAudioCatalog = [];
      return;
    }
    const url = buildBackendEndpointUrl("workspaceSummary", "/desktop-pet/workspace/summary", {
      user_id: sessionId,
      real_user_id: profileUserId,
      ...buildBackendCharacterContext(),
      limit: 24,
      t: String(Date.now())
    });
    const response = await backendFetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      setWorkspaceMusicRecommendations([]);
      workspaceAudioCatalog = [];
      return;
    }
    const payload = await response.json().catch(() => null);
    const sections = payload?.sections || payload;
    const candidates = [];
    for (const sectionName of ["files", "outputs"]) {
      const items = Array.isArray(sections[sectionName]) ? sections[sectionName] : [];
      for (const item of items) {
        if (isWorkspaceAudioItem(item)) {
          candidates.push(buildWorkspaceMusicRecommendationFromItem(item, sectionName));
        }
      }
    }
    const nextRecs = dedupeMusicRecommendations(candidates).slice(0, 3);
    setWorkspaceMusicRecommendations(nextRecs);
    workspaceAudioCatalog = dedupeMusicRecommendations(candidates).slice(0, 12);
  } catch {
    setWorkspaceMusicRecommendations([]);
    workspaceAudioCatalog = [];
  } finally {
    workspaceMusicRecommendationsLoading = false;
  }
}

function scheduleWorkspaceMusicRecommendationsRefresh(delay = 300) {
  window.clearTimeout(workspaceMusicRecommendationsRefreshTimer);
  workspaceMusicRecommendationsRefreshTimer = window.setTimeout(() => {
    workspaceMusicRecommendationsRefreshTimer = 0;
    void refreshWorkspaceMusicRecommendations();
  }, delay);
}

function buildQueueMusicRecommendationsSnapshot(limit = 3) {
  if (!musicQueue.length) return [];
  if (musicQueue.length === 1) {
    const current = musicQueue[0];
    const durationSeconds = Number(els.musicPlayer?.duration || 0);
    return [{
      id: current.sourceId || "current",
      sourceId: String(current.sourceId || ""),
      title: String(current.displayName || current.fileName || ""),
      artist: "",
      durationSeconds: Number.isFinite(durationSeconds) ? Math.max(0, durationSeconds) : 0,
      durationLabel: formatPlaylistDuration(durationSeconds),
      reason: "当前播放",
      playable: true
    }];
  }
  const recommendations = [];
  const maxIterations = Math.min(musicQueue.length - 1, limit);
  for (let offset = 1; offset <= maxIterations && recommendations.length < limit; offset += 1) {
    const index = (musicQueueIndex + offset) % musicQueue.length;
    const track = musicQueue[index];
    if (!track) continue;
    recommendations.push({
      id: track.sourceId || `rec_${index}`,
      sourceId: String(track.sourceId || ""),
      title: String(track.displayName || track.fileName || ""),
      artist: "",
      durationSeconds: 0,
      durationLabel: "",
      reason: offset === 1 ? "下一首" : "队列中",
      playable: true
    });
  }
  return recommendations;
}

function buildMusicRecommendationsSnapshot(limit = 3) {
  if (workspaceMusicRecommendations.length > 0) {
    return workspaceMusicRecommendations.slice(0, limit);
  }
  return buildQueueMusicRecommendationsSnapshot(limit);
}

function formatPlaylistDuration(seconds) {
  const sec = Math.max(0, Math.round(Number(seconds) || 0));
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function buildMusicSnapshot() {
  const previous = getPreviousMusicTrack();
  const next = getNextMusicTrack();
  const progress = Number(els.musicPlayer?.currentTime || 0);
  const duration = Number(els.musicPlayer?.duration || 0);
  const currentLyric = buildCurrentLyricSnapshot(progress);
  return {
    track: summarizeMusicTrack(musicTrack),
    queue: musicQueue.map(summarizeMusicTrack).filter(Boolean),
    queueIndex: musicQueueIndex,
    queueCount: musicQueue.length,
    queueLabel: getMusicQueueLabel(),
    hasPrevious: hasPreviousMusicTrack(),
    hasNext: hasNextMusicTrack(),
    previousDisplayName: previous?.displayName || "",
    nextDisplayName: next?.displayName || "",
    progressSeconds: Number.isFinite(progress) ? Math.max(0, progress) : 0,
    durationSeconds: Number.isFinite(duration) ? Math.max(0, duration) : 0,
    currentLyric,
    playing: musicPlaying,
    paused: musicPaused,
    loading: musicLoading,
    displayName: getMusicDisplayName(),
    systemMedia: summarizeSystemMedia(),
    systemLyrics: summarizeSystemMediaLyrics(),
    recommendations: buildMusicRecommendationsSnapshot()
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
  const recs = buildMusicRecommendationsSnapshot();
  const recsSummary = recs.map(({ title, reason, sourceId }) => ({ title, reason, source_id: sourceId || "" }));
  const catalog = buildPlayableMusicCatalog();
  const systemActivity = buildSystemMediaActivity({ recommendations: recsSummary, catalog });

  if (shouldUseSystemMediaActivity(systemActivity)) return systemActivity;

  if (!musicTrack) {
    if (recs.length > 0 || catalog.length > 0) {
      return { type: "audio_recommendations", status: "idle", recommendations: recsSummary, catalog };
    }
    return null;
  }

  const currentTime = Number(els.musicPlayer?.currentTime || 0);
  const duration = Number(els.musicPlayer?.duration || 0);
  const currentLyric = buildCurrentLyricSnapshot(currentTime);
  const status = musicPlaying ? "running" : musicPaused ? "paused" : "stopped";
  const progressSeconds = Number.isFinite(currentTime) ? Math.max(0, currentTime) : 0;
  if (status === "stopped" && progressSeconds <= 0 && recs.length === 0 && catalog.length === 0) return null;
  return {
    type: "audio_playback",
    title: getMusicDisplayName() || "未命名音乐",
    source_id: musicTrack.sourceId || "local_music_current",
    handle: "current",
    status,
    progress_seconds: progressSeconds,
    duration_seconds: Number.isFinite(duration) ? Math.max(0, duration) : 0,
    source_kind: "local_file",
    file_name: musicTrack.fileName,
    extension: musicTrack.extension,
    attachment_handle: musicTrack.backendAttachment?.handle || "",
    attachment_id: musicTrack.backendAttachment?.attachmentId || "",
    timeline_id: musicTrack.timeline?.timeline_id || "",
    timeline_status: musicTrack.timelineStatus || "",
    timeline_quality: musicTrack.timelineQuality || "",
    queue_count: musicQueue.length,
    queue_index: musicQueueIndex >= 0 ? musicQueueIndex + 1 : 0,
    queue_titles: musicQueue.map((track) => track.displayName).filter(Boolean).slice(0, 8),
    previous_title: getPreviousMusicTrack()?.displayName || "",
    next_title: getNextMusicTrack()?.displayName || "",
    lyric_file_name: currentLyric?.fileName || "",
    lyric_line_count: currentLyric?.lineCount || 0,
    lyric_index: currentLyric?.index ?? -1,
    lyric_current: currentLyric?.text || "",
    lyric_previous: currentLyric?.previousText || "",
    lyric_next: currentLyric?.nextText || "",
    recommendations: recsSummary,
    catalog
  };
}

function shouldUseSystemMediaActivity(systemActivity) {
  if (!systemActivity) return false;
  if (!musicTrack) return true;
  // Local track loaded: keep local context while playing or paused
  if (musicPlaying || musicPaused) return false;
  // Local track stopped: yield to system media only if it is actively playing
  return Boolean(systemMedia.isPlaying);
}

function buildSystemMediaActivity({ recommendations = [], catalog = [] } = {}) {
  if (!isFreshSystemMedia(systemMedia)) return null;
  const titleParts = [systemMedia.title, systemMedia.artist].filter(Boolean);
  const title = titleParts.length ? titleParts.join(" - ") : "系统正在播放的音乐";
  const currentLyric = buildSystemMediaLyricSnapshot(systemMedia.positionSeconds);
  const status = systemMedia.isPlaying
    ? "running"
    : systemMedia.playbackStatus === "paused"
      ? "paused"
      : "stopped";
  if (status === "stopped") return null;
  return {
    type: "audio_playback",
    title,
    source_id: `system_media:${systemMedia.trackKey || simpleHash(title)}`,
    handle: "system_media_current",
    status,
    progress_seconds: safePositiveSeconds(systemMedia.positionSeconds),
    duration_seconds: safePositiveSeconds(systemMedia.durationSeconds),
    source_kind: "system_media",
    source_app: systemMedia.sourceApp || "",
    artist: systemMedia.artist || "",
    album: systemMedia.album || "",
    system_media: true,
    playback_status: systemMedia.playbackStatus || "unknown",
    lyric_file_name: currentLyric?.text ? currentLyric.source || "online" : "",
    lyric_line_count: currentLyric?.text ? currentLyric.lineCount || 0 : 0,
    lyric_index: currentLyric?.text ? currentLyric.index ?? -1 : -1,
    lyric_current: currentLyric?.text || "",
    lyric_previous: currentLyric?.previousText || "",
    lyric_next: currentLyric?.nextText || "",
    lyric_status: currentLyric?.status || systemMediaLyrics.status || "unavailable",
    lyric_reason: currentLyric?.reason || systemMediaLyrics.reason || "",
    lyric_confidence: currentLyric?.confidence || systemMediaLyrics.confidence || "",
    lyric_source: currentLyric?.source || systemMediaLyrics.source || "",
    recommendations,
    catalog
  };
}

function buildPlayableMusicCatalog() {
  const seen = new Set();
  const catalog = [];

  const addItem = (item) => {
    if (!item || !item.sourceId || !item.title) return;
    const key = item.sourceId || item.title;
    if (seen.has(key)) return;
    seen.add(key);
    catalog.push({
      source_id: item.sourceId,
      title: item.title,
      source: item.itemType ? `workspace:${item.itemType}` : "queue",
      item_type: item.itemType || "",
      handle: item.handle || "",
      reason: item.reason || "",
      playable: true
    });
  };

  for (const track of musicQueue) {
    if (track?.displayName) {
      addItem({
        sourceId: track.sourceId || track.queueDedupeKey || "",
        title: String(track.displayName || ""),
        itemType: "",
        handle: "",
        reason: "播放队列"
      });
    }
  }

  for (const rec of workspaceAudioCatalog) {
    addItem({
      sourceId: rec.sourceId,
      title: rec.title,
      itemType: rec.itemType,
      handle: rec.handle,
      reason: rec.reason || "手边音频"
    });
  }

  return catalog.slice(0, 12);
}

function resetStreamingReplyState(turnToken = 0) {
  streamingReplyTurnToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
  streamingReplyText = "";
  streamingReplyFinalized = false;
  streamedReplyLastShownAt = 0;
  streamedReplyLastShownText = "";
  streamedReplyQueue = [];
  lastStateRequestSignature = "";
  streamingReplySegmentKeys.clear();
}

function ensureStreamingReplyTurn(turnToken) {
  const normalizedToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
  if (streamingReplyTurnToken === normalizedToken) return;
  resetStreamingReplyState(normalizedToken);
}

function queueStreamedReplySegment(text, turnToken, segmentIndex = null) {
  const normalized = normalizeTtsText(text);
  if (!normalized || !isTurnActive(turnToken)) return false;

  ensureStreamingReplyTurn(turnToken);
  const textKey = buildSpeechTextKey(normalized);
  if (!textKey) return false;
  const numericIndex = Number(segmentIndex);
  const segmentKey = Number.isFinite(numericIndex)
    ? `${numericIndex}:${textKey}`
    : textKey;
  if (streamingReplySegmentKeys.has(segmentKey)) return false;

  streamingReplySegmentKeys.add(segmentKey);
  streamingReplyText = normalizeTtsText(streamingReplyText ? `${streamingReplyText}${normalized}` : normalized);
  streamedReplyQueue.push(normalized);
  const immediate = !replyDisplayActive || bubbleKind === "thinking" || !streamedReplyLastShownText;
  scheduleStreamedReplyDisplay({ immediate });
  return true;
}

function queueLiveReplyPayloadItems(items, { speaking = true } = {}) {
  const normalized = (Array.isArray(items) ? items : [items])
    .map((item) => normalizeTtsText(item))
    .filter(Boolean);
  if (!normalized.length) {
    finalizeStreamedReplyDisplay();
    return false;
  }

  const tail = removeStreamingReplyPrefix(normalized.join(""));
  if (tail) {
    const tailSegments = splitSpeechText(tail);
    for (const segment of tailSegments) {
      queueStreamedReplySegment(segment, activeTurnToken);
    }
    if (speaking) setPetMotion("speaking");
  }
  finalizeStreamedReplyDisplay();
  return true;
}

function removeStreamingReplyPrefix(text) {
  const finalText = normalizeTtsText(text);
  const prefix = normalizeTtsText(streamingReplyText);
  if (!finalText || !prefix) return finalText;
  if (finalText.startsWith(prefix)) return normalizeTtsText(finalText.slice(prefix.length));

  const finalKey = buildSpeechTextKey(finalText);
  const prefixKey = buildSpeechTextKey(prefix);
  if (!prefixKey || !finalKey.startsWith(prefixKey)) return "";
  if (finalKey.length <= prefixKey.length) return "";

  let compactCount = 0;
  let sliceIndex = 0;
  for (let index = 0; index < finalText.length; index += 1) {
    if (!/\s/.test(finalText[index])) compactCount += 1;
    if (compactCount >= prefixKey.length) {
      sliceIndex = index + 1;
      break;
    }
  }
  return normalizeTtsText(finalText.slice(sliceIndex));
}

function scheduleStreamedReplyDisplay({ immediate = false } = {}) {
  if (!streamedReplyQueue.length || segmentTimer) return;
  if (immediate) {
    showNextStreamedReplySegment();
    return;
  }
  const elapsed = Date.now() - streamedReplyLastShownAt;
  const delay = Math.max(0, getSegmentDisplayDelay(streamedReplyLastShownText) - elapsed);
  segmentTimer = window.setTimeout(() => {
    segmentTimer = 0;
    showNextStreamedReplySegment();
  }, delay);
}

function showNextStreamedReplySegment() {
  window.clearTimeout(segmentTimer);
  segmentTimer = 0;
  if (!streamedReplyQueue.length || !isTurnActive(streamingReplyTurnToken)) return;

  const text = streamedReplyQueue.shift();
  streamedReplyLastShownText = text;
  streamedReplyLastShownAt = Date.now();
  firstSpeechSegmentShown = true;
  displayReplyBubbleText(text, { speaking: true });
  markTurnLatencyOnce("first-bubble-displayed", { chars: String(text || "").length });

  if (streamedReplyQueue.length) {
    scheduleStreamedReplyDisplay();
  } else if (streamingReplyFinalized) {
    scheduleBubbleReset(Math.max(String(text || "").length, 4), bubbleToken);
  }
}

function finalizeStreamedReplyDisplay() {
  if (!streamingReplyText) return;
  streamingReplyFinalized = true;
  if (streamedReplyQueue.length) {
    scheduleStreamedReplyDisplay();
    return;
  }
  if (replyDisplayActive && bubbleKind === "reply") {
    scheduleBubbleReset(Math.max(String(streamedReplyLastShownText || "").length, 4), bubbleToken);
  }
}

function resetStreamingTtsState(turnToken = 0) {
  streamingTtsTurnToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
  streamingTtsText = "";
  streamingTtsSegmentKeys.clear();
}

function ensureStreamingTtsTurn(turnToken) {
  const normalizedToken = Number.isFinite(Number(turnToken)) ? Number(turnToken) : 0;
  if (streamingTtsTurnToken === normalizedToken) return;
  resetStreamingTtsState(normalizedToken);
}

function queueStreamedTtsSegment(text, turnToken, segmentIndex = null) {
  const normalized = normalizeTtsText(text);
  if (!normalized || !isTurnActive(turnToken) || !state.voiceEnabled) return;
  if (resourceState.tts?.enabled === false) return;

  ensureStreamingTtsTurn(turnToken);
  const textKey = buildSpeechTextKey(normalized);
  if (!textKey) return;
  const numericIndex = Number(segmentIndex);
  const segmentKey = Number.isFinite(numericIndex)
    ? `${numericIndex}:${textKey}`
    : textKey;
  if (streamingTtsSegmentKeys.has(segmentKey)) return;

  streamingTtsSegmentKeys.add(segmentKey);
  streamingTtsText = normalizeTtsText(streamingTtsText ? `${streamingTtsText}${normalized}` : normalized);
  queueTtsItems([normalized], `stream:${turnToken}:${segmentKey}`, { append: true, preserveSegments: true });
}

function queueLiveTtsPayloadItems(items, signature = "") {
  const normalized = (Array.isArray(items) ? items : [items])
    .map((item) => normalizeTtsText(item))
    .filter(Boolean);
  if (!normalized.length) return;

  if (streamingTtsText) {
    const tail = removeStreamingTtsPrefix(normalized.join(""));
    if (tail) {
      const tailSegments = splitSpeechText(tail);
      queueTtsItems(tailSegments.length ? tailSegments : [tail], signature ? `${signature}:tail` : "stream-tail", {
        append: true,
        preserveSegments: true
      });
    }
    return;
  }

  queueTtsItems(normalized, signature, { preserveSegments: true });
}

function removeStreamingTtsPrefix(text) {
  const finalText = normalizeTtsText(text);
  const prefix = normalizeTtsText(streamingTtsText);
  if (!finalText || !prefix) return finalText;
  if (finalText.startsWith(prefix)) return normalizeTtsText(finalText.slice(prefix.length));

  const finalKey = buildSpeechTextKey(finalText);
  const prefixKey = buildSpeechTextKey(prefix);
  if (!prefixKey || !finalKey.startsWith(prefixKey)) return "";
  if (finalKey.length <= prefixKey.length) return "";

  let compactCount = 0;
  let sliceIndex = 0;
  for (let index = 0; index < finalText.length; index += 1) {
    if (!/\s/.test(finalText[index])) compactCount += 1;
    if (compactCount >= prefixKey.length) {
      sliceIndex = index + 1;
      break;
    }
  }
  return normalizeTtsText(finalText.slice(sliceIndex));
}

function scheduleTtsPrewarm({ force = false, delayMs = TTS_PREWARM_DELAY_MS } = {}) {
  window.clearTimeout(ttsPrewarmTimer);
  ttsPrewarmTimer = 0;
  if (!canRunTtsPrewarm()) return;
  ttsPrewarmTimer = window.setTimeout(() => {
    ttsPrewarmTimer = 0;
    void runTtsPrewarm({ force });
  }, Math.max(0, Number(delayMs) || 0));
}

function cancelTtsPrewarm() {
  window.clearTimeout(ttsPrewarmTimer);
  ttsPrewarmTimer = 0;
  if (ttsPrewarmController) {
    ttsPrewarmController.abort();
    ttsPrewarmController = null;
  }
  ttsPrewarmInFlightKey = "";
}

function canRunTtsPrewarm() {
  if (!state.voiceEnabled || resourceState.health !== "online" || resourceState.tts?.enabled === false) return false;
  if (sending || ttsActive || ttsQueue.length > 0 || replyDisplayActive || proactiveWakeRunning) return false;
  if (voiceInputState === "recording" || voiceInputState === "processing") return false;
  return true;
}

function getTtsPrewarmKey() {
  return [
    getCharacterRuntimeKey(),
    String(resourceState.tts?.endpoint || ""),
    String(resourceState.tts?.responseMediaType || "")
  ].join("::");
}

async function runTtsPrewarm({ force = false } = {}) {
  if (!canRunTtsPrewarm()) return;
  const key = getTtsPrewarmKey();
  const warmedAt = Number(ttsPrewarmReadyAtByKey.get(key) || 0);
  if (!force && warmedAt && Date.now() - warmedAt < TTS_PREWARM_COOLDOWN_MS) return;
  if (ttsPrewarmInFlightKey === key) return;

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), TTS_PREWARM_TIMEOUT_MS);
  const startedAt = performance.now();
  ttsPrewarmController = controller;
  ttsPrewarmInFlightKey = key;

  try {
    const requestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        text: TTS_PREWARM_TEXT,
        ...buildBackendCharacterContext()
      })
    };
    if (isTauriRuntime) {
      requestInit.connectTimeout = TTS_PREWARM_TIMEOUT_MS;
    } else {
      requestInit.signal = controller.signal;
    }

    const response = await backendFetch(buildBackendEndpointUrl("tts", "/tts", { t: Date.now() }), requestInit);
    if (response.ok) {
      await response.arrayBuffer();
      ttsPrewarmReadyAtByKey.set(key, Date.now());
      logTtsTiming("prewarm-ready", {
        requestMs: Math.round(performance.now() - startedAt),
        character: getCurrentCharacterPackId()
      });
    } else {
      logTtsTiming("prewarm-skipped", { status: response.status });
    }
  } catch (error) {
    if (!controller.signal.aborted) {
      logTtsTiming("prewarm-failed", { error: formatError(error) });
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (ttsPrewarmController === controller) ttsPrewarmController = null;
    if (ttsPrewarmInFlightKey === key) ttsPrewarmInFlightKey = "";
  }
}

function queueTtsItems(items, signature = "", { append = false, preserveSegments = false } = {}) {
  const normalized = (Array.isArray(items) ? items : [items])
    .map((item) => normalizeTtsText(item))
    .filter(Boolean);
  if (!state.voiceEnabled || !normalized.length) return;
  if (resourceState.tts?.enabled === false) {
    setRuntimeStatus("后端语音暂未开启", { mode: "error" });
    return;
  }
  const nextItems = buildTtsQueueItems(normalized, { preserveSegments });
  if (!nextItems.length) return;
  cancelTtsPrewarm();

  if (append) {
    ttsQueue.push(...nextItems);
    if (!ttsActive) {
      ttsToken += 1;
      void runTtsQueue(ttsToken);
    }
    return;
  }

  if (signature && signature === lastTtsSignature) return;

  stopTts({ resetSignature: false });
  lastTtsSignature = signature || `tts:${normalized.join("\u241e")}`;
  ttsToken += 1;
  const token = ttsToken;
  ttsQueue = nextItems;
  void runTtsQueue(token);
}

async function testTts() {
  if (!state.voiceEnabled) {
    setVoiceEnabled(true);
  }
  queueTtsItems([getActiveCharacterText("ttsTestText")], `test:${Date.now()}`);
}

async function previewTts(text) {
  const normalized = normalizeTtsText(text) || getActiveCharacterText("ttsTestText");
  if (!state.voiceEnabled) {
    setVoiceEnabled(true);
  }
  queueTtsItems([normalized], `preview:${Date.now()}`);
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
  let pendingPrepared = startNextTtsPrepare(token);
  try {
    while (token === ttsToken && state.voiceEnabled) {
      if (!pendingPrepared) pendingPrepared = startNextTtsPrepare(token);
      if (!pendingPrepared) break;

      const prepared = await pendingPrepared;
      if (prepared?.audio && (token !== ttsToken || !state.voiceEnabled)) {
        discardPreparedTtsAudio(prepared.audio);
        break;
      }

      const nextPrepared = token === ttsToken && state.voiceEnabled ? startNextTtsPrepare(token) : null;
      pendingPrepared = nextPrepared;
      if (prepared?.error) {
        reportTtsError(prepared.error, token);
      } else if (prepared?.audio) {
        try {
          await playPreparedTtsAudio(prepared.audio, token);
        } catch (error) {
          reportTtsError(error, token);
        }
      }
    }
  } finally {
    if (token !== ttsToken || !state.voiceEnabled) {
      discardPendingTtsPrepare(pendingPrepared);
    }
    if (token === ttsToken) {
      ttsQueue = [];
      setTtsActive(false);
    }
  }
}

function startNextTtsPrepare(token) {
  while (ttsQueue.length > 0) {
    const text = ttsQueue.shift();
    if (!text) continue;
    return fetchTtsAudio(text, token)
      .then((audio) => ({ text, audio }))
      .catch((error) => ({ text, error }));
  }
  return null;
}

async function fetchTtsAudio(text, token) {
  const controller = new AbortController();
  const startedAt = performance.now();
  const timeoutId = window.setTimeout(() => controller.abort(), TTS_TIMEOUT_MS);
  const slowTimerId = window.setTimeout(() => {
    if (token === ttsToken && ttsController === controller && !controller.signal.aborted) {
      setRuntimeStatus("语音生成中...", { mode: "speaking" });
    }
  }, TTS_SLOW_REQUEST_MS);
  ttsController = controller;

  try {
    const requestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        text,
        ...buildBackendCharacterContext()
      })
    };
    if (isTauriRuntime) {
      requestInit.connectTimeout = 30_000;
    } else {
      requestInit.signal = controller.signal;
    }

    const response = await backendFetch(buildBackendEndpointUrl("tts", "/tts", { t: Date.now() }), requestInit);
    if (!response.ok) throw new Error(await readBackendErrorMessage(response, `TTS HTTP ${response.status}`));

    const arrayBuffer = await response.arrayBuffer();
    if (token !== ttsToken) return null;
    if (controller.signal.aborted) throw createAbortError();

    const contentType = response.headers.get("content-type") || "audio/mpeg";
    const blob = new Blob([arrayBuffer], { type: contentType });
    const finishedAt = performance.now();
    const audio = {
      text,
      contentType,
      objectUrl: URL.createObjectURL(blob),
      requestMs: Math.round(finishedAt - startedAt)
    };
    logTtsTiming("prepared", {
      requestMs: audio.requestMs,
      textLength: text.length,
      contentType: audio.contentType
    });
    return audio;
  } finally {
    window.clearTimeout(timeoutId);
    window.clearTimeout(slowTimerId);
    if (ttsController === controller) ttsController = null;
  }
}

async function playPreparedTtsAudio(prepared, token) {
  if (!prepared?.objectUrl) return;
  ttsObjectUrl = prepared.objectUrl;
  prepared.objectUrl = "";
  try {
    els.voicePlayer.src = ttsObjectUrl;
    els.voicePlayer.currentTime = 0;
    els.voicePlayer.volume = state.voiceVolume;
    setRuntimeStatus("语音播放中", { mode: "speaking" });
    const playRequestedAt = performance.now();
    await els.voicePlayer.play();
    if (token !== ttsToken) return;
    logTtsTiming("playback-started", {
      requestMs: prepared.requestMs,
      playStartupMs: Math.round(performance.now() - playRequestedAt),
      textLength: prepared.text?.length || 0
    });
    await waitForTtsAudio(token);
  } finally {
    cleanupTtsObjectUrl();
  }
}

function discardPreparedTtsAudio(prepared) {
  if (!prepared?.objectUrl) return;
  URL.revokeObjectURL(prepared.objectUrl);
  prepared.objectUrl = "";
}

function discardPendingTtsPrepare(pendingPrepared) {
  if (!pendingPrepared) return;
  pendingPrepared
    .then((prepared) => {
      if (prepared?.audio) discardPreparedTtsAudio(prepared.audio);
    })
    .catch(() => {});
}

function reportTtsError(error, token) {
  if (token !== ttsToken) return;
  setRuntimeStatus(`语音播放失败：${friendlyErrorMessage(formatError(error))}`, { mode: "error" });
}

function createAbortError() {
  const error = new Error("请求超时");
  error.name = "AbortError";
  return error;
}

function logTtsTiming(event, details = {}) {
  try {
    if (window.localStorage?.getItem("akane.debug.tts") !== "1") return;
  } catch {
    return;
  }
  console.debug("[Akane TTS]", event, details);
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
    scheduleMusicEmotionRestore();
    updateActivityControls();
    scheduleSettingsSnapshot();
  }
}

function normalizeTtsText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function buildTtsQueueItems(items, { preserveSegments = false } = {}) {
  const source = (Array.isArray(items) ? items : [items])
    .map((item) => normalizeTtsText(item))
    .filter(Boolean);
  if (!source.length) return [];
  if (preserveSegments) return source;

  const chunks = [];
  let current = "";
  const pushCurrent = () => {
    if (!current.trim()) return;
    chunks.push(current.trim());
    current = "";
  };

  for (const item of source) {
    const pieces = splitTtsTextForLatency(item);
    for (const piece of pieces) {
      const text = normalizeTtsText(piece);
      if (!text) continue;
      const glue = current && /[。！？!?；;，,、…]$/.test(current) ? "" : "，";
      const next = current ? `${current}${glue}${text}` : text;
      if (current && next.length > TTS_CHUNK_SOFT_LIMIT) {
        pushCurrent();
        current = text;
      } else {
        current = next;
      }
    }
  }

  pushCurrent();
  return chunks.length ? chunks : source;
}

function splitTtsTextForLatency(text) {
  const normalized = normalizeTtsText(text);
  if (!normalized) return [];
  const phraseMatches = normalized.match(/[^。！？!?；;，,、…]+[。！？!?；;，,、…]?/g) || [normalized];
  const pieces = [];
  for (const phrase of phraseMatches) {
    const cleanPhrase = normalizeTtsText(phrase);
    if (!cleanPhrase) continue;
    if (cleanPhrase.length <= TTS_CHUNK_SOFT_LIMIT) {
      pieces.push(cleanPhrase);
    } else {
      pieces.push(...hardWrapText(cleanPhrase, TTS_CHUNK_SOFT_LIMIT));
    }
  }
  return pieces.length ? pieces : [normalized];
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
  if (els.clearMusicQueue) {
    els.clearMusicQueue.disabled = musicLoading || (!musicTrack && !musicQueue.length);
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
    setRestingPetEmotion();
    scheduleMusicEmotionRestore();
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
  let entry = resolveEmotionEntry(emotion);
  if (entry && !entry.url && entry.path && isTauriRuntime) {
    try {
      entry = { ...entry, url: convertFileSrc(entry.path) };
    } catch (error) {
      console.error("[setPetEmotion] convertFileSrc failed:", entry.path, error);
    }
  }
  if (entry && !entry.url && canUseBundledEmotionFallback()) {
    const fallback = findEntry(bundledOutfit.emotions, emotion) || bundledOutfit.emotions[0];
    if (fallback?.url) {
      console.warn("[setPetEmotion] falling back to bundled emotion:", emotion, fallback);
      entry = { ...fallback };
    }
  }
  if (!entry?.url) {
    console.error("[setPetEmotion] resolved entry has no image URL:", emotion, entry);
    setStatus(`立绘地址缺失：${emotion}`, { durationMs: 3600 });
    if (entry?.id) state.currentEmotion = entry.id;
    scheduleSettingsSnapshot();
    updateMenuLabels();
    if (persist) scheduleSave(0);
    return entry?.id || String(emotion || "").trim();
  }
  if (!force && state.currentEmotion === entry.id && els.petImage.src) return entry.id;
  state.currentEmotion = entry.id;
  visualRenderer.setExpression(entry, { force });
  scheduleSettingsSnapshot();
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
  return findEntry(emotions, getProfileDefaultEmotion()) || findEntry(emotions, "normal") || emotions[0] || getDefaultLocalOutfit().emotions[0];
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
  for (const item of getProfileEmotionAliases()[key] || []) add(item);
  add(getProfileDefaultEmotion());
  add("normal");
  return result;
}

function buildCurrentVisual() {
  const outfit = getActiveOutfit();
  const emotions = getActiveEmotions();
  const emotion = resolveEmotionEntry(state.currentEmotion).id;
  const characterPackId = getCurrentCharacterPackId();
  return {
    character_pack_id: characterPackId,
    emotion,
    character: {
      character_pack_id: characterPackId,
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

function getProfileDefaultOutfit() {
  return String(getActiveCharacterProfile()?.appearance?.defaultOutfit || DEFAULT_OUTFIT).trim() || DEFAULT_OUTFIT;
}

function getProfileDefaultEmotion() {
  return String(getActiveCharacterProfile()?.appearance?.defaultEmotion || DEFAULT_EMOTION).trim() || DEFAULT_EMOTION;
}

function getProfileMusicEmotion() {
  return String(getActiveCharacterProfile()?.appearance?.musicEmotion || MUSIC_EMOTION).trim() || getProfileDefaultEmotion();
}

function getProfileRequiredEmotions() {
  const values = getActiveCharacterProfile()?.appearance?.requiredEmotions;
  return Array.isArray(values) && values.length ? values : [getProfileDefaultEmotion()];
}

function getProfileRecommendedEmotions() {
  const values = getActiveCharacterProfile()?.appearance?.recommendedEmotions;
  return Array.isArray(values) ? values : RECOMMENDED_EMOTIONS;
}

function getProfileEmotionAliases() {
  return getActiveCharacterProfile()?.emotionAliases || COMMON_EMOTION_CANDIDATES;
}

function getProfileLocalClickLines() {
  const lines = getActiveCharacterProfile()?.dialogue?.localClickLines;
  return Array.isArray(lines) && lines.length ? lines : LOCAL_CLICK_LINES;
}

function getProfileText(key, fallback) {
  return String(getActiveCharacterProfile()?.dialogue?.[key] || fallback || "").trim();
}

function getProfileIdentityText(key, fallback) {
  return String(getActiveCharacterProfile()?.identity?.[key] || fallback || "").trim();
}

function getCurrentCharacterPackId() {
  return state.characterPackId || getActiveCharacterPackId();
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
    missingRequired: getProfileRequiredEmotions().filter((item) => !keys.has(normalizeEntryKey(item))),
    missingRecommended: getProfileRecommendedEmotions().filter((item) => !keys.has(normalizeEntryKey(item)))
  };
}

function resourceSourceLabel(source) {
  return {
    manifest: "当前角色包",
    character_pack: "本地角色包",
    bundled: "内置兜底"
  }[String(source || "")] || "本地兜底";
}

function getLocalResourceSource() {
  return runtimeCharacterPackOutfits.length || characterPackOutfits.length ? "character_pack" : "bundled";
}

function getDefaultLocalOutfit() {
  return findEntry(localOutfits, getProfileDefaultOutfit()) || localOutfits[0] || bundledOutfit;
}

function refreshLocalResourceAssets() {
  const activePackId = getCurrentCharacterPackId();
  runtimeCharacterPackOutfits = buildRuntimeCharacterPackOutfits(activePackId);
  characterPackOutfits = buildCharacterPackOutfits(activePackId);
  localOutfits = buildLocalOutfits();
  if (resourceState.source !== "manifest") {
    resourceState.outfit = findEntry(localOutfits, state.outfit) || getDefaultLocalOutfit();
    resourceState.source = getLocalResourceSource();
    resourceState.loadedAt = Date.now();
  }
}

function buildLocalOutfits() {
  const outfits = [...runtimeCharacterPackOutfits, ...characterPackOutfits];
  return outfits.length ? outfits : [bundledOutfit];
}

function buildRuntimeCharacterPackOutfits(activePackId = getCurrentCharacterPackId()) {
  const pack = runtimeCharacterPacks.find((item) => String(item?.id || item?.packId || "").trim() === activePackId);
  const outfits = Array.isArray(pack?.outfits) ? pack.outfits : [];
  const built = outfits
    .map((outfit) => {
      const outfitId = String(outfit?.id || outfit?.name || "").trim();
      const emotions = (Array.isArray(outfit?.emotions) ? outfit.emotions : [])
        .map((emotion) => {
          const id = String(emotion?.id || emotion?.name || "").trim();
          const path = String(emotion?.path || "").trim();
          let url = "";
          if (path) {
            try {
              url = isTauriRuntime ? convertFileSrc(path) : path;
            } catch (error) {
              console.error("[buildRuntimeCharacterPackOutfits] convertFileSrc failed:", path, error);
            }
          }
          return {
            id,
            name: String(emotion?.name || id).trim(),
            aliases: [],
            url: String(url || "").trim(),
            path
          };
        })
        .filter((emotion) => emotion.id && emotion.url);
      return {
        id: outfitId,
        name: String(outfit?.name || outfitId).trim(),
        aliases: [],
        emotions: sortEmotions(emotions)
      };
    })
    .filter((outfit) => outfit.id && outfit.emotions.length)
    .sort(compareOutfitEntries);
  return built.length ? built : [];
}

function buildCharacterPackOutfits(activePackId = getActiveCharacterPackId()) {
  const grouped = new Map();
  for (const [path, url] of Object.entries(characterPackCharacterAssets)) {
    const match = path.match(/\/characters\/([^/]+)\/assets\/characters\/([^/]+)\/([^/]+)\.(png|jpe?g|webp)$/i);
    if (!match) continue;
    const packId = decodeURIComponent(match[1] || "").trim();
    const outfitId = decodeURIComponent(match[2] || "").trim();
    const emotionId = decodeURIComponent(match[3] || "").trim();
    if (packId !== activePackId) continue;
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
    .sort(compareOutfitEntries);
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

function compareOutfitEntries(a, b) {
  const defaultOutfit = getProfileDefaultOutfit();
  if (a.id === defaultOutfit) return -1;
  if (b.id === defaultOutfit) return 1;
  return String(a.id || "").localeCompare(String(b.id || ""), "zh-CN");
}

function compareEmotionEntries(a, b) {
  const defaultEmotion = getProfileDefaultEmotion();
  if (a.id === defaultEmotion) return -1;
  if (b.id === defaultEmotion) return 1;
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

function normalizeCharacterPackId(value) {
  const requested = String(value || "").trim();
  if (!requested) return getActiveCharacterPackId();
  const normalized = normalizeEntryKey(requested);
  const packs = listCharacterPacks();
  const match =
    packs.find((pack) => pack.id === requested) ||
    packs.find((pack) => normalizeEntryKey(pack.id) === normalized) ||
    packs.find((pack) => pack.characterId === requested) ||
    packs.find((pack) => normalizeEntryKey(pack.characterId) === normalized);
  return String(match?.id || getActiveCharacterPackId()).trim();
}

function normalizeOutfitName(value) {
  return String(value || "").trim() || getProfileDefaultOutfit();
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
