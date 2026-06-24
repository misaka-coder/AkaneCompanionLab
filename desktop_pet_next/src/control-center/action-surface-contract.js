import {
  CONTROL_CENTER_ACTIONS,
  CONTROL_CENTER_BRIDGED_ACTION_IDS
} from "./action-router.js";

export const CONTROL_CENTER_ACTION_SURFACE_STATUS = Object.freeze({
  bridged: "bridged",
  clientHandled: "client-handled",
  deferred: "deferred"
});

export const CONTROL_CENTER_CLIENT_HANDLED_ACTION_IDS = Object.freeze([
  CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails,
  CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll,
  CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen,
  CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen,
  CONTROL_CENTER_ACTIONS.advancedLogsMore
]);

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
  bridged("character", CONTROL_CENTER_ACTIONS.characterSelectPack, "settings-command", "setCharacterPack"),
  bridged("character", CONTROL_CENTER_ACTIONS.characterSetOutfit, "settings-command", "setOutfit"),

  bridged("voice", CONTROL_CENTER_ACTIONS.voiceTest, "settings-command", "testTts"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceStop, "settings-command", "stopTts"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceSetVolume, "settings-command", "setVoiceVolume"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voicePreviewPlay, "settings-command", "previewTts"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceSetSpeed, "settings-command", "setVoiceSpeed"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceSetWakeWord, "settings-command", "setWakeWord"),
  bridged("voice", CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity, "settings-command", "setWakeSensitivity"),

  bridged("music", CONTROL_CENTER_ACTIONS.musicPrevious, "settings-command", "previousMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicNext, "settings-command", "nextMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicPause, "settings-command", "toggleMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicStop, "settings-command", "stopMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicClear, "settings-command", "clearMusicQueue"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicSeek, "settings-command", "seekMusic"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicSelectQueueItem, "settings-command", "playMusicTrack"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicSetPlayMode, "settings-command", "setMusicPlayMode"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization, "settings-command", "setMusicVolumeNormalization"),
  bridged("music", CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation, "settings-command", "playWorkspaceAudio"),

  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled, "settings-command", "setDesktopContextEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled, "settings-command", "setClipboardContextEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled, "settings-command", "setScreenVisionEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec, "settings-command", "setScreenVisionIntervalSec"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount, "settings-command", "setScreenVisionFrameCount"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear, "settings-command", "clearScreenVision"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled, "settings-command", "setProactiveWakeEnabled"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec, "settings-command", "setProactiveWakeIntervalSec"),
  bridged("perception", CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics, "settings-command", "requestSnapshot"),

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
  deferred("character", CONTROL_CENTER_ACTIONS.characterManageOutfits, "Requires outfit gallery or management route."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterMoreExpressions, "Requires expression gallery or pagination route."),
  deferred("character", CONTROL_CENTER_ACTIONS.characterResourceRepair, "Requires resource validation and repair action."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSelectTtsVoice, "Requires voice catalog and selected voice payload."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSelectAsrDevice, "Requires device id contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetAsrLanguage, "Requires supported language contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceSetAsrSensitivity, "Requires sensitivity range contract."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceRecordsClear, "Requires recognition-log storage boundary."),
  deferred("voice", CONTROL_CENTER_ACTIONS.voiceQueueClear, "Requires synthesis queue ownership."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSetMood, "Requires mood to playback request contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicRefreshRecommendations, "Requires recommendation source contract."),
  deferred("music", CONTROL_CENTER_ACTIONS.musicSelectOutputDevice, "Requires output device id contract."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionPrivacyHelp, "Navigation/help surface, not a desktop command yet."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionManagePermissions, "Requires permission management boundary."),
  clientHandled("perception", CONTROL_CENTER_ACTIONS.perceptionActiveWindowDetails, "Local toggle: expands active-window card details."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionClipboardClear, "Requires clipboard history ownership."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionEventsViewAll, "Requires sensing-event log route."),
  deferred("perception", CONTROL_CENTER_ACTIONS.perceptionSuggestionRun, "Requires suggestion action payload."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesQuickAction, "Requires capability invocation payload."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesManageModules, "Requires capability management route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesMoreWorkflows, "Requires workflow catalog route."),
  clientHandled("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderConfigOpen, "Local toggle: expands local provider configuration details."),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave, "backend-route", "POST /capabilities/providers/{providerId}/config"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck, "backend-route", "POST /capabilities/providers/{providerId}/health-check"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest, "backend-route", "POST /capabilities/providers/{providerId}/tts-test"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder, "backend-route", "POST /capabilities/providers/{providerId}/voice-profiles/inspect-folder"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave, "backend-route", "POST /capabilities/providers/{providerId}/voice-profiles/{voiceProfileId}/config"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter, "tauri-invoke", "set_character_voice_profile"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter, "tauri-invoke", "clear_character_voice_profile"),
  clientHandled("abilities", CONTROL_CENTER_ACTIONS.abilitiesMcpConfigOpen, "Local toggle: expands MCP server configuration details."),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave, "backend-route", "POST /capabilities/mcp-servers/{serverId}/config"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover, "backend-route", "POST /capabilities/mcp-servers/{serverId}/discover"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave, "backend-route", "POST /capabilities/approval-policy"),
  clientHandled("abilities", CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigOpen, "Local toggle: expands local workflow binding details."),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave, "backend-route", "POST /capabilities/workflows/{workflowId}/config"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport, "backend-route", "POST /capabilities/workflows/{workflowId}/file"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate, "backend-route", "POST /capabilities/workflows/{workflowId}/validate"),
  bridged("abilities", CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck, "backend-route", "POST /api/qq/self-check"),
  clientHandled("abilities", CONTROL_CENTER_ACTIONS.abilitiesLogsViewAll, "Local toggle: expands ability call history rows."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesSafetyDetails, "Requires policy detail route."),
  deferred("abilities", CONTROL_CENTER_ACTIONS.abilitiesLive2dOpenSettings, "Requires Live2D settings owner."),
  deferred("advanced", CONTROL_CENTER_ACTIONS.advancedLogsClear, "Requires log storage owner and confirmation semantics."),
  clientHandled("advanced", CONTROL_CENTER_ACTIONS.advancedLogsMore, "Local toggle: expands diagnostics log entries."),
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

function clientHandled(page, actionId, description) {
  return Object.freeze({
    page,
    actionId,
    description,
    status: CONTROL_CENTER_ACTION_SURFACE_STATUS.clientHandled,
    bridged: false
  });
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
