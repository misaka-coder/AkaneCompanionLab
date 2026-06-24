export const CONTROL_CENTER_ACTIONS = Object.freeze({
  chatNew: "chat.new",
  chatStop: "chat.stop",
  workspaceOpen: "workspace.open",
  characterImportZip: "character.importZip",
  characterOpenPackFolder: "character.openPackFolder",
  characterApply: "character.apply",
  characterRefresh: "character.refresh",
  characterPreviewEmotion: "character.previewEmotion",
  characterRestoreDefaults: "character.restoreDefaults",
  characterSelectPack: "character.selectPack",
  characterSetOutfit: "character.setOutfit",
  characterManageOutfits: "character.manageOutfits",
  characterMoreExpressions: "character.moreExpressions",
  characterResourceRepair: "character.resourceRepair",
  voiceTest: "voice.test",
  voiceStop: "voice.stop",
  voiceSetTtsEnabled: "voice.setTtsEnabled",
  voiceSetAsrEnabled: "voice.setAsrEnabled",
  voiceSetVolume: "voice.setVolume",
  voiceSelectTtsVoice: "voice.selectTtsVoice",
  voiceSetSpeed: "voice.setSpeed",
  voiceSelectAsrDevice: "voice.selectAsrDevice",
  voiceSetAsrLanguage: "voice.setAsrLanguage",
  voiceSetAsrSensitivity: "voice.setAsrSensitivity",
  voicePreviewPlay: "voice.previewPlay",
  voiceRecordsClear: "voice.records.clear",
  voiceQueueClear: "voice.queue.clear",
  voiceSetWakeWord: "voice.setWakeWord",
  voiceSetWakeSensitivity: "voice.setWakeSensitivity",
  musicPrevious: "music.previous",
  musicNext: "music.next",
  musicPause: "music.pause",
  musicStop: "music.stop",
  musicClear: "music.clear",
  musicSeek: "music.seek",
  musicSetPlayMode: "music.setPlayMode",
  musicSetMood: "music.setMood",
  musicRefreshRecommendations: "music.refreshRecommendations",
  musicSelectQueueItem: "music.selectQueueItem",
  musicSetVolumeNormalization: "music.setVolumeNormalization",
  musicSelectOutputDevice: "music.selectOutputDevice",
  musicPlayWorkspaceRecommendation: "music.playWorkspaceRecommendation",
  windowNotify: "window.notify",
  windowMinimize: "window.minimize",
  windowMaximize: "window.maximize",
  windowClose: "window.close",
  perceptionDesktopContextSetEnabled: "perception.desktopContext.setEnabled",
  perceptionClipboardContextSetEnabled: "perception.clipboardContext.setEnabled",
  perceptionScreenVisionSetEnabled: "perception.screenVision.setEnabled",
  perceptionScreenVisionSetIntervalSec: "perception.screenVision.setIntervalSec",
  perceptionScreenVisionSetFrameCount: "perception.screenVision.setFrameCount",
  perceptionScreenVisionClear: "perception.screenVision.clear",
  perceptionProactiveWakeSetEnabled: "perception.proactiveWake.setEnabled",
  perceptionProactiveWakeSetIntervalSec: "perception.proactiveWake.setIntervalSec",
  perceptionPrivacyHelp: "perception.privacyHelp",
  perceptionManagePermissions: "perception.managePermissions",
  perceptionActiveWindowDetails: "perception.activeWindow.details",
  perceptionClipboardClear: "perception.clipboard.clear",
  perceptionEventsViewAll: "perception.events.viewAll",
  perceptionSuggestionRun: "perception.suggestion.run",
  perceptionRunDiagnostics: "perception.runDiagnostics",
  abilitiesQuickAction: "abilities.quickAction",
  abilitiesManageModules: "abilities.manageModules",
  abilitiesMoreWorkflows: "abilities.moreWorkflows",
  abilitiesProviderConfigOpen: "abilities.provider.config.open",
  abilitiesProviderConfigSave: "abilities.provider.config.save",
  abilitiesProviderHealthCheck: "abilities.provider.healthCheck",
  abilitiesProviderTtsTest: "abilities.provider.ttsTest",
  abilitiesProviderVoiceProfileInspectFolder: "abilities.provider.voiceProfile.inspectFolder",
  abilitiesProviderVoiceProfileSave: "abilities.provider.voiceProfile.save",
  abilitiesProviderVoiceProfileAssignToCurrentCharacter: "abilities.provider.voiceProfile.assignToCurrentCharacter",
  abilitiesProviderVoiceProfileClearCurrentCharacter: "abilities.provider.voiceProfile.clearCurrentCharacter",
  abilitiesMcpConfigOpen: "abilities.mcp.config.open",
  abilitiesMcpConfigSave: "abilities.mcp.config.save",
  abilitiesMcpDiscover: "abilities.mcp.discover",
  abilitiesApprovalPolicySave: "abilities.approvalPolicy.save",
  abilitiesWorkflowConfigOpen: "abilities.workflow.config.open",
  abilitiesWorkflowConfigSave: "abilities.workflow.config.save",
  abilitiesWorkflowFileImport: "abilities.workflow.file.import",
  abilitiesWorkflowValidate: "abilities.workflow.validate",
  abilitiesQqSelfCheck: "abilities.qq.selfCheck",
  abilitiesLogsViewAll: "abilities.logs.viewAll",
  abilitiesSafetyDetails: "abilities.safety.details",
  abilitiesLive2dOpenSettings: "abilities.live2d.openSettings",
  advancedProbeClickThrough: "advanced.probeClickThrough",
  advancedResetWindow: "advanced.resetWindow",
  advancedToggleWebgl: "advanced.toggleWebgl",
  advancedSetHitTestEnabled: "advanced.setHitTestEnabled",
  advancedSetHitboxOverlay: "advanced.setHitboxOverlay",
  advancedLogsClear: "advanced.logs.clear",
  advancedLogsMore: "advanced.logs.more",
  advancedExitPet: "advanced.exitPet",
  advancedExpertOption: "advanced.expertOption",
  advancedLive2dOpenStatus: "advanced.live2d.openStatus",
  advancedAbilityDetails: "advanced.ability.details"
});

