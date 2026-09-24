import { SCREEN_OBSERVATION_COMMANDS } from "../screen-observation.js";

export const CONTROL_CENTER_ACTIONS = Object.freeze({
  settingsSelectBot: "settings.selectBot",
  chatNew: "chat.new",
  chatSend: "chat.send",
  chatStop: "chat.stop",
  chatJobControl: "chat.jobControl",
  chatAttach: "chat.attach",
  chatRemoveAttachment: "chat.removeAttachment",
  chatPlayAttachment: "chat.playAttachment",
  chatFileAction: "chat.fileAction",
  workspaceOpen: "workspace.open",
  characterOpenWorkshop: "character.openWorkshop",
  characterOpenPackFolder: "character.openPackFolder",
  characterRefresh: "character.refresh",
  characterPreviewEmotion: "character.previewEmotion",
  characterSelectPack: "character.selectPack",
  characterSetOutfit: "character.setOutfit",
  voiceTest: "voice.test",
  voiceStop: "voice.stop",
  voiceSetTtsEnabled: "voice.setTtsEnabled",
  voiceSetAsrEnabled: "voice.setAsrEnabled",
  voiceSetVolume: "voice.setVolume",
  voiceSetSpeed: "voice.setSpeed",
  voicePreviewPlay: "voice.previewPlay",
  voiceSetWakeWord: "voice.setWakeWord",
  voiceSetWakeSensitivity: "voice.setWakeSensitivity",
  musicPrevious: "music.previous",
  musicNext: "music.next",
  musicTogglePlayback: "music.togglePlayback",
  musicStop: "music.stop",
  musicClear: "music.clear",
  musicSeek: "music.seek",
  musicSetPlayMode: "music.setPlayMode",
  musicSelectQueueItem: "music.selectQueueItem",
  musicSetVolumeNormalization: "music.setVolumeNormalization",
  musicPlayWorkspaceRecommendation: "music.playWorkspaceRecommendation",
  windowMinimize: "window.minimize",
  windowMaximize: "window.maximize",
  windowClose: "window.close",
  perceptionScreenVisionSetEnabled: "perception.screenVision.setEnabled",
  perceptionScreenVisionSetSampleIntervalSec: "perception.screenVision.setSampleIntervalSec",
  perceptionScreenVisionSetWindowSec: "perception.screenVision.setWindowSec",
  perceptionScreenVisionSetMaxEdge: "perception.screenVision.setMaxEdge",
  perceptionScreenVisionSetPacking: "perception.screenVision.setPacking",
  perceptionScreenVisionSetSheetCount: "perception.screenVision.setSheetCount",
  perceptionScreenVisionSetFrameCount: "perception.screenVision.setFrameCount",
  perceptionScreenVisionClear: "perception.screenVision.clear",
  perceptionProactiveWakeSetEnabled: "perception.proactiveWake.setEnabled",
  perceptionProactiveWakeSetIntervalSec: "perception.proactiveWake.setIntervalSec",
  perceptionRunDiagnostics: "perception.runDiagnostics",
  abilitiesProviderConfigSave: "abilities.provider.config.save",
  abilitiesProviderHealthCheck: "abilities.provider.healthCheck",
  abilitiesProviderTtsTest: "abilities.provider.ttsTest",
  abilitiesProviderVoiceProfileInspectFolder: "abilities.provider.voiceProfile.inspectFolder",
  abilitiesProviderVoiceProfileSave: "abilities.provider.voiceProfile.save",
  abilitiesProviderVoiceProfileAssignToCurrentCharacter: "abilities.provider.voiceProfile.assignToCurrentCharacter",
  abilitiesProviderVoiceProfileClearCurrentCharacter: "abilities.provider.voiceProfile.clearCurrentCharacter",
  abilitiesMcpConfigSave: "abilities.mcp.config.save",
  abilitiesMcpDiscover: "abilities.mcp.discover",
  abilitiesMcpEnable: "abilities.mcp.enable",
  abilitiesMcpDisable: "abilities.mcp.disable",
  abilitiesMcpRestart: "abilities.mcp.restart",
  abilitiesMcpRemove: "abilities.mcp.remove",
  abilitiesPluginEnable: "abilities.plugin.enable",
  abilitiesPluginDisable: "abilities.plugin.disable",
  abilitiesPluginRollback: "abilities.plugin.rollback",
  abilitiesPluginStageSource: "abilities.plugin.stageSource",
  abilitiesPluginStageWheel: "abilities.plugin.stageWheel",
  abilitiesPluginStageMarket: "abilities.plugin.stageMarket",
  abilitiesPluginInstall: "abilities.plugin.install",
  abilitiesPluginDiscardStage: "abilities.plugin.discardStage",
  abilitiesPluginUninstall: "abilities.plugin.uninstall",
  abilitiesPluginConnectionSave: "abilities.plugin.connection.save",
  abilitiesPluginPickSource: "abilities.plugin.pickSource",
  abilitiesPluginPickWheel: "abilities.plugin.pickWheel",
  abilitiesApprovalPolicySave: "abilities.approvalPolicy.save",
  abilitiesApprovalRequestDecide: "abilities.approvalRequest.decide",
  abilitiesSkillsOpenFolder: "abilities.skills.openFolder",
  abilitiesWorkflowConfigSave: "abilities.workflow.config.save",
  abilitiesWorkflowFileImport: "abilities.workflow.file.import",
  abilitiesWorkflowValidate: "abilities.workflow.validate",
  abilitiesQqSelfCheck: "abilities.qq.selfCheck",
  qqSetupDetect: "qq.setup.detect",
  qqSetupSelect: "qq.setup.select",
  qqSetupStart: "qq.setup.start",
  qqSetupOpenLogin: "qq.setup.openLogin",
  qqSetupOpenFolder: "qq.setup.openFolder",
  advancedProbeClickThrough: "advanced.probeClickThrough",
  advancedResetWindow: "advanced.resetWindow",
  advancedToggleWebgl: "advanced.toggleWebgl",
  advancedSetHitTestEnabled: "advanced.setHitTestEnabled",
  advancedSetHitboxOverlay: "advanced.setHitboxOverlay"
});

