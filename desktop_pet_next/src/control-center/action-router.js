export const CONTROL_CENTER_ACTIONS = Object.freeze({
  chatNew: "chat.new",
  chatStop: "chat.stop",
  workspaceOpen: "workspace.open",
  characterImportZip: "character.importZip",
  characterOpenPackFolder: "character.openPackFolder",
  characterApply: "character.apply",
  characterRefresh: "character.refresh",
  characterRestoreDefaults: "character.restoreDefaults",
  voiceTest: "voice.test",
  voiceStop: "voice.stop",
  musicPrevious: "music.previous",
  musicNext: "music.next",
  musicPause: "music.pause",
  musicStop: "music.stop",
  musicClear: "music.clear",
  windowNotify: "window.notify",
  windowMinimize: "window.minimize",
  windowMaximize: "window.maximize",
  windowClose: "window.close",
  perceptionDesktopContextSetEnabled: "perception.desktopContext.setEnabled",
  perceptionClipboardContextSetEnabled: "perception.clipboardContext.setEnabled",
  perceptionScreenVisionSetEnabled: "perception.screenVision.setEnabled",
  perceptionProactiveWakeSetEnabled: "perception.proactiveWake.setEnabled",
  perceptionProactiveWakeSetIntervalSec: "perception.proactiveWake.setIntervalSec"
});

export const CONTROL_CENTER_BRIDGED_ACTION_IDS = Object.freeze([
  CONTROL_CENTER_ACTIONS.chatNew,
  CONTROL_CENTER_ACTIONS.chatStop,
  CONTROL_CENTER_ACTIONS.workspaceOpen,
  CONTROL_CENTER_ACTIONS.voiceTest,
  CONTROL_CENTER_ACTIONS.voiceStop,
  CONTROL_CENTER_ACTIONS.characterOpenPackFolder,
  CONTROL_CENTER_ACTIONS.characterRefresh,
  CONTROL_CENTER_ACTIONS.perceptionDesktopContextSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionClipboardContextSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionScreenVisionSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetEnabled,
  CONTROL_CENTER_ACTIONS.perceptionProactiveWakeSetIntervalSec,
  CONTROL_CENTER_ACTIONS.windowClose,
  CONTROL_CENTER_ACTIONS.windowMinimize,
  CONTROL_CENTER_ACTIONS.windowMaximize,
  CONTROL_CENTER_ACTIONS.musicPrevious,
  CONTROL_CENTER_ACTIONS.musicNext,
  CONTROL_CENTER_ACTIONS.musicPause,
  CONTROL_CENTER_ACTIONS.musicStop,
  CONTROL_CENTER_ACTIONS.musicClear
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
