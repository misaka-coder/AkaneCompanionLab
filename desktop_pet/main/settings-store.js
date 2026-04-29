const { app } = require("electron");
const fs = require("fs");
const path = require("path");

const DEFAULT_SETTINGS = {
  backendUrl: "http://127.0.0.1:9999",
  outfit: "猫娘",
  opacity: 1,
  petScale: 1,
  voiceEnabled: false,
  voiceInputEnabled: true,
  desktopContextEnabled: true,
  clipboardContextEnabled: false,
};

const OPACITY_VALUES = [1, 0.85, 0.7];
const PET_SCALE_VALUES = [0.85, 1, 1.15, 1.3];
const MIN_PET_SCALE = 0.75;
const MAX_PET_SCALE = 1.45;

function getSettingsPath() {
  return path.join(app.getPath("userData"), "akane-pet-settings.json");
}

function loadSettings() {
  try {
    const raw = fs.readFileSync(getSettingsPath(), "utf-8");
    return normalizeSettings(JSON.parse(raw));
  } catch {
    return normalizeSettings({});
  }
}

function saveSettings(settings) {
  const normalized = normalizeSettings(settings);
  fs.mkdirSync(path.dirname(getSettingsPath()), { recursive: true });
  fs.writeFileSync(getSettingsPath(), JSON.stringify(normalized, null, 2), "utf-8");
  return normalized;
}

function updateSettings(partial) {
  return saveSettings({
    ...loadSettings(),
    ...(partial && typeof partial === "object" ? partial : {}),
  });
}

function normalizeSettings(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const settings = { ...DEFAULT_SETTINGS };

  const backendUrl = String(source.backendUrl || "").trim().replace(/\/+$/, "");
  if (backendUrl) settings.backendUrl = backendUrl;

  const outfit = normalizeOutfit(source.outfit);
  if (outfit) settings.outfit = outfit;

  const opacity = Number(source.opacity);
  if (OPACITY_VALUES.includes(opacity)) {
    settings.opacity = opacity;
  }

  settings.petScale = normalizePetScale(source.petScale);

  if (typeof source.voiceEnabled === "boolean") {
    settings.voiceEnabled = source.voiceEnabled;
  }
  if (typeof source.voiceInputEnabled === "boolean") {
    settings.voiceInputEnabled = source.voiceInputEnabled;
  }
  if (typeof source.desktopContextEnabled === "boolean") {
    settings.desktopContextEnabled = source.desktopContextEnabled;
  }
  if (typeof source.clipboardContextEnabled === "boolean") {
    settings.clipboardContextEnabled = source.clipboardContextEnabled;
  }

  const bounds = normalizeBounds(source.windowBounds);
  if (bounds) settings.windowBounds = bounds;

  return settings;
}

function normalizePetScale(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_SETTINGS.petScale;
  const clamped = Math.max(MIN_PET_SCALE, Math.min(MAX_PET_SCALE, numeric));
  return Math.round(clamped * 100) / 100;
}

function normalizeOutfit(value) {
  const outfit = String(value || "").trim();
  if (!outfit) return "";
  if (["水手服", "睡衣"].includes(outfit)) return DEFAULT_SETTINGS.outfit;
  return outfit;
}

function normalizeBounds(value) {
  if (!value || typeof value !== "object") return null;

  const x = Math.round(Number(value.x));
  const y = Math.round(Number(value.y));
  const width = Math.round(Number(value.width));
  const height = Math.round(Number(value.height));

  if (![x, y, width, height].every(Number.isFinite)) return null;
  if (width < 180 || height < 300) return null;

  return { x, y, width, height };
}

module.exports = {
  DEFAULT_SETTINGS,
  OPACITY_VALUES,
  PET_SCALE_VALUES,
  normalizePetScale,
  loadSettings,
  saveSettings,
  updateSettings,
};
