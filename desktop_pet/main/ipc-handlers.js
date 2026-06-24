const { clipboard, ipcMain, shell } = require("electron");
const { loadSettings, updateSettings } = require("./settings-store");
const { applyPetScaleBounds } = require("./window");
const { collectDesktopContext } = require("./desktop-context");
const { createMenuWindow, closeMenuWindow, sendMenuAction } = require("./menu-window");
const { toggleWorkspaceWindow, getWorkspaceWindow } = require("./workspace-window");
const { toggleDebugWindow, getDebugWindow } = require("./debug-window");
const { toggleSettingsWindow } = require("./settings-window");

const IPC_CHANNELS = [
  "get-settings",
  "set-settings",
  "get-desktop-context",
  "move-window",
  "show-context-menu",
  "open-context-menu",
  "menu-action",
  "toggle-workspace-panel",
  "toggle-settings-panel",
  "notify-workspace-changed",
  "publish-workspace-activity-state",
  "request-workspace-activity-state",
  "workspace-activity-action",
  "toggle-debug-panel",
  "publish-debug-state",
  "request-debug-state",
  "debug-action",
  "open-external",
  "show-item-in-folder",
  "copy-text",
  "minimize-window",
  "close-window",
];

function registerIpcHandlers(mainWindow, { onSettingsChanged, onContextMenuRequested } = {}) {
  for (const channel of IPC_CHANNELS) {
    ipcMain.removeHandler(channel);
  }

  ipcMain.handle("get-settings", () => {
    return loadSettings();
  });

  ipcMain.handle("set-settings", (_event, partial) => {
    const settings = updateSettings(partial);
    applyWindowSettings(mainWindow, settings);
    if (onSettingsChanged) onSettingsChanged(settings);
    return settings;
  });

  ipcMain.handle("get-desktop-context", (_event, options) => {
    return collectDesktopContext(mainWindow, loadSettings(), options);
  });

  ipcMain.handle("move-window", (_event, dx, dy) => {
    const [x, y] = mainWindow.getPosition();
    mainWindow.setPosition(x + Math.round(dx), y + Math.round(dy));
    updateSettings({ windowBounds: mainWindow.getBounds() });
  });

  ipcMain.handle("open-context-menu", (_event, { screenX, screenY, settings }) => {
    createMenuWindow({
      x: screenX,
      y: screenY,
      settings: settings || loadSettings(),
    });
  });

  ipcMain.handle("menu-action", (_event, { action, value }) => {
    sendMenuAction(mainWindow, action, value);
  });

  ipcMain.handle("toggle-workspace-panel", (_event, identity) => {
    const settings = loadSettings();
    if (identity && typeof identity === "object") {
      settings.profileUserId = identity.profileUserId || "master";
      settings.sessionId = identity.sessionId || "";
    }
    toggleWorkspaceWindow({ settings });
  });

  ipcMain.handle("toggle-settings-panel", () => {
    toggleSettingsWindow({ settings: loadSettings() });
    return { ok: true };
  });

  ipcMain.handle("notify-workspace-changed", () => {
    const win = getWorkspaceWindow();
    if (win) win.webContents.send("workspace-changed");
  });

  ipcMain.handle("publish-workspace-activity-state", (_event, activity) => {
    const win = getWorkspaceWindow();
    if (win) win.webContents.send("workspace-activity-state", activity || null);
    return { ok: true };
  });

  ipcMain.handle("request-workspace-activity-state", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("workspace-activity-state-request");
    }
    return { ok: true };
  });

  ipcMain.handle("workspace-activity-action", (_event, action) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("workspace-activity-action", action || {});
      return { ok: true };
    }
    return { ok: false, error: "main_window_unavailable" };
  });

  ipcMain.handle("toggle-debug-panel", () => {
    toggleDebugWindow();
    return { ok: true };
  });

  ipcMain.handle("publish-debug-state", (_event, state) => {
    const win = getDebugWindow();
    if (win) win.webContents.send("debug-state", state || null);
    return { ok: true };
  });

  ipcMain.handle("request-debug-state", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("debug-state-request");
    }
    return { ok: true };
  });

  ipcMain.handle("debug-action", (_event, action) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("debug-action", action || {});
      return { ok: true };
    }
    return { ok: false, error: "main_window_unavailable" };
  });

  ipcMain.handle("show-context-menu", () => {
    if (onContextMenuRequested) onContextMenuRequested();
  });

  ipcMain.handle("open-external", async (_event, url) => {
    const target = String(url || "").trim();
    if (!target || !/^(https?:|file:)/i.test(target)) {
      return { ok: false, error: "invalid_url" };
    }
    await shell.openExternal(target);
    return { ok: true };
  });

  ipcMain.handle("show-item-in-folder", (_event, filePath) => {
    const target = String(filePath || "").trim();
    if (!target) return { ok: false, error: "empty_path" };
    shell.showItemInFolder(target);
    return { ok: true };
  });

  ipcMain.handle("copy-text", (_event, value) => {
    const text = String(value || "");
    if (!text) return { ok: false, error: "empty_text" };
    clipboard.writeText(text);
    return { ok: true };
  });

  ipcMain.handle("minimize-window", () => {
    mainWindow.minimize();
  });

  ipcMain.handle("close-window", () => {
    mainWindow.close();
  });
}

function applyWindowSettings(mainWindow, settings) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.setOpacity(settings.opacity);
  applyPetScaleBounds(mainWindow, settings);
}

module.exports = { registerIpcHandlers };