export const CONTROL_CENTER_BRIDGED_ACTION_IDS = Object.freeze([
  CONTROL_CENTER_ACTIONS.settingsSelectBot,
  CONTROL_CENTER_ACTIONS.chatNew,
  CONTROL_CENTER_ACTIONS.chatSend,
  CONTROL_CENTER_ACTIONS.chatStop,
  CONTROL_CENTER_ACTIONS.chatJobControl,
  CONTROL_CENTER_ACTIONS.chatAttach,
  CONTROL_CENTER_ACTIONS.chatRemoveAttachment,
  CONTROL_CENTER_ACTIONS.chatPlayAttachment,
  CONTROL_CENTER_ACTIONS.chatFileAction,
  CONTROL_CENTER_ACTIONS.workspaceOpen,
  CONTROL_CENTER_ACTIONS.characterOpenWorkshop,
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
  ...Object.keys(SCREEN_OBSERVATION_COMMANDS),
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
  CONTROL_CENTER_ACTIONS.musicTogglePlayback,
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
  CONTROL_CENTER_ACTIONS.abilitiesMcpEnable,
  CONTROL_CENTER_ACTIONS.abilitiesMcpDisable,
  CONTROL_CENTER_ACTIONS.abilitiesMcpRestart,
  CONTROL_CENTER_ACTIONS.abilitiesMcpRemove,
  CONTROL_CENTER_ACTIONS.abilitiesPluginEnable,
  CONTROL_CENTER_ACTIONS.abilitiesPluginDisable,
  CONTROL_CENTER_ACTIONS.abilitiesPluginRollback,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageSource,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageWheel,
  CONTROL_CENTER_ACTIONS.abilitiesPluginStageMarket,
  CONTROL_CENTER_ACTIONS.abilitiesPluginInstall,
  CONTROL_CENTER_ACTIONS.abilitiesPluginDiscardStage,
  CONTROL_CENTER_ACTIONS.abilitiesPluginUninstall,
  CONTROL_CENTER_ACTIONS.abilitiesPluginConnectionSave,
  CONTROL_CENTER_ACTIONS.abilitiesPluginPickSource,
  CONTROL_CENTER_ACTIONS.abilitiesPluginPickWheel,
  CONTROL_CENTER_ACTIONS.abilitiesApprovalPolicySave,
  CONTROL_CENTER_ACTIONS.abilitiesApprovalRequestDecide,
  CONTROL_CENTER_ACTIONS.abilitiesSkillsOpenFolder,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowConfigSave,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowFileImport,
  CONTROL_CENTER_ACTIONS.abilitiesWorkflowValidate,
  CONTROL_CENTER_ACTIONS.abilitiesQqSelfCheck,
  CONTROL_CENTER_ACTIONS.qqSetupDetect,
  CONTROL_CENTER_ACTIONS.qqSetupSelect,
  CONTROL_CENTER_ACTIONS.qqSetupStart,
  CONTROL_CENTER_ACTIONS.qqSetupOpenLogin,
  CONTROL_CENTER_ACTIONS.qqSetupOpenFolder
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
  if (typeof dataSource.handlesAction === "function") {
    return Boolean(dataSource.handlesAction(actionId));
  }
  return isControlCenterBridgedAction(actionId);
}

function shouldReturnNotImplemented(dataSource, actionId) {
  return isControlCenterBridgedAction(actionId) || Boolean(dataSource);
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
