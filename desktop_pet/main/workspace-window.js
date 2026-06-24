const { BrowserWindow } = require("electron");
const path = require("path");

const WORKSPACE_WIDTH = 370;
const WORKSPACE_HEIGHT = 520;

let workspaceWindow = null;

function createWorkspaceWindow({ settings }) {
  if (workspaceWindow && !workspaceWindow.isDestroyed()) {
    workspaceWindow.focus();
    return workspaceWindow;
  }

  const win = new BrowserWindow({
    width: WORKSPACE_WIDTH,
    height: WORKSPACE_HEIGHT,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: true,
    backgroundColor: "#00000000",
    focusable: false,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "workspace.html"));

  win.webContents.on("did-finish-load", () => {
    win.webContents.send("workspace-init", settings || {});
  });

  win.on("closed", () => {
    workspaceWindow = null;
  });

  workspaceWindow = win;
  return win;
}

function getWorkspaceWindow() {
  return workspaceWindow && !workspaceWindow.isDestroyed() ? workspaceWindow : null;
}

function closeWorkspaceWindow() {
  if (workspaceWindow && !workspaceWindow.isDestroyed()) {
    workspaceWindow.close();
    workspaceWindow = null;
  }
}

function toggleWorkspaceWindow({ settings }) {
  const existing = getWorkspaceWindow();
  if (existing) {
    closeWorkspaceWindow();
    return null;
  }
  return createWorkspaceWindow({ settings });
}

module.exports = { createWorkspaceWindow, getWorkspaceWindow, closeWorkspaceWindow, toggleWorkspaceWindow };
