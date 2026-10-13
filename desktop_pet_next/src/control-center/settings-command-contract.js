import { CONTROL_CENTER_ACTIONS } from "./action-router.js";
import { SCREEN_OBSERVATION_COMMANDS } from "../screen-observation.js";

export const DEFAULT_CONFIRMATION_TIMEOUT_MS = 1800;

const screenObservationActionSpecs = Object.fromEntries(
  Object.entries(SCREEN_OBSERVATION_COMMANDS).map(([id, spec]) => [
    id,
    Object.freeze({
      command: spec.command,
      observed: true,
      ...(id === "perception.screenVision.setEnabled"
        ? { timeoutMs: payload => (payload?.value ? 60000 : DEFAULT_CONFIRMATION_TIMEOUT_MS) }
        : {})
    })
  ])
);

const explicitSettingsActionSpecs = Object.freeze({
  [CONTROL_CENTER_ACTIONS.settingsSelectBot]: { command: "setBoundBot", observed: true },
  [CONTROL_CENTER_ACTIONS.chatSend]: { command: "sendChatMessage", observed: true },
  [CONTROL_CENTER_ACTIONS.chatStop]: { command: "stopReply", observed: true },
  [CONTROL_CENTER_ACTIONS.chatAttach]: { command: "attachChatFiles", observed: true, timeoutMs: 180000 },
  [CONTROL_CENTER_ACTIONS.chatRemoveAttachment]: { command: "removeChatAttachment", observed: true },
  [CONTROL_CENTER_ACTIONS.chatPlayAttachment]: { command: "playChatAttachment", observed: true, timeoutMs: 30000 },
  [CONTROL_CENTER_ACTIONS.chatFileAction]: { command: "chatFileAction", observed: true, timeoutMs: 60000 },
  [CONTROL_CENTER_ACTIONS.voiceTest]: { command: "testTts", observed: true, timeoutMs: 12000 },
  [CONTROL_CENTER_ACTIONS.voiceStop]: { command: "stopTts", observed: true },
  [CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled]: { command: "setVoiceEnabled", observed: true },
  [CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled]: { command: "setVoiceInputEnabled", observed: true },
  [CONTROL_CENTER_ACTIONS.voiceSetVolume]: { command: "setVoiceVolume", observed: true },
  [CONTROL_CENTER_ACTIONS.voicePreviewPlay]: { command: "previewTts", observed: true, timeoutMs: 12000 },
  [CONTROL_CENTER_ACTIONS.characterRefresh]: { command: "reloadResources", observed: true },
  [CONTROL_CENTER_ACTIONS.characterPreviewEmotion]: { command: "previewEmotion", observed: true },
  [CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics]: { command: "requestSnapshot", observed: true },
  [CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled]: { command: "setHitTestEnabled", observed: true },
  [CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay]: { command: "setHitboxOverlay", observed: true },
  [CONTROL_CENTER_ACTIONS.characterSelectPack]: { command: "setCharacterPack", observed: true, timeoutMs: 4000 },
  [CONTROL_CENTER_ACTIONS.characterSetOutfit]: { command: "setOutfit", observed: true },
  [CONTROL_CENTER_ACTIONS.musicPrevious]: { command: "controlActiveMusic", observed: true, timeoutMs: 4000 },
  [CONTROL_CENTER_ACTIONS.musicNext]: { command: "controlActiveMusic", observed: true, timeoutMs: 4000 },
  [CONTROL_CENTER_ACTIONS.musicTogglePlayback]: { command: "controlActiveMusic", observed: true, timeoutMs: 4000 },
  [CONTROL_CENTER_ACTIONS.musicStop]: { command: "controlActiveMusic", observed: true, timeoutMs: 4000 },

  [CONTROL_CENTER_ACTIONS.workspaceOpen]: { command: "openWorkspace", observed: false },
  [CONTROL_CENTER_ACTIONS.advancedProbeClickThrough]: { command: "probeClickThrough", observed: false },
  [CONTROL_CENTER_ACTIONS.advancedResetWindow]: { command: "resetWindow", observed: false },
  [CONTROL_CENTER_ACTIONS.advancedToggleWebgl]: { command: "toggleWebgl", observed: false },
  [CONTROL_CENTER_ACTIONS.musicClear]: { command: "clearMusicQueue", observed: false },
  [CONTROL_CENTER_ACTIONS.musicSetPlayMode]: { command: "setMusicPlayMode", observed: false },
  [CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization]: { command: "setMusicVolumeNormalization", observed: false },
  [CONTROL_CENTER_ACTIONS.musicSeek]: { command: "seekMusic", observed: false },
  [CONTROL_CENTER_ACTIONS.musicSelectQueueItem]: { command: "playMusicTrack", observed: false },
  [CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation]: { command: "playWorkspaceAudio", observed: false }
});

export const SETTINGS_ACTION_SPECS = Object.freeze({
  ...explicitSettingsActionSpecs,
  ...screenObservationActionSpecs
});

export const SETTINGS_COMMAND_BY_ACTION_ID = Object.freeze(
  Object.fromEntries(
    Object.entries(SETTINGS_ACTION_SPECS).map(([actionId, spec]) => [actionId, spec.command])
  )
);

export const OBSERVED_SETTINGS_ACTION_IDS = Object.freeze(
  new Set(
    Object.entries(SETTINGS_ACTION_SPECS)
      .filter(([, spec]) => spec.observed)
      .map(([actionId]) => actionId)
  )
);

export function getSettingsActionTimeout(actionId, payload) {
  const spec = SETTINGS_ACTION_SPECS[actionId];
  if (!spec) return DEFAULT_CONFIRMATION_TIMEOUT_MS;
  if (typeof spec.timeoutMs === "function") {
    return spec.timeoutMs(payload);
  }
  if (typeof spec.timeoutMs === "number") {
    return spec.timeoutMs;
  }
  return DEFAULT_CONFIRMATION_TIMEOUT_MS;
}
