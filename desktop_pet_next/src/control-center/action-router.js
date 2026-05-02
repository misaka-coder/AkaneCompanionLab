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
  windowClose: "window.close"
});

export function createControlCenterActionRouter(options = {}) {
  const handlers = new Map();
  const dataSource = options.dataSource;
  const logger = options.logger || console;

  return {
    register(actionId, handler) {
      handlers.set(actionId, handler);
      return () => handlers.delete(actionId);
    },

    async run(actionId, payload = {}, context = {}) {
      if (!actionId) {
        return { ok: false, status: "missing-action-id" };
      }

      const handler = handlers.get(actionId);
      if (handler) {
        return handler(payload, context);
      }

      if (dataSource?.runAction) {
        return dataSource.runAction(actionId, payload, context);
      }

      logger.info?.("[control-center] action", actionId, payload, context);
      return { ok: true, status: "noop", actionId, payload };
    }
  };
}
