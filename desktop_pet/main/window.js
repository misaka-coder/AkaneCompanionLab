const { BrowserWindow, screen } = require("electron");
const path = require("path");
const { loadSettings, updateSettings } = require("./settings-store");

const DEFAULT_WIDTH = 360;
const DEFAULT_HEIGHT = 620;
const MIN_USABLE_WIDTH = 340;
const MIN_USABLE_HEIGHT = 600;

function createWindow() {
  const settings = loadSettings();
  const bounds = resolveWindowBounds(settings.windowBounds);

  const win = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true,
    minWidth: MIN_USABLE_WIDTH,
    minHeight: 460,
    hasShadow: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.setOpacity(settings.opacity);
  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  registerWindowBoundsPersistence(win);

  return win;
}

function resolveWindowBounds(savedBounds) {
  const normalized = normalizeSavedBounds(savedBounds);
  if (normalized && isWindowOnScreen(normalized)) {
    return fitBoundsToWorkArea(normalized);
  }
  return getDefaultWindowBounds();
}

function getDefaultWindowBounds() {
  const workArea = screen.getPrimaryDisplay().workArea;
  const width = Math.min(DEFAULT_WIDTH, workArea.width);
  const height = Math.min(DEFAULT_HEIGHT, workArea.height);
  return {
    width,
    height,
    x: workArea.x + workArea.width - width - 20,
    y: workArea.y + workArea.height - height - 30,
  };
}

function normalizeSavedBounds(bounds) {
  if (!bounds || typeof bounds !== "object") return null;
  const width = Math.max(Number(bounds.width) || 0, MIN_USABLE_WIDTH);
  const height = Math.max(Number(bounds.height) || 0, MIN_USABLE_HEIGHT);
  const x = Number.isFinite(Number(bounds.x)) ? Number(bounds.x) : undefined;
  const y = Number.isFinite(Number(bounds.y)) ? Number(bounds.y) : undefined;
  if (!x && x !== 0) return null;
  if (!y && y !== 0) return null;
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
  };
}

function fitBoundsToWorkArea(bounds) {
  const display = screen.getDisplayMatching(bounds);
  const area = display.workArea;
  const width = Math.min(bounds.width, area.width);
  const height = Math.min(bounds.height, area.height);
  return {
    width,
    height,
    x: Math.min(Math.max(bounds.x, area.x), area.x + area.width - width),
    y: Math.min(Math.max(bounds.y, area.y), area.y + area.height - height),
  };
}

function isWindowOnScreen(bounds) {
  return screen.getAllDisplays().some((display) => {
    const area = display.workArea;
    const visibleWidth =
      Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x);
    const visibleHeight =
      Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y);
    return visibleWidth >= 80 && visibleHeight >= 80;
  });
}

function registerWindowBoundsPersistence(win) {
  let saveTimer = 0;

  const saveBounds = () => {
    if (win.isDestroyed()) return;
    updateSettings({ windowBounds: win.getBounds() });
  };

  const scheduleSave = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveBounds, 250);
  };

  win.on("moved", scheduleSave);
  win.on("resized", scheduleSave);
  win.on("close", () => {
    clearTimeout(saveTimer);
    saveBounds();
  });
}

module.exports = { createWindow };