export const CONTROL_CENTER_BRIDGED_ACTION_IDS = Object.freeze([
  CONTROL_CENTER_ACTIONS.chatNew,
  CONTROL_CENTER_ACTIONS.chatStop,
  CONTROL_CENTER_ACTIONS.workspaceOpen,
  CONTROL_CENTER_ACTIONS.voiceTest,
  CONTROL_CENTER_ACTIONS.voiceStop,
  CONTROL_CENTER_ACTIONS.voiceSetTtsEnabled,
  CONTROL_CENTER_ACTIONS.voiceSetAsrEnabled,
  CONTROL_CENTER_ACTIONS.voiceSetVolume,
  CONTROL_CENTER_ACTIONS.voicePreviewPlay,
  CONTROL_CENTER_ACTIONS.voiceSetSpeed,
  CONTROL_CENTER_ACTIONS.voiceSetWakeWord,
  CONTROL_CENTER_ACTIONS.voiceSetWakeSensitivity,
  CONTROL_CENTER_ACTIONS.characterOpenPackFolder,
  CONTROL_CENTER_ACTIONS.characterRefresh,
  CONTROL_CENTER_ACTIONS.characterPreviewEmotion,
  CONTROL_CENTER_ACTIONS.characterSelectPack,
  CONTROL_CENTER_ACTIONS.characterSetOutfit,
  CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetIntervalSec,
  CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetFrameCount,
  CONTROL_CENTER_ACTIONS.perceptionScreenVisionClear,
  CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec,
  CONTROL_CENTER_ACTIONS.perceptionRunDiagnostics,
  CONTROL_CENTER_ACTIONS.windowClose,
  CONTROL_CENTER_ACTIONS.windowMinimize,
  CONTROL_CENTER_ACTIONS.windowMaximize,
  CONTROL_CENTER_ACTIONS.advancedProbeClickThrough,
  CONTROL_CENTER_ACTIONS.advancedResetWindow,
  CONTROL_CENTER_ACTIONS.advancedToggleWebgl,
  CONTROL_CENTER_ACTIONS.advancedSetHitTestEnabled,
  CONTROL_CENTER_ACTIONS.advancedSetHitboxOverlay,
  CONTROL_CENTER_ACTIONS.musicPrevious,
  CONTROL_CENTER_ACTIONS.musicNext,
  CONTROL_CENTER_ACTIONS.musicPause,
  CONTROL_CENTER_ACTIONS.musicStop,
  CONTROL_CENTER_ACTIONS.musicClear,
  CONTROL_CENTER_ACTIONS.musicSeek,
  CONTROL_CENTER_ACTIONS.musicSelectQueueItem,
  CONTROL_CENTER_ACTIONS.musicSetPlayMode,
  CONTROL_CENTER_ACTIONS.musicSetVolumeNormalization,
  CONTROL_CENTER_ACTIONS.musicPlayWorkspaceRecommendation,
  CONTROL_CENTER_ACTIONS.abilitiesProviderConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesProviderHealthCheck,
  CONTROL_CENTER_ACTIONS.abilitiesProviderTtsTest,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileInspectFolder,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileSave,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileAssignToCurrentCharacter,
  CONTROL_CENTER_ACTIONS.abilitiesProviderVoiceProfileClearCurrentCharacter,
  CONTROL_CENTER_ACTIONS.abilitiesMcpConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesMcpDiscover,
  CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate,
  CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck
]);

