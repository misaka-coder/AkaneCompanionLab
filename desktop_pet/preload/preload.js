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
  onWorkspacePanelToggle: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("workspace-panel-toggle", listener);
    return () => ipcRenderer.removeListener("workspace-panel-toggle", listener);
  },
  showContextMenu: () => ipcRenderer.invoke("show-context-menu"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  copyText: (value) => ipcRenderer.invoke("copy-text", value),
  moveWindow: (dx, dy) => ipcRenderer.invoke("move-window", dx, dy),
  minimizeWindow: () => ipcRenderer.invoke("minimize-window"),
  closeWindow: () => ipcRenderer.invoke("close-window"),
  platform: process.platform,
});
