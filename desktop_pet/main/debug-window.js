const { BrowserWindow } = require("electron");
const path = require("path");

const DEBUG_WIDTH = 390;
const DEBUG_HEIGHT = 560;

let debugWindow = null;

function createDebugWindow() {
  if (debugWindow && !debugWindow.isDestroyed()) {
    debugWindow.focus();
    return debugWindow;
  }

  const win = new BrowserWindow({
    width: DEBUG_WIDTH,
    height: DEBUG_HEIGHT,
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

  win.loadFile(path.join(__dirname, "..", "renderer", "debug.html"));

  win.webContents.on("did-finish-load", () => {
    win.webContents.send("debug-init");
  });

  win.on("closed", () => {
    debugWindow = null;
  });

  debugWindow = win;
  return win;
}

function getDebugWindow() {
  return debugWindow && !debugWindow.isDestroyed() ? debugWindow : null;
}

function closeDebugWindow() {
  if (debugWindow && !debugWindow.isDestroyed()) {
    debugWindow.close();
    debugWindow = null;
  }
}

function toggleDebugWindow() {
  const existing = getDebugWindow();
  if (existing) {
    closeDebugWindow();
    return null;
  }
  return createDebugWindow();
}

module.exports = { createDebugWindow, getDebugWindow, closeDebugWindow, toggleDebugWindow };
