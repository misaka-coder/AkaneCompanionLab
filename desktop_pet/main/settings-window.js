const { BrowserWindow } = require("electron");
const path = require("path");

const SETTINGS_WIDTH = 420;
const SETTINGS_HEIGHT = 620;

let settingsWindow = null;

function createSettingsWindow({ settings }) {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.focus();
    settingsWindow.webContents.send("settings-init", settings || {});
    return settingsWindow;
  }

  const win = new BrowserWindow({
    width: SETTINGS_WIDTH,
    height: SETTINGS_HEIGHT,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: true,
    backgroundColor: "#00000000",
    focusable: true,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "settings.html"));

  win.webContents.on("did-finish-load", () => {
    win.webContents.send("settings-init", settings || {});
  });

  win.on("closed", () => {
    settingsWindow = null;
  });

  settingsWindow = win;
  return win;
}

function getSettingsWindow() {
  return settingsWindow && !settingsWindow.isDestroyed() ? settingsWindow : null;
}

function closeSettingsWindow() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.close();
    settingsWindow = null;
  }
}

function toggleSettingsWindow({ settings }) {
  const existing = getSettingsWindow();
  if (existing) {
    closeSettingsWindow();
    return null;
  }
  return createSettingsWindow({ settings });
}

module.exports = { createSettingsWindow, getSettingsWindow, closeSettingsWindow, toggleSettingsWindow };
