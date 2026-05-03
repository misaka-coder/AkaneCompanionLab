import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS
} from "./action-router.js";

export const CONTROL_CENTER_ACTION_SURFACE_STATUS = Object.freeze({
  bridged: "bridged",
  deferred: "deferred"
});

const bridgedActionIds = new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS);

export const CONTROL_CENTER_ACTION_SURFACES = Object.freeze([
  bridged("overview", CONTROL_CENTER_ACTIONS.chatNew, "settings-command", "newSession"),
  bridged("overview", CONTROL_CENTER_ACTIONS.chatStop, "settings-command", "stopReply"),
  bridged("overview", CONTROL_CENTER_ACTIONS.workspaceOpen, "tauri-invoke", "open_workspace_window"),
  bridged("overview", CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled, "settings-command", "setVoiceEnabled"),
  bridged("overview", CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled, "settings-command", "setVoiceInputEnabled"),
  bridged("overview", CONTROL_CENTER_ACTIONS.musicPrevious, "settings-command", "previousMusic"),
  bridged("overview", CONTROL_CENTER_ACTIONS.musicNext, "settings-command", "nextMusic"),
  bridged("overview", CONTROL_CENTER_ACTIONS.musicPause, "settings-command", "toggleMusic"),
  bridged("overview", CONTROL_CENTER_ACTIONS.musicStop, "settings-command", "stopMusic"),
  bridged("overview", CONTROL_CENTER_ACTIONS.musicClear, "settings-command", "clearMusicQueue"),
  bridged("overview", CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled, "settings-command", "setDesktopContextEnabled"),
  bridged("overview", CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled, "settings-command", "setClipboardContextEnabled"),
  bridged("overview", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled, "settings-command", "setScreenVisionEnabled"),
  bridged("overview", CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled, "settings-command", "setProactiveWakeEnabled"),

  bridged("character", CONTROL_CENTER_ACTIONS.characterOpenPackFolder, "tauri-invoke", "open_character_packs_folder"),
  bridged("character", CONTROL_CENTER_ACTIONS.characterRefresh, "settings-command", "reloadResources"),
  bridged("character", CONTROL_CENTER_ACTIONS.characterPreviewEmotion, "settings-command", "previewEmotion"),

  bridged("voice", CONTROL_CENTER_ACTIONS.voiceTest, "settings-command", "testTts"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceStop, "settings-command", "stopTts"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceSetVolume, "settings-command", "setVoiceVolume"),

  bridged("music", CONTROL_CENTER_ACTIONS.musicPrevious, "settings-command", "previousMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicNext, "settings-command", "nextMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicPause, "settings-command", "toggleMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicStop, "settings-command", "stopMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicClear, "settings-command", "clearMusicQueue"),

  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled, "settings-command", "setDesktopContextEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled, "settings-command", "setClipboardContextEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled, "settings-command", "setScreenVisionEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec, "settings-command", "setScreenVisionIntervalSec"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount, "settings-command", "setScreenVisionFrameCount"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear, "settings-command", "clearScreenVision"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled, "settings-command", "setProactiveWakeEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec, "settings-command", "setProactiveWakeIntervalSec"),

  bridged("window", CONTROL_CENTER_ACTIONS.windowClose, "tauri-invoke", "close_window"),
  bridged("window", CONTROL_CENTER_ACTIONS.windowMinimize, "tauri-window", "minimize"),
  bridged("window", CONTROL_CENTER_ACTIONS.windowMaximize, "tauri-window", "toggleMaximize"),

  bridged("advanced", CONTROL_CENTER_ACTIONS.advancedProbeClickThrough, "settings-command", "probeClickThrough"),
  bridged("advanced", CONTROL_CENTER_ACTIONS.advancedResetWindow, "settings-command", "resetWindow"),
  bridged("advanced", CONTROL_CENTER_ACTIONS.advancedToggleWebgl, "settings-command", "toggleWebgl"),
  bridged("advanced", CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled, "settings-command", "setHitTestEnabled"),
  bridged("advanced", CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay, "settings-command", "setHitboxOverlay"),

  deferred("window", CONTROL_CENTER_ACTIONS.windowNotify, "No stable notification command yet."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterImportZip, "Requires file picker and zip bytes contract."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterApply, "Requires stable payload and apply/preview semantics."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterRestoreDefaults, "Requires confirmation and restore scope contract."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterSelectPack, "Requires pack id selection contract."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterSetOutfit, "Requires preview vs apply semantics."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterManageOutfits, "Requires outfit gallery or management route."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterMoreExpressions, "Requires expression gallery or pagination route."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterResourceRepair, "Requires resource validation and repair action."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSelectTtsVoice, "Requires voice catalog and selected voice payload."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetSpeed, "Requires speed range and runtime ownership."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSelectAsrDevice, "Requires device id contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetAsrLanguage, "Requires supported language contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetAsrSensitivity, "Requires sensitivity range contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voicePreviewPlay, "Requires preview playback command boundary."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceRecordsClear, "Requires recognition-log storage boundary."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceQueueClear, "Requires synthesis queue ownership."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetWakeWord, "Requires wake-word settings contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity, "Requires wake word sensitivity range contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSetPlayMode, "Requires playback mode enum and ownership."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSetMood, "Requires mood to playback request contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicRefreshRecommendations, "Requires recommendation source contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSelectQueueItem, "Requires track id and queue ownership."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization, "Requires audio output settings contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSelectOutputDevice, "Requires output device id contract."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionPrivacyHelp, "Navigation/help surface, not a desktop command yet."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionManagePermissions, "Requires permission management boundary."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails, "Requires active-window detail route or modal data."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionClipboardClear, "Requires clipboard history ownership."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionEventsViewAll, "Requires sensing-event log route."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionSuggestionRun, "Requires suggestion action payload."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics, "Requires diagnostics command boundary."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesQuickAction, "Requires capability invocation payload."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesManageModules, "Requires capability management route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesMoreWorkflows, "Requires workflow catalog route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll, "Requires ability invocation log route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesSafetyDetails, "Requires policy detail route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesLive2dOpenSettings, "Requires Live2D settings owner."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedLogsClear, "Requires log storage owner and confirmation semantics."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedLogsMore, "Requires diagnostics log route."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedExitPet, "Destructive action; requires explicit confirmation and ownership decision."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedExpertOption, "Requires per-option command contracts."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedLive2dOpenStatus, "Requires Live2D runtime status route."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedAbilityDetails, "Requires ability detail or drill-down route.")
]);

