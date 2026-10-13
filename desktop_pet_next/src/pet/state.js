import { getActiveCharacterPackId } from "../character-profile.js";
import { SCREEN_OBSERVATION_DEFAULTS, normalizeScreenObservationSettings } from "../screen-observation.js";
import { cloneCareState } from "../care-feature.js";
import { LOCAL_DEFAULT_INSTANCE_ID, DEFAULT_BACKEND_URL, PROFILE_USER_ID, PROACTIVE_WAKE_DEFAULT_SEC, MUSIC_PLAY_MODES, SCALE_MIN, SCALE_MAX, PROACTIVE_WAKE_MIN_SEC, PROACTIVE_WAKE_MAX_SEC } from './constants.js';
import { getProfileDefaultOutfit, getProfileDefaultEmotion, normalizeCharacterPackId } from './profile.js';
import { clamp, normalizeBackendUrl, generateSessionId, formatError } from './utils.js';

/** Owns state behavior and its temporary runtime state. */
export function createStateController({
  state,
  platform: platformPort,
  lifecycle,
  scope: runtimeScope,
  status: statusPort,
  settings: settingsPort,
  window: windowPort,
  character: characterPort
}) {
  const DEFAULT_STATE = {
    instanceId: LOCAL_DEFAULT_INSTANCE_ID,
    hostId: LOCAL_DEFAULT_INSTANCE_ID,
    boundBotId: LOCAL_DEFAULT_INSTANCE_ID,
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
    screenVisionEnabled: false,
    ...SCREEN_OBSERVATION_DEFAULTS,
    proactiveWakeEnabled: false,
    proactiveWakeIntervalSec: PROACTIVE_WAKE_DEFAULT_SEC,
    hitTestEnabled: true,
    hitboxOverlay: false,
    care: null,
    musicPlayMode: MUSIC_PLAY_MODES[0],
    musicVolumeNormalization: true
  };
  Object.assign(state, { ...DEFAULT_STATE, characters: {} });

  function getProfileUserId() {
    return state.profileUserId || PROFILE_USER_ID;
  }

  let saveTimer = 0;

  function normalizeState(value) {
    // Ignore retired preferences when loading older state.
    const {
      screenVisionMode: _retiredMode, screenVisionIntervalSec: _retiredInterval,
      desktopContextEnabled: _retiredDesktopContext, clipboardContextEnabled: _retiredClipboard,
      voiceSpeed: _retiredVoiceSpeed, wakeWord: _retiredWakeWord, wakeSensitivity: _retiredWakeSensitivity,
      ...incoming
    } = value ?? {};
    const scale = clamp(Number(incoming.scale ?? DEFAULT_STATE.scale), SCALE_MIN, SCALE_MAX);
    const instanceId = String(incoming.instanceId || "").trim() || LOCAL_DEFAULT_INSTANCE_ID;
    return {
      ...DEFAULT_STATE,
      ...incoming,
      instanceId,
      hostId: String(incoming.hostId || instanceId).trim() || instanceId,
      boundBotId: String(incoming.boundBotId || instanceId).trim() || instanceId,
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
      screenVisionEnabled: Boolean(incoming.screenVisionEnabled ?? DEFAULT_STATE.screenVisionEnabled),
      ...normalizeScreenObservationSettings(incoming),
      proactiveWakeEnabled: Boolean(incoming.proactiveWakeEnabled ?? DEFAULT_STATE.proactiveWakeEnabled),
      proactiveWakeIntervalSec: normalizeProactiveWakeIntervalSec(
        incoming.proactiveWakeIntervalSec ?? DEFAULT_STATE.proactiveWakeIntervalSec
      ),
      hitTestEnabled: Boolean(incoming.hitTestEnabled ?? DEFAULT_STATE.hitTestEnabled),
      hitboxOverlay: Boolean(incoming.hitboxOverlay ?? DEFAULT_STATE.hitboxOverlay),
      care: cloneCareState(incoming.care),
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
      care: cloneCareState(value.care),
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

  function normalizeProactiveWakeIntervalSec(value) {
    const number = Number(value);
    return Math.round(clamp(Number.isFinite(number) && number > 0 ? number : PROACTIVE_WAKE_DEFAULT_SEC, PROACTIVE_WAKE_MIN_SEC, PROACTIVE_WAKE_MAX_SEC));
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

  function setRestoreLatestOnStartup(enabled) {
    state.restoreLatestOnStartup = Boolean(enabled);
    scheduleSave(0);
    statusPort.setRuntimeStatus(state.restoreLatestOnStartup ? "启动时会恢复上一轮" : "启动时不恢复上一轮", {
      mode: "idle"
    });
    settingsPort.scheduleSettingsSnapshot();
  }

  function scheduleSave(delay = 500) {
    if (!platformPort.isTauriRuntime) return;
    lifecycle.clearTimeout(saveTimer);
    saveTimer = lifecycle.setTimeout(saveNow, delay);
  }

  async function saveNow() {
    const requestScope = runtimeScope.capture();
    if (!platformPort.isTauriRuntime) return;
    try {
      const geometry = await platformPort.invoke("get_window_geometry");
      if (!runtimeScope.isCurrent(requestScope)) return false;
      windowPort.applyWindowGeometryToState(geometry);
      characterPort.persistCurrentCharacterRuntimeState();
      await platformPort.invoke("save_pet_state", {
        state: {
          ...state,
          care: null,
          characters: characterPort.buildCharacterRuntimeSnapshot()
        }
      });
      if (!runtimeScope.isCurrent(requestScope)) return false;
    } catch (error) {
      if (!runtimeScope.isCurrent(requestScope)) return false;
      statusPort.setStatus(`Save failed: ${formatError(error)}`);
    }
  }

  return {
    get DEFAULT_STATE() { return DEFAULT_STATE; },
    getProfileUserId,
    normalizeState,
    normalizeCharacterRuntimeState,
    normalizeNullableInteger,
    normalizePositiveInteger,
    normalizeProactiveWakeIntervalSec,
    normalizeMusicPlayMode,
    normalizeBooleanSetting,
    setRestoreLatestOnStartup,
    scheduleSave,
    saveNow
  };
}
