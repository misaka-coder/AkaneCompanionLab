const { app, BrowserWindow, globalShortcut } = require("electron");
const { createWindow, applyPetScaleBounds } = require("./window");
const { createTray } = require("./tray");
const { registerIpcHandlers } = require("./ipc-handlers");
const { loadSettings } = require("./settings-store");
const { attachDesktopContextTracker, refreshDesktopContextTracker } = require("./desktop-context");
const { getMenuWindow } = require("./menu-window");
const { getWorkspaceWindow } = require("./workspace-window");
const { getSettingsWindow } = require("./settings-window");

const VOICE_INPUT_SHORTCUT = "CommandOrControl+Shift+Space";

let mainWindow = null;
let tray = null;

app.whenReady().then(() => {
  mainWindow = createWindow();
  tray = createTray(mainWindow, { onSettingsChanged: notifySettingsChanged });
  registerIpcHandlers(mainWindow, {
    onSettingsChanged: notifySettingsChanged,
    onContextMenuRequested: showContextMenu,
  });
  attachDesktopContextTracker(mainWindow, { loadSettings });
  registerVoiceShortcut(loadSettings());
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    mainWindow = createWindow();
    tray = tray || createTray(mainWindow, { onSettingsChanged: notifySettingsChanged });
    registerIpcHandlers(mainWindow, {
      onSettingsChanged: notifySettingsChanged,
      onContextMenuRequested: showContextMenu,
    });
    attachDesktopContextTracker(mainWindow, { loadSettings });
    registerVoiceShortcut(loadSettings());
  }
});

function notifySettingsChanged(settings) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setOpacity(settings.opacity);
    applyPetScaleBounds(mainWindow, settings);
    mainWindow.webContents.send("settings-changed", settings);
  }
  const menuWin = getMenuWindow();
  if (menuWin) {
    menuWin.webContents.send("settings-changed", settings);
  }
  const workspaceWin = getWorkspaceWindow();
  if (workspaceWin) {
    workspaceWin.webContents.send("settings-changed", settings);
  }
  const settingsWin = getSettingsWindow();
  if (settingsWin) {
    settingsWin.webContents.send("settings-changed", settings);
  }
  if (tray && typeof tray.refreshMenu === "function") {
    tray.refreshMenu();
  }
  refreshDesktopContextTracker();
  registerVoiceShortcut(settings);
}

function showContextMenu() {
  if (tray && typeof tray.popupMenu === "function") {
    tray.popupMenu();
  }
}

function registerVoiceShortcut(settings) {
  globalShortcut.unregister(VOICE_INPUT_SHORTCUT);
  if (!settings?.voiceInputEnabled) return;
  globalShortcut.register(VOICE_INPUT_SHORTCUT, () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isFocused()) return;
    mainWindow.webContents.send("voice-shortcut-toggle");
  });
}