export const CONTROL_CENTER_DEFERRED_ACTION_SURFACES = Object.freeze(
  CONTROL_CENTER_ACTION_SURFACES.filter((surface) => surface.status === CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred)
);

export function listControlCenterActionSurfaces(status) {
  if (!status) return [...CONTROL_CENTER_ACTION_SURFACES];
  return CONTROL_CENTER_ACTION_SURFACES.filter((surface) => surface.status === status);
}

export function getControlCenterActionSurface(actionId) {
  return CONTROL_CENTER_ACTION_SURFACES.find((surface) => surface.actionId === actionId) || null;
}

export function getUncataloguedBridgedActionIds() {
  const catalogued = new Set(
    CONTROL_CENTER_ACTION_SURFACES
      .filter((surface) => surface.status === CONTROL_CENTER_ACTION_SURFACE_STATUS.bridged)
      .map((surface) => surface.actionId)
  );
  return CONTROL_CENTER_BRIDGED_ACTION_IDS.filter((actionId) => !catalogued.has(actionId));
}

function bridged(page, actionId, boundary, command) {
  return Object.freeze({
    page,
    actionId,
    boundary,
    command,
    status: CONTROL_CENTER_ACTION_SURFACE_STATUS.bridged,
    bridged: bridgedActionIds.has(actionId)
  });
}

function deferred(page, actionId, reason) {
  return Object.freeze({
    page,
    actionId,
    reason,
    status: CONTROL_CENTER_ACTION_SURFACE_STATUS.deferred,
    bridged: false
  });
}