const bridgedActionIds = new Set(CONTROL_CENTER_BRIDGED_ACTION_IDS);

export function createControlCenterActionRouter(options = {}) {
  const handlers = new Map();
  const dataSource = options.dataSource;
  const logger = options.logger || console;
  let onAfterAction = typeof options.onAfterAction === "function" ? options.onAfterAction : null;

  const router = {
    register(actionId, handler) {
      const normalizedActionId = normalizeActionId(actionId);
      if (!normalizedActionId || typeof handler !== "function") {
        return () => {};
      }
      handlers.set(normalizedActionId, handler);
      return () => handlers.delete(normalizedActionId);
    },

    registerHandlers(nextHandlers) {
      return registerControlCenterActionHandlers(router, nextHandlers);
    },

    setAfterActionHook(nextHook) {
      onAfterAction = typeof nextHook === "function" ? nextHook : null;
      return () => {
        if (onAfterAction === nextHook) onAfterAction = null;
      };
    },

    async run(actionId, payload = {}, context = {}) {
      const normalizedActionId = normalizeActionId(actionId);
      if (!normalizedActionId) {
        return { ok: false, status: "missing-action-id" };
      }

      let result;
      try {
        const handler = handlers.get(normalizedActionId);
        if (handler) {
          result = await handler(payload, context);
        } else if (shouldRouteToDataSource(dataSource, normalizedActionId, options)) {
          result = await dataSource.runAction(normalizedActionId, payload, context);
        } else if (shouldReturnNotImplemented(dataSource, normalizedActionId)) {
          result = createNotImplementedActionResult(normalizedActionId);
        } else {
          logger.info?.("[control-center] action", normalizedActionId, payload, context);
          result = { ok: true, status: "noop", actionId: normalizedActionId, payload };
        }
      } catch (error) {
        result = {
          ok: false,
          status: "failed",
          actionId: normalizedActionId,
          payload,
          error: formatActionError(error),
          refresh: true
        };
      }

      const normalizedResult = normalizeActionResult(result, normalizedActionId, payload);
      await notifyAfterAction(onAfterAction, normalizedResult, payload, context);
      return normalizedResult;
    }
  };

  if (options.handlers) {
    registerControlCenterActionHandlers(router, options.handlers);
  }

  return router;
}

export function registerControlCenterActionHandlers(router, handlers = {}) {
  if (!router || typeof router.register !== "function") {
    return () => {};
  }

  const disposers = [];
  for (const [actionId, handler] of normalizeHandlerEntries(handlers)) {
    disposers.push(router.register(actionId, handler));
  }

  return () => {
    for (const dispose of disposers.splice(0)) {
      dispose();
    }
  };
}

export function isControlCenterBridgedAction(actionId) {
  return bridgedActionIds.has(normalizeActionId(actionId));
}

export function createNotImplementedActionResult(actionId) {
  return {
    ok: false,
    status: "not-implemented",
    actionId: normalizeActionId(actionId),
    refresh: false
  };
}

function shouldRouteToDataSource(dataSource, actionId, options) {
  if (typeof dataSource?.runAction !== "function") return false;
  if (options.forwardUnknownActions) return true;
  if (dataSource.kind === "mock") return true;
  if (typeof dataSource.handlesAction === "function") {
    return Boolean(dataSource.handlesAction(actionId));
  }
  return isControlCenterBridgedAction(actionId);
}

function shouldReturnNotImplemented(dataSource, actionId) {
  return isControlCenterBridgedAction(actionId) || Boolean(dataSource && dataSource.kind !== "mock");
}

function normalizeActionResult(result, actionId, payload) {
  if (!result || typeof result !== "object") {
    return { ok: true, status: "handled", actionId, payload, refresh: true };
  }

  const normalized = {
    ...result,
    actionId: normalizeActionId(result.actionId || actionId)
  };

  if (normalized.status === "not-implemented") {
    return normalized.refresh === undefined
      ? { ...normalized, refresh: false }
      : { ...normalized, refresh: Boolean(normalized.refresh) };
  }

  return {
    ...normalized,
    refresh: normalized.refresh === undefined ? true : Boolean(normalized.refresh)
  };
}

async function notifyAfterAction(onAfterAction, result, payload, context) {
  if (typeof onAfterAction !== "function") return;
  try {
    await onAfterAction(result, { payload, context });
  } catch {
    // Action hooks are advisory and should not make the action itself fail.
  }
}

function normalizeHandlerEntries(handlers) {
  if (handlers instanceof Map) return handlers.entries();
  if (Array.isArray(handlers)) return handlers;
  if (handlers && typeof handlers === "object") return Object.entries(handlers);
  return [];
}

function normalizeActionId(actionId) {
  return String(actionId || "").trim();
}

function formatActionError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}
