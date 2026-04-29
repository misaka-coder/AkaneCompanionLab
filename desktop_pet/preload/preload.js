const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("akaneAPI", {
  getSettings: () => ipcRenderer.invoke("get-settings"),
  setSettings: (partial) => ipcRenderer.invoke("set-settings", partial),
  getDesktopContext: (options) => ipcRenderer.invoke("get-desktop-context", options || {}),
  onSettingsChanged: (callback) => {
    const listener = (_event, settings) => callback(settings);
    ipcRenderer.on("settings-changed", listener);
    return () => ipcRenderer.removeListener("settings-changed", listener);
  },
  onSettingsPrompt: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("settings-prompt", listener);
    return () => ipcRenderer.removeListener("settings-prompt", listener);
  },
  onReloadSprite: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("reload-sprite", listener);
    return () => ipcRenderer.removeListener("reload-sprite", listener);
  },
  onVoiceShortcutToggle: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("voice-shortcut-toggle", listener);
    return () => ipcRenderer.removeListener("voice-shortcut-toggle", listener);
  },
  notifyWorkspaceChanged: () => ipcRenderer.invoke("notify-workspace-changed"),
  onWorkspacePanelToggle: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("workspace-panel-toggle", listener);
    return () => ipcRenderer.removeListener("workspace-panel-toggle", listener);
  },
  toggleWorkspacePanel: (identity) => ipcRenderer.invoke("toggle-workspace-panel", identity || {}),
  toggleSettingsPanel: () => ipcRenderer.invoke("toggle-settings-panel"),
  onWorkspaceInit: (callback) => {
    const listener = (_event, settings) => callback(settings);
    ipcRenderer.on("workspace-init", listener);
    return () => ipcRenderer.removeListener("workspace-init", listener);
  },
  onWorkspaceChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("workspace-changed", listener);
    return () => ipcRenderer.removeListener("workspace-changed", listener);
  },
  publishWorkspaceActivityState: (activity) =>
    ipcRenderer.invoke("publish-workspace-activity-state", activity || null),
  requestWorkspaceActivityState: () => ipcRenderer.invoke("request-workspace-activity-state"),
  sendWorkspaceActivityAction: (action) => ipcRenderer.invoke("workspace-activity-action", action || {}),
  onWorkspaceActivityState: (callback) => {
    const listener = (_event, activity) => callback(activity || null);
    ipcRenderer.on("workspace-activity-state", listener);
    return () => ipcRenderer.removeListener("workspace-activity-state", listener);
  },
  onWorkspaceActivityStateRequest: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("workspace-activity-state-request", listener);
    return () => ipcRenderer.removeListener("workspace-activity-state-request", listener);
  },
  onWorkspaceActivityAction: (callback) => {
    const listener = (_event, action) => callback(action || {});
    ipcRenderer.on("workspace-activity-action", listener);
    return () => ipcRenderer.removeListener("workspace-activity-action", listener);
  },
  toggleDebugPanel: () => ipcRenderer.invoke("toggle-debug-panel"),
  publishDebugState: (state) => ipcRenderer.invoke("publish-debug-state", state || null),
  requestDebugState: () => ipcRenderer.invoke("request-debug-state"),
  sendDebugAction: (action) => ipcRenderer.invoke("debug-action", action || {}),
  onDebugPanelToggle: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("debug-panel-toggle", listener);
    return () => ipcRenderer.removeListener("debug-panel-toggle", listener);
  },
  onDebugInit: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("debug-init", listener);
    return () => ipcRenderer.removeListener("debug-init", listener);
  },
  onDebugState: (callback) => {
    const listener = (_event, state) => callback(state || null);
    ipcRenderer.on("debug-state", listener);
    return () => ipcRenderer.removeListener("debug-state", listener);
  },
  onDebugStateRequest: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("debug-state-request", listener);
    return () => ipcRenderer.removeListener("debug-state-request", listener);
  },
  onDebugAction: (callback) => {
    const listener = (_event, action) => callback(action || {});
    ipcRenderer.on("debug-action", listener);
    return () => ipcRenderer.removeListener("debug-action", listener);
  },
  onSettingsPanelToggle: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("settings-panel-toggle", listener);
    return () => ipcRenderer.removeListener("settings-panel-toggle", listener);
  },
  onSettingsInit: (callback) => {
    const listener = (_event, settings) => callback(settings || {});
    ipcRenderer.on("settings-init", listener);
    return () => ipcRenderer.removeListener("settings-init", listener);
  },
  openContextMenu: ({ screenX, screenY, settings }) =>
    ipcRenderer.invoke("open-context-menu", { screenX, screenY, settings }),
  menuAction: (action, value) =>
    ipcRenderer.invoke("menu-action", { action, value }),
  onMenuInit: (callback) => {
    const listener = (_event, settings) => callback(settings);
    ipcRenderer.on("menu-init", listener);
    return () => ipcRenderer.removeListener("menu-init", listener);
  },
  onMenuAction: (callback) => {
    const listener = (_event, { action, value }) => callback(action, value);
    ipcRenderer.on("menu-action", listener);
    return () => ipcRenderer.removeListener("menu-action", listener);
  },
  showContextMenu: () => ipcRenderer.invoke("show-context-menu"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  showItemInFolder: (filePath) => ipcRenderer.invoke("show-item-in-folder", filePath),
  copyText: (value) => ipcRenderer.invoke("copy-text", value),
  moveWindow: (dx, dy) => ipcRenderer.invoke("move-window", dx, dy),
  minimizeWindow: () => ipcRenderer.invoke("minimize-window"),
  closeWindow: () => ipcRenderer.invoke("close-window"),
  platform: process.platform,
});
