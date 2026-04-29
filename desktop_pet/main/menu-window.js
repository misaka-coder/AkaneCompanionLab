const { BrowserWindow } = require("electron");
const path = require("path");

const MENU_WIDTH = 250;
const MENU_HEIGHT = 330;

let menuWindow = null;

function createMenuWindow({ x, y, settings }) {
  if (menuWindow && !menuWindow.isDestroyed()) {
    menuWindow.close();
  }

  const win = new BrowserWindow({
    width: MENU_WIDTH,
    height: MENU_HEIGHT,
    x: Math.round(x),
    y: Math.round(y),
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

  win.loadFile(path.join(__dirname, "..", "renderer", "menu.html"));

  win.webContents.on("did-finish-load", () => {
    win.webContents.send("menu-init", settings);
  });

  win.on("blur", () => {
    closeMenuWindow();
  });

  menuWindow = win;
  return win;
}

function closeMenuWindow() {
  if (menuWindow && !menuWindow.isDestroyed()) {
    menuWindow.close();
    menuWindow = null;
  }
}

function sendMenuAction(mainWindow, action, value) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("menu-action", { action, value });
  }
}

function getMenuWindow() {
  return menuWindow && !menuWindow.isDestroyed() ? menuWindow : null;
}

module.exports = { createMenuWindow, closeMenuWindow, sendMenuAction, getMenuWindow };
