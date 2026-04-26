const { ipcMain } = require("electron");
const { loadSettings, updateSettings } = require("./settings-store");
const { collectDesktopContext } = require("./desktop-context");

const IPC_CHANNELS = [
  "get-settings",
  "set-settings",
  "get-desktop-context",
  "move-window",
  "show-context-menu",
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

  ipcMain.handle("show-context-menu", () => {
    if (onContextMenuRequested) onContextMenuRequested();
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
}

module.exports = { registerIpcHandlers };
